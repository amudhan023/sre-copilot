"""
PostgreSQL storage and retrieval primitives for the SRE Copilot RAG store.

This module owns the database-facing operations for RAG chunks: schema
creation, idempotent chunk upserts, dense pgvector search, and PostgreSQL
full-text search. The higher-level retriever is responsible for combining
these ranked lists with RRF and later applying cross-encoder reranking.

Important implementation details:
- Dense embeddings are VECTOR(1024) and searched with cosine distance.
- ``search_vector`` is a PostgreSQL TSVECTOR backed by a GIN index.
- Every retrieval query requires a tenant filter; do not bypass it.
- ``chunk_id`` is unique so ingestion can safely upsert the same chunk.
"""

import os
from collections.abc import Iterable

import psycopg
from pgvector.psycopg import register_vector


DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "dbname=sre_copilot user=amudhan023 port=5433",
)


def get_connection():
    connection = psycopg.connect(DATABASE_URL)
    register_vector(connection)
    return connection


def create_table():
    """Create the RAG table and indexes if they do not already exist."""
    with get_connection() as connection:
        connection.execute(
            """
            CREATE EXTENSION IF NOT EXISTS vector;

            CREATE TABLE IF NOT EXISTS rag_chunks (
                id BIGSERIAL PRIMARY KEY,
                tenant TEXT NOT NULL,
                document_id TEXT NOT NULL,
                chunk_id TEXT NOT NULL UNIQUE,
                chunk_index INTEGER NOT NULL,

                service TEXT NOT NULL,
                document_type TEXT NOT NULL,

                title TEXT,
                content TEXT NOT NULL,

                embedding VECTOR(1024),

                search_vector TSVECTOR
            );
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS rag_chunks_embedding_idx
            ON rag_chunks
            USING hnsw (embedding vector_cosine_ops);
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS rag_chunks_search_vector_idx
            ON rag_chunks
            USING gin (search_vector);
            """
        )

        connection.commit()


def insert_chunk(chunk: dict, embedding, connection=None) -> None:
    """Insert or update one chunk without creating duplicates.

    ``chunk`` must contain the tenant, document/chunk identifiers, metadata,
    and content fields used by the schema. The embedding must be a 1024-
    dimensional BGE-M3 dense vector.
    """
    owns_connection = connection is None
    if owns_connection:
        connection = get_connection()

    try:
        connection.execute(
            """
            INSERT INTO rag_chunks (
                tenant,
                document_id,
                chunk_id,
                chunk_index,
                service,
                document_type,
                title,
                content,
                embedding,
                search_vector
            )
            VALUES (
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s,
                to_tsvector('english', coalesce(%s, '') || ' ' || %s)
            )
            ON CONFLICT (chunk_id)
            DO UPDATE SET
                tenant = EXCLUDED.tenant,
                document_id = EXCLUDED.document_id,
                chunk_index = EXCLUDED.chunk_index,
                service = EXCLUDED.service,
                document_type = EXCLUDED.document_type,
                title = EXCLUDED.title,
                content = EXCLUDED.content,
                embedding = EXCLUDED.embedding,
                search_vector = EXCLUDED.search_vector
            """,
            (
                chunk["tenant"],
                chunk["document_id"],
                chunk["chunk_id"],
                chunk["chunk_index"],
                chunk["service"],
                chunk["document_type"],
                chunk.get("title"),
                chunk["content"],
                embedding,
                chunk.get("title"),
                chunk["content"],
            ),
        )
        if owns_connection:
            connection.commit()
    finally:
        if owns_connection:
            connection.close()


def insert_chunks(chunks: Iterable[tuple[dict, object]]) -> None:
    """Idempotently upsert multiple chunks in one database transaction."""
    with get_connection() as connection:
        for chunk, embedding in chunks:
            insert_chunk(chunk, embedding, connection=connection)
        connection.commit()


def _base_result_columns() -> str:
    return """
        chunk_id,
        document_id,
        tenant,
        service,
        document_type,
        title,
        content
    """


def dense_search(
    query_embedding,
    tenant: str,
    limit: int = 20,
) -> list[dict]:
    """Return the nearest chunks for a tenant using pgvector cosine distance."""
    if not tenant.strip():
        raise ValueError("tenant must not be empty")
    if limit <= 0:
        raise ValueError("limit must be greater than zero")

    with get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT
                {_base_result_columns()},
                1 - (embedding <=> %s) AS score
            FROM rag_chunks
            WHERE tenant = %s
              AND embedding IS NOT NULL
            ORDER BY embedding <=> %s
            LIMIT %s
            """,
            (query_embedding, tenant, query_embedding, limit),
        ).fetchall()

    columns = [
        "chunk_id",
        "document_id",
        "tenant",
        "service",
        "document_type",
        "title",
        "content",
        "score",
    ]
    return [dict(zip(columns, row)) for row in rows]


def sparse_search(
    query: str,
    tenant: str,
    limit: int = 20,
) -> list[dict]:
    """Return keyword-ranked chunks for a tenant using PostgreSQL FTS."""
    if not query.strip():
        raise ValueError("query must not be empty")
    if not tenant.strip():
        raise ValueError("tenant must not be empty")
    if limit <= 0:
        raise ValueError("limit must be greater than zero")

    with get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT
                {_base_result_columns()},
                ts_rank_cd(
                    search_vector,
                    websearch_to_tsquery('english', %s)
                ) AS score
            FROM rag_chunks
            WHERE tenant = %s
              AND search_vector @@ websearch_to_tsquery('english', %s)
            ORDER BY score DESC
            LIMIT %s
            """,
            (query, tenant, query, limit),
        ).fetchall()

    columns = [
        "chunk_id",
        "document_id",
        "tenant",
        "service",
        "document_type",
        "title",
        "content",
        "score",
    ]
    return [dict(zip(columns, row)) for row in rows]

import os

import psycopg
from pgvector.psycopg import register_vector


DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "dbname=sre_copilot user=amudhan023 port=5433",
)

# Sets up the Postgres side of the RAG store: a connection helper (using
# pgvector so vector columns come back as numpy arrays instead of raw
# strings) and the rag_chunks table itself. The table carries both a
# VECTOR(1024) column for dense embeddings with an HNSW index (a fast
# approximate-nearest-neighbor index for cosine similarity) and a TSVECTOR
# column for keyword search with a GIN index - one table, two search paths.


def get_connection():
    connection = psycopg.connect(DATABASE_URL)
    register_vector(connection)
    return connection


def create_table():
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
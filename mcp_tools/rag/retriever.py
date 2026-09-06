from mcp_tools.rag.embeddings import BGE3Embeddings
from mcp_tools.rag.store import get_connection

"""
Hybrid retrieval for the SRE Copilot RAG system.

Purpose:
    Retrieves relevant historical incident chunks using two complementary
    search strategies:
      1. Dense semantic search using BGE-M3 embeddings + pgvector.
      2. Sparse lexical search using PostgreSQL full-text search.

High-level flow:
    Query
      -> Dense retrieval
      -> Sparse retrieval
      -> Reciprocal Rank Fusion (RRF)
      -> Top candidate chunks

Important:
    The sparse search currently uses PostgreSQL tsvector/FTS. It is NOT
    BGE-M3's learned sparse lexical weights.

    RRF combines rankings rather than directly comparing dense and sparse
    scores, because the two search systems produce different score scales.

    This file only retrieves candidates. Cross-encoder reranking happens
    separately in reranker.py.

Future:
    The retriever can later be extended with additional filters such as
    document type, environment, time range, and tenant.
"""

# imports...

class HybridRetriever:
    def __init__(self):
        self.embedder = BGE3Embeddings()

    def dense_search(
        self,
        query: str,
        service: str | None = None,
        limit: int = 20,
    ):
        embedding = self.embedder.encode([query])["dense"][0]

        sql = """
            SELECT
                id,
                document_id,
                chunk_id,
                chunk_index,
                service,
                document_type,
                title,
                content,
                1 - (embedding <=> %s) AS score
            FROM rag_chunks
            WHERE embedding IS NOT NULL
        """

        params = [embedding]

        if service:
            sql += " AND service = %s"
            params.append(service)

        sql += """
            ORDER BY embedding <=> %s
            LIMIT %s
        """

        params.extend([embedding, limit])

        with get_connection() as connection:
            rows = connection.execute(sql, params).fetchall()

        return [
            {
                "id": row[0],
                "document_id": row[1],
                "chunk_id": row[2],
                "chunk_index": row[3],
                "service": row[4],
                "document_type": row[5],
                "title": row[6],
                "content": row[7],
                "score": float(row[8]),
            }
            for row in rows
        ]

    def sparse_search(
        self,
        query: str,
        service: str | None = None,
        limit: int = 20,
    ):
        sql = """
            SELECT
                id,
                document_id,
                chunk_id,
                chunk_index,
                service,
                document_type,
                title,
                content,
                ts_rank_cd(
                    search_vector,
                    websearch_to_tsquery('english', %s)
                ) AS score
            FROM rag_chunks
            WHERE search_vector @@
                websearch_to_tsquery('english', %s)
        """

        params = [query, query]

        if service:
            sql += " AND service = %s"
            params.append(service)

        sql += """
            ORDER BY score DESC
            LIMIT %s
        """

        params.append(limit)

        with get_connection() as connection:
            rows = connection.execute(sql, params).fetchall()

        return [
            {
                "id": row[0],
                "document_id": row[1],
                "chunk_id": row[2],
                "chunk_index": row[3],
                "service": row[4],
                "document_type": row[5],
                "title": row[6],
                "content": row[7],
                "score": float(row[8]),
            }
            for row in rows
        ]

    def hybrid_search(
        self,
        query: str,
        service: str | None = None,
        limit: int = 5,
        candidate_limit: int = 20,
    ):
        dense = self.dense_search(
            query,
            service=service,
            limit=candidate_limit,
        )

        sparse = self.sparse_search(
            query,
            service=service,
            limit=candidate_limit,
        )

        fused = {}

        rrf_k = 60

        for rank, result in enumerate(dense, start=1):
            chunk_id = result["chunk_id"]

            fused.setdefault(
                chunk_id,
                {
                    **result,
                    "dense_rank": rank,
                    "sparse_rank": None,
                    "rrf_score": 0.0,
                },
            )

            fused[chunk_id]["rrf_score"] += (
                1 / (rrf_k + rank)
            )

        for rank, result in enumerate(sparse, start=1):
            chunk_id = result["chunk_id"]

            if chunk_id not in fused:
                fused[chunk_id] = {
                    **result,
                    "dense_rank": None,
                    "sparse_rank": rank,
                    "rrf_score": 0.0,
                }

            fused[chunk_id]["sparse_rank"] = rank

            fused[chunk_id]["rrf_score"] += (
                1 / (rrf_k + rank)
            )

        results = sorted(
            fused.values(),
            key=lambda item: item["rrf_score"],
            reverse=True,
        )

        return results[:limit]
"""
Hybrid retrieval for the SRE Copilot knowledge base.

This module combines PostgreSQL/pgvector dense retrieval with PostgreSQL
full-text sparse retrieval, merges their rankings with Reciprocal Rank Fusion
(RRF), and optionally reranks the RRF candidates with a BGE cross-encoder.

Pipeline:
    Query
      -> BGE-M3 dense embedding
      -> pgvector dense search + PostgreSQL FTS sparse search
      -> RRF candidate set
      -> BGE Reranker v2-M3
      -> final Top-K

Important implementation details:
- Dense retrieval uses BGE-M3 embeddings and pgvector cosine distance.
- Sparse retrieval uses the indexed PostgreSQL TSVECTOR column.
- Every database retrieval path requires a tenant filter.
- RRF combines rankings, not raw dense/sparse scores.
- The cross-encoder only sees the RRF candidate set; it never searches the
  database directly.
- ``hybrid_search`` remains RRF-only for callers that need the intermediate
  candidate set.
- ``retrieve`` is the complete retrieval pipeline and returns the final
  reranked results.
"""

from collections.abc import Iterable

from mcp_tools.rag.embeddings import BGE3Embeddings
from mcp_tools.rag.reranker import BGEReranker
from mcp_tools.rag.store import dense_search, sparse_search


DEFAULT_RRF_K = 60


def reciprocal_rank_fusion(
    ranked_lists: Iterable[list[dict]],
    k: int = DEFAULT_RRF_K,
) -> list[dict]:
    """Fuse ranked results by stable chunk_id using Reciprocal Rank Fusion."""
    if k <= 0:
        raise ValueError("k must be greater than zero")

    candidates: dict[str, dict] = {}

    for ranked_list in ranked_lists:
        for rank, result in enumerate(ranked_list, start=1):
            chunk_id = result["chunk_id"]
            candidate = candidates.setdefault(
                chunk_id,
                {**result, "rrf_score": 0.0},
            )
            candidate["rrf_score"] += 1.0 / (k + rank)

            if "dense_rank" in result:
                candidate["dense_rank"] = rank
            if "sparse_rank" in result:
                candidate["sparse_rank"] = rank

    return sorted(
        candidates.values(),
        key=lambda result: result["rrf_score"],
        reverse=True,
    )


class HybridRetriever:
    """Run dense + sparse retrieval, RRF, and cross-encoder reranking."""

    def __init__(
        self,
        embedder: BGE3Embeddings | None = None,
        reranker: BGEReranker | None = None,
    ):
        self.embedder = embedder or BGE3Embeddings()
        self.reranker = reranker or BGEReranker()

    def dense_search(
        self,
        query: str,
        tenant: str,
        limit: int = 20,
    ) -> list[dict]:
        """Embed a query and execute tenant-scoped dense retrieval."""
        embedding = self.embedder.encode([query])["dense"][0]
        results = dense_search(
            query_embedding=embedding,
            tenant=tenant,
            limit=limit,
        )
        return [
            {**result, "dense_rank": rank}
            for rank, result in enumerate(results, start=1)
        ]

    def sparse_search(
        self,
        query: str,
        tenant: str,
        limit: int = 20,
    ) -> list[dict]:
        """Execute tenant-scoped PostgreSQL full-text retrieval."""
        results = sparse_search(
            query=query,
            tenant=tenant,
            limit=limit,
        )
        return [
            {**result, "sparse_rank": rank}
            for rank, result in enumerate(results, start=1)
        ]

    def hybrid_search(
        self,
        query: str,
        tenant: str,
        limit: int = 20,
        candidate_limit: int = 20,
    ) -> list[dict]:
        """Return the top RRF candidates from dense and sparse retrieval."""
        dense = self.dense_search(
            query,
            tenant=tenant,
            limit=candidate_limit,
        )
        sparse = self.sparse_search(
            query,
            tenant=tenant,
            limit=candidate_limit,
        )
        return reciprocal_rank_fusion([dense, sparse])[:limit]

    def retrieve(
        self,
        query: str,
        tenant: str,
        dense_limit: int = 20,
        sparse_limit: int = 20,
        rrf_limit: int = 20,
        top_k: int = 5,
    ) -> dict:
        """Run the complete retrieval pipeline and return final Top-K results."""
        if top_k <= 0:
            raise ValueError("top_k must be greater than zero")
        if rrf_limit <= 0:
            raise ValueError("rrf_limit must be greater than zero")

        dense = self.dense_search(
            query,
            tenant=tenant,
            limit=dense_limit,
        )
        sparse = self.sparse_search(
            query,
            tenant=tenant,
            limit=sparse_limit,
        )
        rrf = reciprocal_rank_fusion([dense, sparse])[:rrf_limit]
        reranked = self.reranker.rerank(
            query,
            rrf,
            top_k=top_k,
        )

        return {
            "query": query,
            "tenant": tenant,
            "dense": dense,
            "sparse": sparse,
            "rrf": rrf,
            "results": reranked,
        }

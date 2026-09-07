from FlagEmbedding import FlagReranker

"""
Cross-encoder reranking for retrieved SRE incident candidates.

Purpose:
    Takes the broader candidate set produced by hybrid retrieval and
    re-scores each query/chunk pair using BGE Reranker v2-M3.

High-level flow:
    Hybrid retrieval
        -> Top ~20 candidates
        -> Cross-encoder reranker
        -> Top 5 relevant chunks

Important:
    Retrieval is optimized for recall.
    Reranking is optimized for relevance.

    We intentionally retrieve more candidates than we finally return.
    This gives the reranker enough candidates to choose from.

This file does not perform database retrieval itself.
"""

class BGEReranker:
    def __init__(self):
        self.model = FlagReranker(
            "BAAI/bge-reranker-v2-m3",
            use_fp16=False,
        )

    def rerank(
        self,
        query: str,
        results: list[dict],
        top_k: int = 5,
    ) -> list[dict]:
        if not results:
            return []

        pairs = [
            [query, result["content"]]
            for result in results
        ]

        scores = self.model.compute_score(
            pairs,
            normalize=True,
        )

        if not isinstance(scores, list):
            scores = [scores]

        reranked = []

        for result, score in zip(results, scores):
            reranked.append(
                {
                    **result,
                    "rerank_score": float(score),
                }
            )

        reranked.sort(
            key=lambda item: item["rerank_score"],
            reverse=True,
        )

        return reranked[:top_k]
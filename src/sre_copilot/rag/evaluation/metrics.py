"""Retrieval metrics kept alongside the Ragas evaluation harness."""

from __future__ import annotations

import math


def recall_at_k(retrieved_ids: list[str], relevant_ids: set[str], k: int) -> float:
    """Fraction of relevant documents present in the first k results."""
    if not relevant_ids:
        return 0.0
    retrieved = set(retrieved_ids[:k])
    return len(retrieved & relevant_ids) / len(relevant_ids)


def precision_at_k(retrieved_ids: list[str], relevant_ids: set[str], k: int) -> float:
    """Fraction of the first k results that are relevant."""
    if k <= 0:
        raise ValueError("k must be greater than zero")
    top_k = retrieved_ids[:k]
    if not top_k:
        return 0.0
    return sum(document_id in relevant_ids for document_id in top_k) / len(top_k)


def reciprocal_rank(retrieved_ids: list[str], relevant_ids: set[str]) -> float:
    """Return reciprocal rank of the first relevant result."""
    for rank, document_id in enumerate(retrieved_ids, start=1):
        if document_id in relevant_ids:
            return 1.0 / rank
    return 0.0


def ndcg_at_k(retrieved_ids: list[str], relevant_ids: set[str], k: int) -> float:
    """Binary-relevance NDCG@K for document-level retrieval."""
    if k <= 0:
        raise ValueError("k must be greater than zero")
    top_k = retrieved_ids[:k]
    dcg = sum(
        (1.0 / math.log2(rank + 1))
        for rank, document_id in enumerate(top_k, start=1)
        if document_id in relevant_ids
    )
    ideal_hits = min(len(relevant_ids), k)
    idcg = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_hits + 1))
    return dcg / idcg if idcg else 0.0


def retrieval_metrics(
    retrieved_ids: list[str],
    relevant_ids: list[str] | tuple[str, ...],
    k: int = 5,
) -> dict[str, float]:
    """Calculate the ranking metrics that Ragas does not replace."""
    relevant = set(relevant_ids)
    return {
        f"recall@{k}": recall_at_k(retrieved_ids, relevant, k),
        f"precision@{k}": precision_at_k(retrieved_ids, relevant, k),
        "mrr": reciprocal_rank(retrieved_ids, relevant),
        f"ndcg@{k}": ndcg_at_k(retrieved_ids, relevant, k),
    }

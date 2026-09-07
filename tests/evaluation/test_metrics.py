from sre_copilot.rag.evaluation.metrics import (
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
    retrieval_metrics,
)


def test_retrieval_metrics_perfect_ranking():
    retrieved = ["INC-1042", "INC-0987", "INC-0001"]
    relevant = ["INC-1042", "INC-0987"]

    scores = retrieval_metrics(retrieved, relevant, k=2)

    assert scores["recall@2"] == 1.0
    assert scores["precision@2"] == 1.0
    assert scores["mrr"] == 1.0
    assert scores["ndcg@2"] == 1.0


def test_retrieval_metrics_ranked_hit():
    retrieved = ["INC-0001", "INC-1042", "INC-0002"]
    relevant = {"INC-1042"}

    assert recall_at_k(retrieved, relevant, 2) == 1.0
    assert precision_at_k(retrieved, relevant, 2) == 0.5
    assert reciprocal_rank(retrieved, relevant) == 0.5
    assert ndcg_at_k(retrieved, relevant, 2) > 0.0

import math

import pytest

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


def test_empty_retrieval_returns_zero_scores():
    retrieved = []
    relevant = {"INC-1042"}

    assert recall_at_k(retrieved, relevant, 5) == 0.0
    assert precision_at_k(retrieved, relevant, 5) == 0.0
    assert reciprocal_rank(retrieved, relevant) == 0.0
    assert ndcg_at_k(retrieved, relevant, 5) == 0.0


def test_no_relevant_documents_returns_zero_recall_and_ndcg():
    retrieved = ["INC-0001", "INC-0002"]
    relevant = set()

    assert recall_at_k(retrieved, relevant, 2) == 0.0
    assert precision_at_k(retrieved, relevant, 2) == 0.0
    assert reciprocal_rank(retrieved, relevant) == 0.0
    assert ndcg_at_k(retrieved, relevant, 2) == 0.0


def test_multiple_relevant_documents_partial_retrieval():
    retrieved = ["INC-0001", "INC-1042", "INC-0002", "INC-0987"]
    relevant = {"INC-1042", "INC-0987"}

    assert recall_at_k(retrieved, relevant, 3) == 0.5
    assert precision_at_k(retrieved, relevant, 3) == pytest.approx(1 / 3)
    assert reciprocal_rank(retrieved, relevant) == 0.5

    expected_dcg = 1.0 / math.log2(3)
    expected_idcg = 1.0 + 1.0 / math.log2(3)
    assert ndcg_at_k(retrieved, relevant, 3) == pytest.approx(
        expected_dcg / expected_idcg
    )


def test_better_ranking_improves_mrr_and_ndcg():
    relevant = {"INC-1042", "INC-0987"}

    best = ["INC-1042", "INC-0987", "INC-0001"]
    worse = ["INC-0001", "INC-1042", "INC-0987"]

    assert reciprocal_rank(best, relevant) > reciprocal_rank(worse, relevant)
    assert ndcg_at_k(best, relevant, 3) > ndcg_at_k(worse, relevant, 3)


def test_retrieval_metrics_reject_non_positive_k():
    retrieved = ["INC-1042"]
    relevant = ["INC-1042"]

    with pytest.raises(ValueError, match="k must be greater than zero"):
        precision_at_k(retrieved, relevant, 0)

    with pytest.raises(ValueError, match="k must be greater than zero"):
        ndcg_at_k(retrieved, relevant, 0)

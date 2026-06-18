"""Unit tests for the confidence scoring formula from DESIGN.md section 10.2."""
import pytest
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from agents.investigation.src.main import score_hypothesis


class TestScoreHypothesis:
    """Tests directly verify the formula documented in DESIGN.md."""

    def test_base_score_with_no_signals(self):
        score = score_hypothesis(
            hypothesis="test",
            evidence=[],
            similar_incidents=[],
            deployment_correlated=False,
            anomaly_type="LATENCY_SPIKE",
            has_runbook=False,
            competing_hypothesis_count=1,
        )
        # Base 0.50 - 0.15 (no similar incidents) = 0.35
        assert score == pytest.approx(0.35, abs=0.01)

    def test_similar_incidents_boost(self):
        score_none = score_hypothesis(
            hypothesis="test", evidence=[], similar_incidents=[],
            deployment_correlated=False, anomaly_type="X",
            has_runbook=False, competing_hypothesis_count=1,
        )
        score_with = score_hypothesis(
            hypothesis="test", evidence=[], similar_incidents=["INC-001"],
            deployment_correlated=False, anomaly_type="X",
            has_runbook=False, competing_hypothesis_count=1,
        )
        # +0.15 for having similar incidents, also -0.15 removed
        assert score_with > score_none
        assert score_with - score_none == pytest.approx(0.30, abs=0.01)

    def test_three_similar_incidents_extra_boost(self):
        score_one = score_hypothesis(
            hypothesis="test", evidence=[], similar_incidents=["INC-001"],
            deployment_correlated=False, anomaly_type="X",
            has_runbook=False, competing_hypothesis_count=1,
        )
        score_three = score_hypothesis(
            hypothesis="test", evidence=[], similar_incidents=["INC-001", "INC-002", "INC-003"],
            deployment_correlated=False, anomaly_type="X",
            has_runbook=False, competing_hypothesis_count=1,
        )
        # +0.10 extra for ≥3 similar incidents
        assert score_three - score_one == pytest.approx(0.10, abs=0.01)

    def test_deployment_correlation_boost(self):
        score_no_deploy = score_hypothesis(
            hypothesis="test", evidence=["ev1"],
            similar_incidents=["INC-001"],
            deployment_correlated=False, anomaly_type="LATENCY_SPIKE",
            has_runbook=False, competing_hypothesis_count=1,
        )
        score_deploy = score_hypothesis(
            hypothesis="test", evidence=["ev1"],
            similar_incidents=["INC-001"],
            deployment_correlated=True, anomaly_type="LATENCY_SPIKE",
            has_runbook=False, competing_hypothesis_count=1,
        )
        assert score_deploy - score_no_deploy == pytest.approx(0.20, abs=0.01)

    def test_runbook_match_boost(self):
        score_no_runbook = score_hypothesis(
            hypothesis="test", evidence=[], similar_incidents=["INC-001"],
            deployment_correlated=False, anomaly_type="X",
            has_runbook=False, competing_hypothesis_count=1,
        )
        score_runbook = score_hypothesis(
            hypothesis="test", evidence=[], similar_incidents=["INC-001"],
            deployment_correlated=False, anomaly_type="X",
            has_runbook=True, competing_hypothesis_count=1,
        )
        assert score_runbook - score_no_runbook == pytest.approx(0.05, abs=0.01)

    def test_competing_hypotheses_penalty(self):
        score_one = score_hypothesis(
            hypothesis="test", evidence=[], similar_incidents=["INC-001"],
            deployment_correlated=False, anomaly_type="X",
            has_runbook=False, competing_hypothesis_count=1,
        )
        score_many = score_hypothesis(
            hypothesis="test", evidence=[], similar_incidents=["INC-001"],
            deployment_correlated=False, anomaly_type="X",
            has_runbook=False, competing_hypothesis_count=4,
        )
        # -0.10 for >2 competing hypotheses
        assert score_one - score_many == pytest.approx(0.10, abs=0.01)

    def test_score_always_between_0_and_1(self):
        # All negative signals
        score_low = score_hypothesis(
            hypothesis="weak", evidence=[], similar_incidents=[],
            deployment_correlated=False, anomaly_type="X",
            has_runbook=False, competing_hypothesis_count=5,
        )
        assert 0.0 <= score_low <= 1.0

        # All positive signals
        score_high = score_hypothesis(
            hypothesis="strong", evidence=["e1","e2","e3","e4","e5"],
            similar_incidents=["INC-001","INC-002","INC-003"],
            deployment_correlated=True, anomaly_type="LATENCY_SPIKE",
            has_runbook=True, competing_hypothesis_count=1,
        )
        assert 0.0 <= score_high <= 1.0

    def test_evidence_quality_boost(self):
        score_no_ev = score_hypothesis(
            hypothesis="test", evidence=[],
            similar_incidents=["INC-001"],
            deployment_correlated=True, anomaly_type="X",
            has_runbook=True, competing_hypothesis_count=1,
        )
        score_rich_ev = score_hypothesis(
            hypothesis="test", evidence=["e1","e2","e3","e4","e5"],
            similar_incidents=["INC-001"],
            deployment_correlated=True, anomaly_type="X",
            has_runbook=True, competing_hypothesis_count=1,
        )
        # +0.05 for >=4 evidence items
        assert score_rich_ev >= score_no_ev

    def test_max_confidence_scenario(self):
        score = score_hypothesis(
            hypothesis="deployment caused latency spike",
            evidence=["DB connections 99/100", "New deployment 12min ago",
                      "Same pattern as INC-2025-001", "Missing index in query plan"],
            similar_incidents=["INC-2025-001", "INC-2025-020", "INC-2025-045"],
            deployment_correlated=True,
            anomaly_type="LATENCY_SPIKE",
            has_runbook=True,
            competing_hypothesis_count=2,
        )
        assert score >= 0.80, f"Expected high confidence, got {score}"

    def test_min_confidence_scenario(self):
        score = score_hypothesis(
            hypothesis="unknown cause",
            evidence=[],
            similar_incidents=[],
            deployment_correlated=False,
            anomaly_type="UNKNOWN",
            has_runbook=False,
            competing_hypothesis_count=5,
        )
        assert score <= 0.30, f"Expected low confidence, got {score}"

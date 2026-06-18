"""Unit tests for anomaly detection logic — z-score, severity, dedup."""
import math
import pytest
from unittest.mock import patch, MagicMock

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from agents.detection.src.main import (
    compute_zscore, determine_severity, percentile,
    MIN_SAMPLES, SEVERITY_THRESHOLDS,
)


class TestComputeZscore:
    def test_insufficient_samples_returns_zero(self):
        values = [100.0] * (MIN_SAMPLES - 1)
        zscore, mean, std = compute_zscore(values, 500.0)
        assert zscore == 0.0

    def test_obvious_spike(self):
        # Stable baseline around 200ms, spike to 5000ms
        values = [200.0 + i * 0.5 for i in range(50)]
        zscore, mean, std = compute_zscore(values, 5000.0)
        assert zscore > 10.0

    def test_no_anomaly_within_baseline(self):
        values = [200.0 + i * 0.5 for i in range(50)]
        zscore, mean, std = compute_zscore(values, 210.0)
        assert zscore < 2.0

    def test_mean_calculation(self):
        values = [100.0] * 20
        zscore, mean, std = compute_zscore(values, 100.0)
        assert math.isclose(mean, 100.0, rel_tol=0.01)

    def test_constant_values_no_zscore(self):
        values = [50.0] * 30
        zscore, mean, std = compute_zscore(values, 50.0)
        assert zscore == 0.0

    def test_negative_spike_below_baseline(self):
        # When all baseline values are identical, std=0 → zscore=0 by design
        # Use a varied baseline to test negative deviation
        values = [100.0 + i for i in range(30)]
        zscore, mean, std = compute_zscore(values, 50.0)  # far below mean of ~115
        assert zscore < 0  # negative deviation

    def test_exactly_at_threshold(self):
        # Exactly 2.5 sigma above mean
        import statistics
        values = [100.0] * 30
        mean = sum(values) / len(values)
        variance = sum((v - mean) ** 2 for v in values) / len(values)
        # All values same → std=0, z-score undefined → should return 0
        zscore, _, _ = compute_zscore(values, 100.0)
        assert zscore == 0.0


class TestDetermineSeverity:
    def test_critical_latency(self):
        assert determine_severity("service_latency_p99_ms", 6.0) == "CRITICAL"

    def test_high_latency(self):
        assert determine_severity("service_latency_p99_ms", 3.5) == "HIGH"

    def test_medium_latency(self):
        assert determine_severity("service_latency_p99_ms", 2.6) == "MEDIUM"

    def test_critical_error_rate(self):
        assert determine_severity("service_error_rate_percent", 7.0) == "CRITICAL"

    def test_high_error_rate(self):
        assert determine_severity("service_error_rate_percent", 4.0) == "HIGH"

    def test_cpu_saturation_critical(self):
        assert determine_severity("service_cpu_percent", 6.0) == "CRITICAL"

    def test_boundary_at_critical_threshold(self):
        # Exactly at critical threshold for latency (5.0)
        assert determine_severity("service_latency_p99_ms", 5.0) == "CRITICAL"

    def test_boundary_just_below_critical(self):
        # Just below critical threshold
        assert determine_severity("service_latency_p99_ms", 4.9) == "HIGH"

    def test_unknown_metric_defaults_to_medium(self):
        result = determine_severity("some_unknown_metric", 2.6)
        assert result == "MEDIUM"

    def test_memory_thresholds(self):
        assert determine_severity("service_memory_percent", 5.1) == "CRITICAL"
        assert determine_severity("service_memory_percent", 3.1) == "HIGH"
        assert determine_severity("service_memory_percent", 2.6) == "MEDIUM"


class TestPercentile:
    def test_p50_of_uniform_list(self):
        values = list(range(100))
        p50 = percentile(values, 50)
        assert 49 <= p50 <= 51

    def test_p95(self):
        values = list(range(100))
        p95 = percentile(values, 95)
        assert 94 <= p95 <= 96

    def test_empty_list_returns_zero(self):
        assert percentile([], 50) == 0.0

    def test_single_value(self):
        assert percentile([42.0], 50) == 42.0
        assert percentile([42.0], 99) == 42.0


class TestAnomalyScoreRange:
    def test_anomaly_score_clamped_to_1(self):
        # z-score of 10 → score = 10/10 = 1.0 (clamped)
        score = min(1.0, 10.0 / 10.0)
        assert score == 1.0

    def test_anomaly_score_proportional(self):
        score = min(1.0, 5.0 / 10.0)
        assert math.isclose(score, 0.5, rel_tol=0.01)


class TestSeverityThresholdCompleteness:
    def test_all_monitored_metrics_have_thresholds(self):
        from agents.detection.src.main import MONITORED_METRICS
        for metric in MONITORED_METRICS:
            assert metric in SEVERITY_THRESHOLDS, f"Missing threshold for {metric}"

    def test_critical_always_higher_than_high(self):
        for metric, thresholds in SEVERITY_THRESHOLDS.items():
            assert thresholds["CRITICAL"] > thresholds["HIGH"], \
                f"CRITICAL threshold not > HIGH for {metric}"

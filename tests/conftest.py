"""
Shared pytest fixtures for unit, integration, and e2e tests.

Unit tests: no external dependencies (all mocked).
Integration tests: real Kafka/Redis/Postgres via testcontainers.
E2E tests: full Docker Compose stack (set E2E=true to enable).
"""
from __future__ import annotations
import json
import os
import sys
import time
import threading
import subprocess
from typing import Generator
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# ─── Mark helpers ─────────────────────────────────────────────────────────────

def pytest_configure(config):
    config.addinivalue_line("markers", "unit: fast tests with no Docker dependencies")
    config.addinivalue_line("markers", "integration: tests requiring Docker containers")
    config.addinivalue_line("markers", "e2e: full stack tests requiring docker compose up")
    config.addinivalue_line("markers", "contract: schema/API contract tests")


def pytest_collection_modifyitems(items):
    # Skip e2e tests unless E2E=true is set
    if not os.getenv("E2E"):
        skip_e2e = pytest.mark.skip(reason="E2E tests disabled — set E2E=true to run")
        for item in items:
            if "e2e" in item.keywords:
                item.add_marker(skip_e2e)


# ─── Fixture data ─────────────────────────────────────────────────────────────

@pytest.fixture
def sample_metric_event() -> dict:
    return {
        "event_id":       "test-event-001",
        "event_type":     "METRIC",
        "source_service": "payment-service",
        "environment":    "production",
        "timestamp":      1_700_000_000_000,
        "metric_name":    "service_latency_p99_ms",
        "metric_value":   8500.0,
        "labels":         {},
    }


@pytest.fixture
def sample_anomaly_event() -> dict:
    return {
        "event_id":          "anomaly-001",
        "incident_id":       "inc-test-001",
        "anomaly_type":      "LATENCY_SPIKE",
        "severity":          "CRITICAL",
        "affected_services": ["payment-service"],
        "detection_time":    1_700_000_000_000,
        "trigger_metric":    "service_latency_p99_ms",
        "observed_value":    8500.0,
        "baseline_value":    420.0,
        "deviation_sigma":   6.2,
        "anomaly_score":     0.82,
        "description":       "P99 latency spike 6.2σ above baseline on payment-service",
    }


@pytest.fixture
def sample_incident_opened() -> dict:
    return {
        "event_id":           "opened-001",
        "incident_id":        "inc-test-001",
        "anomaly_type":       "LATENCY_SPIKE",
        "severity":           "CRITICAL",
        "affected_services":  ["payment-service"],
        "detection_time":     1_700_000_000_000,
        "opened_at":          1_700_000_015_000,
        "correlation_signals": [
            {
                "signal_type": "TEMPORAL_PROXIMITY",
                "strength":    0.85,
                "description": "Deployment 12 minutes before anomaly",
                "evidence":    ["payment-service v2.14.1", "change_type: CODE"],
            }
        ],
        "blast_radius": {
            "affected_services":          ["payment-service", "order-service"],
            "primary_service_criticality": "P0",
            "estimated_user_impact":       "~100% of active users affected",
            "estimated_revenue_impact":    "High — payment processing blocked",
        },
        "deployment_context": {
            "deployment_id":          "dep-001",
            "service_name":           "payment-service",
            "version":                "v2.14.1",
            "deployed_at":            1_699_999_280_000,
            "change_type":            "CODE",
            "correlation_confidence": 0.85,
            "time_delta_minutes":     12,
            "known_risks":            ["New query pattern not indexed"],
        },
        "description": "Critical latency spike on payment-service correlated with recent deployment.",
    }


@pytest.fixture
def sample_rca_event() -> dict:
    return {
        "event_id":     "rca-001",
        "incident_id":  "inc-test-001",
        "rca_id":       "rca-uuid-001",
        "generated_at": 1_700_000_150_000,
        "root_cause_candidates": [
            {
                "rank":              1,
                "hypothesis":        "Missing database index on transactions table caused full table scans, exhausting connection pool",
                "confidence":        0.85,
                "evidence":          ["DB connections at 99/100", "P99 latency 8500ms", "Recent deployment added new query"],
                "similar_incidents": ["INC-2025-001", "INC-2025-020"],
                "runbook_refs":      ["High API Latency (P99 Spike)", "Database Connection Pool Exhaustion"],
            }
        ],
        "top_root_cause":  "Missing database index on transactions table",
        "top_confidence":  0.85,
        "anomaly_type":    "LATENCY_SPIKE",
        "affected_services": ["payment-service"],
        "severity":        "CRITICAL",
    }


@pytest.fixture
def sample_service_registry() -> list[dict]:
    return [
        {
            "service_name":        "payment-service",
            "team_owner":          "payments-team",
            "criticality":         "P0",
            "sla_p99_latency_ms":  500,
            "sla_error_rate_pct":  0.1,
            "on_call_rotation":    "payments-oncall@company.com",
            "downstream_services": ["postgres", "redis"],
        }
    ]


# ─── Mock fixtures ────────────────────────────────────────────────────────────

@pytest.fixture
def mock_anthropic():
    with patch("shared.llm_client.anthropic.Anthropic") as m:
        client = MagicMock()
        m.return_value = client
        yield client


@pytest.fixture
def mock_redis():
    with patch("shared.redis_client.get_client") as m:
        r = MagicMock()
        m.return_value = r
        r.ping.return_value = True
        r.get.return_value = None
        r.set.return_value = True
        r.lrange.return_value = []
        r.lpush.return_value = 1
        yield r


@pytest.fixture
def mock_kafka_producer():
    with patch("shared.kafka_client.make_producer") as m:
        producer = MagicMock()
        m.return_value = producer
        yield producer


@pytest.fixture
def mock_db():
    with patch("shared.db_client.get_conn") as m:
        conn = MagicMock()
        m.return_value.__enter__ = lambda s: conn
        m.return_value.__exit__ = MagicMock(return_value=False)
        yield conn


# ─── Integration fixtures (testcontainers) ───────────────────────────────────

@pytest.fixture(scope="session")
def kafka_container():
    """Start a real Kafka container for integration tests."""
    try:
        from testcontainers.kafka import KafkaContainer
        with KafkaContainer() as kafka:
            os.environ["KAFKA_BOOTSTRAP_SERVERS"] = kafka.get_bootstrap_server()
            yield kafka
    except ImportError:
        pytest.skip("testcontainers[kafka] not installed")


@pytest.fixture(scope="session")
def redis_container():
    """Start a real Redis container for integration tests."""
    try:
        from testcontainers.redis import RedisContainer
        with RedisContainer() as redis_c:
            host = redis_c.get_container_host_ip()
            port = redis_c.get_exposed_port(6379)
            os.environ["REDIS_HOST"] = host
            os.environ["REDIS_PORT"] = str(port)
            yield redis_c
    except ImportError:
        pytest.skip("testcontainers[redis] not installed")


@pytest.fixture(scope="session")
def postgres_container():
    """Start a real Postgres container for integration tests."""
    try:
        from testcontainers.postgres import PostgresContainer
        with PostgresContainer(
            "postgres:15",
            dbname="sre_copilot",
            user="sre_user",
            password="sre_password",
        ) as pg:
            os.environ["POSTGRES_HOST"]     = pg.get_container_host_ip()
            os.environ["POSTGRES_PORT"]     = str(pg.get_exposed_port(5432))
            os.environ["POSTGRES_DB"]       = "sre_copilot"
            os.environ["POSTGRES_USER"]     = "sre_user"
            os.environ["POSTGRES_PASSWORD"] = "sre_password"

            # Run schema migrations
            import psycopg2
            conn = psycopg2.connect(
                host=pg.get_container_host_ip(),
                port=pg.get_exposed_port(5432),
                dbname="sre_copilot",
                user="sre_user",
                password="sre_password",
            )
            schema_path = os.path.join(
                os.path.dirname(__file__), "..", "infrastructure", "postgres", "init.sql"
            )
            with open(schema_path) as f:
                conn.cursor().execute(f.read())
            conn.commit()
            conn.close()
            yield pg
    except ImportError:
        pytest.skip("testcontainers[postgresql] not installed")

"""Unit tests for Pydantic models — validation, serialisation, round-trips."""
import pytest
import json
from shared.models import (
    RawMetricEvent, RawLogEvent, RawDeploymentEvent,
    AnomalyDetectedEvent, CorrelationSignal, IncidentOpenedEvent,
    RootCauseCandidate, DeploymentCorrelation, RCACompletedEvent,
    RemediationStep, RemediationPlanEvent,
    IncidentResolvedEvent, PostmortemGeneratedEvent,
    KnowledgeChunk, KRRequest, KRResponse, KRQueryType,
    ServiceInfo, PostmortemDocument,
    AnomalyType, Severity, IncidentStatus,
    now_ms, new_uuid,
)


class TestHelpers:
    def test_now_ms_returns_int(self):
        ts = now_ms()
        assert isinstance(ts, int)
        assert ts > 1_000_000_000_000  # after year 2001

    def test_new_uuid_is_unique(self):
        ids = {new_uuid() for _ in range(100)}
        assert len(ids) == 100

    def test_new_uuid_format(self):
        uid = new_uuid()
        parts = uid.split("-")
        assert len(parts) == 5


class TestRawMetricEvent:
    def test_creates_with_required_fields(self):
        e = RawMetricEvent(source_service="payment-service", metric_name="cpu", metric_value=45.0)
        assert e.source_service == "payment-service"
        assert e.metric_value == 45.0
        assert e.event_type == "METRIC"
        assert e.environment == "production"

    def test_auto_fields_populated(self):
        e = RawMetricEvent(source_service="svc", metric_name="m", metric_value=1.0)
        assert e.event_id != ""
        assert e.timestamp > 0

    def test_json_round_trip(self):
        e = RawMetricEvent(source_service="svc", metric_name="m", metric_value=99.9)
        d = e.model_dump()
        e2 = RawMetricEvent(**d)
        assert e2.metric_value == 99.9

    def test_json_serializable(self):
        e = RawMetricEvent(source_service="svc", metric_name="m", metric_value=1.0)
        raw = json.dumps(e.model_dump())
        assert "svc" in raw


class TestAnomalyDetectedEvent:
    def test_defaults(self):
        e = AnomalyDetectedEvent()
        assert e.severity == Severity.HIGH
        assert e.anomaly_type == AnomalyType.UNKNOWN
        assert e.anomaly_score == 0.0

    def test_custom_fields(self):
        e = AnomalyDetectedEvent(
            anomaly_type="LATENCY_SPIKE",
            severity="CRITICAL",
            affected_services=["payment-service"],
            deviation_sigma=6.2,
            anomaly_score=0.82,
        )
        assert e.anomaly_type == "LATENCY_SPIKE"
        assert e.severity == "CRITICAL"
        assert "payment-service" in e.affected_services

    def test_incident_id_auto_generated(self):
        e1 = AnomalyDetectedEvent()
        e2 = AnomalyDetectedEvent()
        assert e1.incident_id != e2.incident_id

    def test_all_required_fields_serialized(self):
        e = AnomalyDetectedEvent(
            anomaly_type="CPU_SATURATION",
            severity="HIGH",
            affected_services=["api-gateway"],
            observed_value=95.0,
            baseline_value=22.0,
            deviation_sigma=5.2,
        )
        d = e.model_dump()
        for key in ("incident_id", "anomaly_type", "severity", "affected_services",
                    "detection_time", "anomaly_score", "deviation_sigma"):
            assert key in d


class TestCorrelationSignal:
    def test_valid_signal(self):
        s = CorrelationSignal(
            signal_type="TEMPORAL_PROXIMITY",
            strength=0.85,
            description="Deployment 12 min before anomaly",
            evidence=["v2.14.1 deployed", "CODE change"],
        )
        assert s.strength == 0.85
        assert len(s.evidence) == 2

    def test_strength_is_float(self):
        s = CorrelationSignal(signal_type="RESOURCE_CONTENTION", strength=0.7, description="test")
        assert isinstance(s.strength, float)


class TestRCACompletedEvent:
    def test_deployment_correlation_optional(self):
        e = RCACompletedEvent(incident_id="inc-001")
        assert e.deployment_correlation is None

    def test_deployment_correlation_set(self):
        dc = DeploymentCorrelation(
            service_name="payment-service",
            version="v2.14.1",
            correlation_confidence=0.85,
            time_delta_minutes=12,
        )
        e = RCACompletedEvent(incident_id="inc-001", deployment_correlation=dc)
        assert e.deployment_correlation.version == "v2.14.1"

    def test_candidates_list(self):
        candidates = [
            RootCauseCandidate(rank=1, hypothesis="DB index missing", confidence=0.85),
            RootCauseCandidate(rank=2, hypothesis="Connection pool small", confidence=0.60),
        ]
        e = RCACompletedEvent(incident_id="inc-001", root_cause_candidates=candidates)
        assert len(e.root_cause_candidates) == 2
        assert e.root_cause_candidates[0].confidence == 0.85


class TestRemediationStep:
    def test_valid_step(self):
        s = RemediationStep(
            step_id=1,
            priority="IMMEDIATE",
            action="Scale payment-service to 8 replicas",
            rationale="CPU saturation from high traffic",
            risk_level="LOW",
            rollback="Scale back down",
        )
        assert s.step_id == 1
        assert s.priority == "IMMEDIATE"

    def test_defaults(self):
        s = RemediationStep(step_id=1, priority="IMMEDIATE", action="act", rationale="rat")
        assert s.risk_level == "LOW"
        assert s.rollback == ""
        assert s.owner == ""


class TestRemediationPlanEvent:
    def test_full_plan(self, sample_rca_event):
        steps = [
            RemediationStep(step_id=1, priority="IMMEDIATE", action="Check logs", rationale="First step"),
        ]
        plan = RemediationPlanEvent(
            incident_id=sample_rca_event["incident_id"],
            root_cause="Missing index",
            confidence=0.85,
            action_steps=steps,
            escalation_path=["oncall@company.com"],
            estimated_resolution_time="15-30 minutes",
        )
        assert len(plan.action_steps) == 1
        assert plan.confidence == 0.85

    def test_json_round_trip(self):
        plan = RemediationPlanEvent(incident_id="inc-001", root_cause="test", confidence=0.7)
        d = plan.model_dump()
        plan2 = RemediationPlanEvent(**d)
        assert plan2.incident_id == "inc-001"


class TestKRModels:
    def test_kr_request_defaults(self):
        req = KRRequest(
            incident_id="inc-001",
            query_type=KRQueryType.INCIDENT_SIMILARITY.value,
            query_text="latency spike payment-service",
        )
        assert req.request_id != ""
        assert req.time_window_minutes == 120

    def test_kr_response_with_chunks(self):
        chunk = KnowledgeChunk(
            chunk_id="c001",
            source_type="incidents",
            source_id="INC-2025-001",
            title="Payment latency spike",
            content="P99 latency spiked due to missing DB index",
            score=0.92,
        )
        resp = KRResponse(
            request_id="r001",
            incident_id="inc-001",
            query_type=KRQueryType.INCIDENT_SIMILARITY.value,
            chunks=[chunk],
            total_tokens=250,
        )
        assert len(resp.chunks) == 1
        assert resp.chunks[0].score == 0.92

    def test_kr_response_error(self):
        resp = KRResponse(
            request_id="r001",
            incident_id="inc-001",
            query_type="RUNBOOK_LOOKUP",
            error="Qdrant unavailable",
        )
        assert resp.error == "Qdrant unavailable"
        assert len(resp.chunks) == 0


class TestPostmortemDocument:
    def test_default_values(self):
        doc = PostmortemDocument(incident_id="inc-001")
        assert doc.executive_summary == ""
        assert doc.full_markdown == ""
        assert doc.mttr_minutes == 0.0

    def test_all_sections(self):
        doc = PostmortemDocument(
            incident_id="inc-001",
            title="Test Postmortem",
            executive_summary="A critical incident occurred.",
            timeline="- 14:30 UTC Detected\n- 14:45 UTC Resolved",
            root_cause="Missing database index",
            contributing_factors="1. No query review\n2. No staging load test",
            action_items="| Action | Owner |\n| Add index | payments-team |",
            full_markdown="# Postmortem\n...",
        )
        assert "payments-team" in doc.action_items
        assert "Detected" in doc.timeline


class TestEnums:
    def test_anomaly_types(self):
        expected = {
            "LATENCY_SPIKE", "ERROR_RATE_SPIKE", "CPU_SATURATION",
            "MEMORY_LEAK", "DB_CONNECTION_EXHAUSTION", "KAFKA_CONSUMER_LAG",
            "DEPLOYMENT_FAILURE", "DEPENDENCY_OUTAGE", "NETWORK_PARTITION", "UNKNOWN",
        }
        actual = {e.value for e in AnomalyType}
        assert expected == actual

    def test_severity_levels(self):
        assert set(Severity) == {Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW}

    def test_incident_status_flow(self):
        statuses = [e.value for e in IncidentStatus]
        for s in ("DETECTING", "CORRELATING", "INVESTIGATING", "RCA_COMPLETE", "RESOLVED"):
            assert s in statuses

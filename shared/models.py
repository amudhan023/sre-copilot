"""Shared Pydantic event models for all SRE Copilot services."""
from __future__ import annotations
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field


def now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def new_uuid() -> str:
    return str(uuid.uuid4())


# ─── Enums ────────────────────────────────────────────────────────────────────

class EventType(str, Enum):
    METRIC     = "METRIC"
    LOG        = "LOG"
    DEPLOYMENT = "DEPLOYMENT"


class Severity(str, Enum):
    CRITICAL = "CRITICAL"
    HIGH     = "HIGH"
    MEDIUM   = "MEDIUM"
    LOW      = "LOW"


class AnomalyType(str, Enum):
    LATENCY_SPIKE            = "LATENCY_SPIKE"
    ERROR_RATE_SPIKE         = "ERROR_RATE_SPIKE"
    CPU_SATURATION           = "CPU_SATURATION"
    MEMORY_LEAK              = "MEMORY_LEAK"
    DB_CONNECTION_EXHAUSTION = "DB_CONNECTION_EXHAUSTION"
    KAFKA_CONSUMER_LAG       = "KAFKA_CONSUMER_LAG"
    DEPLOYMENT_FAILURE       = "DEPLOYMENT_FAILURE"
    DEPENDENCY_OUTAGE        = "DEPENDENCY_OUTAGE"
    NETWORK_PARTITION        = "NETWORK_PARTITION"
    UNKNOWN                  = "UNKNOWN"


class IncidentStatus(str, Enum):
    DETECTING     = "DETECTING"
    CORRELATING   = "CORRELATING"
    INVESTIGATING = "INVESTIGATING"
    RCA_COMPLETE  = "RCA_COMPLETE"
    REMEDIATING   = "REMEDIATING"
    RESOLVED      = "RESOLVED"


class KRQueryType(str, Enum):
    INCIDENT_SIMILARITY  = "INCIDENT_SIMILARITY"
    RUNBOOK_LOOKUP       = "RUNBOOK_LOOKUP"
    ARCHITECTURE_CONTEXT = "ARCHITECTURE_CONTEXT"
    DEPLOYMENT_NOTES     = "DEPLOYMENT_NOTES"
    POSTMORTEM_PATTERNS  = "POSTMORTEM_PATTERNS"


# ─── Raw telemetry events ─────────────────────────────────────────────────────

class RawMetricEvent(BaseModel):
    event_id:       str              = Field(default_factory=new_uuid)
    event_type:     str              = EventType.METRIC
    source_service: str
    environment:    str              = "production"
    timestamp:      int              = Field(default_factory=now_ms)
    metric_name:    str
    metric_value:   float
    labels:         dict[str, str]   = {}


class RawLogEvent(BaseModel):
    event_id:       str              = Field(default_factory=new_uuid)
    event_type:     str              = EventType.LOG
    source_service: str
    environment:    str              = "production"
    timestamp:      int              = Field(default_factory=now_ms)
    level:          str              = "INFO"
    message:        str
    trace_id:       str              = Field(default_factory=new_uuid)
    labels:         dict[str, str]   = {}


class RawDeploymentEvent(BaseModel):
    event_id:       str              = Field(default_factory=new_uuid)
    event_type:     str              = EventType.DEPLOYMENT
    source_service: str
    environment:    str              = "production"
    timestamp:      int              = Field(default_factory=now_ms)
    version:        str
    deployed_by:    str              = "ci-cd-pipeline"
    change_type:    str              = "CODE"   # CODE | CONFIG | DEPENDENCY | INFRASTRUCTURE
    git_sha:        str              = ""
    description:    str              = ""
    known_risks:    list[str]        = []


# ─── Detection Agent events ───────────────────────────────────────────────────

class AnomalyDetectedEvent(BaseModel):
    event_id:          str       = Field(default_factory=new_uuid)
    incident_id:       str       = Field(default_factory=new_uuid)
    anomaly_type:      str       = AnomalyType.UNKNOWN
    severity:          str       = Severity.HIGH
    affected_services: list[str] = []
    detection_time:    int       = Field(default_factory=now_ms)
    trigger_metric:    str       = ""
    observed_value:    float     = 0.0
    baseline_value:    float     = 0.0
    deviation_sigma:   float     = 0.0
    anomaly_score:     float     = 0.0
    # Baseline statistics for downstream context
    p50_value:         float     = 0.0
    p95_value:         float     = 0.0
    p99_value:         float     = 0.0
    window_start:      int       = 0
    window_end:        int       = 0
    description:       str       = ""
    raw_events:        list[str] = []


# ─── Correlation Agent events ─────────────────────────────────────────────────

class CorrelationSignal(BaseModel):
    signal_type:  str
    strength:     float           # 0.0 – 1.0
    description:  str
    evidence:     list[str] = []


class IncidentOpenedEvent(BaseModel):
    event_id:             str                      = Field(default_factory=new_uuid)
    incident_id:          str
    anomaly_type:         str
    severity:             str
    affected_services:    list[str]                = []
    detection_time:       int
    opened_at:            int                      = Field(default_factory=now_ms)
    correlation_signals:  list[CorrelationSignal]  = []
    blast_radius:         dict[str, Any]           = {}
    deployment_context:   Optional[dict[str, Any]] = None
    recent_metrics:       dict[str, list[float]]   = {}
    recent_errors:        list[str]                = []
    description:          str                      = ""


# ─── Investigation Agent events ───────────────────────────────────────────────

class RootCauseCandidate(BaseModel):
    rank:              int
    hypothesis:        str
    confidence:        float     # 0.0 – 1.0
    evidence:          list[str] = []
    similar_incidents: list[str] = []  # incident IDs from vector search
    runbook_refs:      list[str] = []


class DeploymentCorrelation(BaseModel):
    deployment_id:          str       = ""
    service_name:           str       = ""
    version:                str       = ""
    deployed_at:            int       = 0
    change_type:            str       = ""
    correlation_confidence: float     = 0.0
    time_delta_minutes:     int       = 0
    known_risks:            list[str] = []


class RCACompletedEvent(BaseModel):
    event_id:               str                         = Field(default_factory=new_uuid)
    incident_id:            str
    rca_id:                 str                         = Field(default_factory=new_uuid)
    generated_at:           int                         = Field(default_factory=now_ms)
    root_cause_candidates:  list[RootCauseCandidate]    = []
    top_root_cause:         str                         = ""
    top_confidence:         float                       = 0.0
    blast_radius:           dict[str, Any]              = {}
    deployment_correlation: Optional[DeploymentCorrelation] = None
    anomaly_type:           str                         = ""
    affected_services:      list[str]                   = []
    severity:               str                         = Severity.HIGH


# ─── Remediation Agent events ─────────────────────────────────────────────────

class RemediationStep(BaseModel):
    step_id:          int
    priority:         str   # IMMEDIATE | WITHIN_15MIN | WITHIN_1HOUR
    action:           str
    rationale:        str
    risk_level:       str   = "LOW"   # LOW | MEDIUM | HIGH
    rollback:         str   = ""
    owner:            str   = ""
    expected_outcome: str   = ""
    runbook_source:   str   = ""


class RemediationPlanEvent(BaseModel):
    event_id:                  str                   = Field(default_factory=new_uuid)
    incident_id:               str
    root_cause:                str
    confidence:                float
    action_steps:              list[RemediationStep] = []
    escalation_path:           list[str]             = []
    runbook_references:        list[str]             = []
    estimated_resolution_time: str                   = "Unknown"
    generated_at:              int                   = Field(default_factory=now_ms)
    anomaly_type:              str                   = ""
    affected_services:         list[str]             = []
    severity:                  str                   = Severity.HIGH


# ─── Resolution / Postmortem events ──────────────────────────────────────────

class IncidentResolvedEvent(BaseModel):
    event_id:          str       = Field(default_factory=new_uuid)
    incident_id:       str
    resolved_at:       int       = Field(default_factory=now_ms)
    detection_time:    int
    mttr_minutes:      float     = 0.0
    resolution_method: str       = "AUTOMATIC_RECOVERY"
    top_root_cause:    str       = ""
    anomaly_type:      str       = ""
    affected_services: list[str] = []
    severity:          str       = Severity.HIGH


class PostmortemGeneratedEvent(BaseModel):
    event_id:          str       = Field(default_factory=new_uuid)
    incident_id:       str
    generated_at:      int       = Field(default_factory=now_ms)
    postmortem:        str       = ""   # full markdown
    mttr_minutes:      float     = 0.0
    severity:          str       = Severity.HIGH
    anomaly_type:      str       = ""
    affected_services: list[str] = []


# ─── Knowledge Retrieval Agent models ────────────────────────────────────────

class KnowledgeChunk(BaseModel):
    chunk_id:    str
    source_type: str             # incidents | runbooks | architecture | deployments | postmortems
    source_id:   str
    title:       str
    content:     str
    score:       float           = 0.0
    metadata:    dict[str, Any]  = {}


class KRRequest(BaseModel):
    request_id:           str       = Field(default_factory=new_uuid)
    incident_id:          str
    query_type:           str       # KRQueryType value
    query_text:           str
    anomaly_type:         str       = ""
    affected_services:    list[str] = []
    incident_time:        int       = Field(default_factory=now_ms)
    time_window_minutes:  int       = 120   # for DEPLOYMENT_NOTES window


class KRResponse(BaseModel):
    request_id:   str
    incident_id:  str
    query_type:   str
    chunks:       list[KnowledgeChunk] = []
    total_tokens: int                  = 0
    error:        str                  = ""


# ─── Application / Service Registry models ───────────────────────────────────

class ServiceInfo(BaseModel):
    service_name:        str
    team_owner:          str       = ""
    criticality:         str       = "P1"
    sla_p99_latency_ms:  int       = 500
    sla_error_rate_pct:  float     = 1.0
    on_call_rotation:    str       = ""
    slack_channel:       str       = ""
    upstream_services:   list[str] = []
    downstream_services: list[str] = []


class PostmortemDocument(BaseModel):
    incident_id:          str
    generated_at:         int       = Field(default_factory=now_ms)
    title:                str       = ""
    severity:             str       = Severity.HIGH
    anomaly_type:         str       = ""
    affected_services:    list[str] = []
    detection_time:       int       = 0
    resolution_time:      int       = 0
    mttr_minutes:         float     = 0.0
    executive_summary:    str       = ""
    timeline:             str       = ""
    root_cause:           str       = ""
    contributing_factors: str       = ""
    impact_analysis:      str       = ""
    what_went_well:       str       = ""
    what_could_improve:   str       = ""
    action_items:         str       = ""
    full_markdown:        str       = ""

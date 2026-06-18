-- SRE Copilot — PostgreSQL Schema

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";

-- ─── Core incident table ──────────────────────────────────────────────────────

CREATE TABLE incidents (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    severity            VARCHAR(20) NOT NULL
                        CHECK (severity IN ('CRITICAL','HIGH','MEDIUM','LOW')),
    anomaly_type        VARCHAR(60) NOT NULL,
    affected_services   TEXT[]       NOT NULL DEFAULT '{}',
    status              VARCHAR(30)  NOT NULL DEFAULT 'DETECTING'
                        CHECK (status IN (
                            'DETECTING','CORRELATING','INVESTIGATING',
                            'RCA_COMPLETE','REMEDIATING','RESOLVED'
                        )),
    detection_time      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    resolution_time     TIMESTAMPTZ,
    mttr_minutes        INTEGER GENERATED ALWAYS AS (
                            CASE WHEN resolution_time IS NOT NULL
                            THEN EXTRACT(EPOCH FROM (resolution_time - detection_time))::INTEGER / 60
                            ELSE NULL END
                        ) STORED,
    anomaly_score       FLOAT,
    trigger_metric      TEXT,
    observed_value      FLOAT,
    baseline_value      FLOAT,
    deviation_sigma     FLOAT,
    description         TEXT,
    correlation_context JSONB,
    blast_radius        JSONB,
    rca_candidates      JSONB,
    top_root_cause      TEXT,
    rca_confidence      FLOAT,
    remediation_plan    JSONB,
    raw_event_id        UUID,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_incidents_status       ON incidents (status);
CREATE INDEX idx_incidents_severity     ON incidents (severity);
CREATE INDEX idx_incidents_detection    ON incidents (detection_time DESC);
CREATE INDEX idx_incidents_services     ON incidents USING GIN (affected_services);

-- ─── Postmortems ──────────────────────────────────────────────────────────────

CREATE TABLE postmortems (
    id                UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    incident_id       UUID         NOT NULL UNIQUE REFERENCES incidents(id) ON DELETE CASCADE,
    title             TEXT         NOT NULL DEFAULT '',
    full_markdown     TEXT         NOT NULL DEFAULT '',
    executive_summary TEXT         NOT NULL DEFAULT '',
    root_cause        TEXT         NOT NULL DEFAULT '',
    mttr_minutes      FLOAT,
    severity          VARCHAR(20),
    anomaly_type      VARCHAR(60),
    generated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_postmortems_incident ON postmortems (incident_id);
CREATE INDEX idx_postmortems_generated ON postmortems (generated_at DESC);

-- ─── Agent audit log ──────────────────────────────────────────────────────────

CREATE TABLE agent_events (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    incident_id UUID REFERENCES incidents(id) ON DELETE CASCADE,
    agent_name  VARCHAR(50)  NOT NULL,
    event_type  VARCHAR(80)  NOT NULL,
    payload     JSONB,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_agent_events_incident ON agent_events (incident_id);
CREATE INDEX idx_agent_events_agent    ON agent_events (agent_name);
CREATE INDEX idx_agent_events_created  ON agent_events (created_at DESC);

-- ─── Email notifications ──────────────────────────────────────────────────────

CREATE TABLE email_notifications (
    id                UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    incident_id       UUID REFERENCES incidents(id) ON DELETE CASCADE,
    notification_type VARCHAR(50)  NOT NULL,
    recipients        TEXT[]       NOT NULL DEFAULT '{}',
    subject           TEXT         NOT NULL,
    body_html         TEXT,
    status            VARCHAR(20)  NOT NULL DEFAULT 'PENDING'
                      CHECK (status IN ('PENDING','SENT','FAILED')),
    sent_at           TIMESTAMPTZ,
    error_message     TEXT,
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_emails_incident ON email_notifications (incident_id);
CREATE INDEX idx_emails_created  ON email_notifications (created_at DESC);

-- ─── Deployment history ───────────────────────────────────────────────────────

CREATE TABLE deployments (
    id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    service_name  TEXT         NOT NULL,
    version       TEXT         NOT NULL,
    deployed_by   TEXT         NOT NULL DEFAULT 'ci-cd-pipeline',
    change_type   VARCHAR(30)  NOT NULL DEFAULT 'CODE',
    git_sha       TEXT,
    description   TEXT,
    known_risks   TEXT[]       NOT NULL DEFAULT '{}',
    deployed_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_deployments_service ON deployments (service_name, deployed_at DESC);
CREATE INDEX idx_deployments_time    ON deployments (deployed_at DESC);

-- ─── Metric snapshots (resolution monitoring) ────────────────────────────────

CREATE TABLE metric_snapshots (
    id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    incident_id   UUID REFERENCES incidents(id) ON DELETE CASCADE,
    service_name  TEXT  NOT NULL,
    metric_name   TEXT  NOT NULL,
    metric_value  FLOAT NOT NULL,
    anomaly_score FLOAT,
    snapshot_time TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_snapshots_incident ON metric_snapshots (incident_id, snapshot_time DESC);

-- ─── Service registry ─────────────────────────────────────────────────────────

CREATE TABLE service_registry (
    service_name        TEXT PRIMARY KEY,
    team_owner          TEXT         NOT NULL,
    criticality         VARCHAR(5)   NOT NULL DEFAULT 'P1',
    sla_p99_latency_ms  INTEGER      DEFAULT 500,
    sla_error_rate_pct  FLOAT        DEFAULT 1.0,
    on_call_rotation    TEXT,
    slack_channel       TEXT,
    upstream_services   TEXT[]       DEFAULT '{}',
    downstream_services TEXT[]       DEFAULT '{}',
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- Seed the 6 simulated microservices
INSERT INTO service_registry
    (service_name, team_owner, criticality, sla_p99_latency_ms, sla_error_rate_pct,
     on_call_rotation, slack_channel, upstream_services, downstream_services)
VALUES
('api-gateway',
    'platform-team',  'P0', 200,  0.5,
    'platform-oncall@company.com', '#platform-alerts',
    '{}',
    ARRAY['payment-service','order-service','user-service']),

('payment-service',
    'payments-team',  'P0', 500,  0.1,
    'payments-oncall@company.com', '#payments-alerts',
    ARRAY['api-gateway'],
    ARRAY['postgres','redis']),

('order-service',
    'commerce-team',  'P0', 400,  0.5,
    'commerce-oncall@company.com', '#commerce-alerts',
    ARRAY['api-gateway'],
    ARRAY['payment-service','inventory-service','kafka','notification-service']),

('user-service',
    'identity-team',  'P1', 300,  0.5,
    'identity-oncall@company.com', '#identity-alerts',
    ARRAY['api-gateway'],
    ARRAY['postgres','redis']),

('notification-service',
    'platform-team',  'P1', 1000, 1.0,
    'platform-oncall@company.com', '#platform-alerts',
    ARRAY['order-service'],
    ARRAY['kafka']),

('inventory-service',
    'commerce-team',  'P1', 400,  1.0,
    'commerce-oncall@company.com', '#commerce-alerts',
    ARRAY['order-service'],
    ARRAY['postgres']);

-- ─── Updated-at trigger ───────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER incidents_updated_at
    BEFORE UPDATE ON incidents
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- ─── Useful views ─────────────────────────────────────────────────────────────

CREATE VIEW active_incidents AS
    SELECT id, severity, anomaly_type, affected_services, status,
           detection_time, top_root_cause, rca_confidence
    FROM incidents
    WHERE status != 'RESOLVED'
    ORDER BY detection_time DESC;

CREATE VIEW incident_summary AS
    SELECT
        i.id, i.severity, i.anomaly_type, i.affected_services,
        i.status, i.detection_time, i.resolution_time, i.mttr_minutes,
        i.top_root_cause, i.rca_confidence,
        p.title            AS postmortem_title,
        p.executive_summary AS postmortem_summary,
        COUNT(ae.id)       AS agent_event_count,
        COUNT(en.id)       AS email_count
    FROM incidents i
    LEFT JOIN postmortems p ON p.incident_id = i.id
    LEFT JOIN agent_events ae ON ae.incident_id = i.id
    LEFT JOIN email_notifications en ON en.incident_id = i.id
    GROUP BY i.id, p.title, p.executive_summary
    ORDER BY i.detection_time DESC;

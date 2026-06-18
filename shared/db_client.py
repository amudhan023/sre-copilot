"""PostgreSQL client — connection pool, incident CRUD, service registry queries."""
from __future__ import annotations
import json
import logging
import os
import time
from contextlib import contextmanager
from typing import Any, Optional

import psycopg2
import psycopg2.pool
import psycopg2.extras

logger = logging.getLogger(__name__)

_pool: Optional[psycopg2.pool.ThreadedConnectionPool] = None


def _dsn() -> str:
    return (
        f"host={os.getenv('POSTGRES_HOST', 'postgres')} "
        f"port={os.getenv('POSTGRES_PORT', '5432')} "
        f"dbname={os.getenv('POSTGRES_DB', 'sre_copilot')} "
        f"user={os.getenv('POSTGRES_USER', 'sre_user')} "
        f"password={os.getenv('POSTGRES_PASSWORD', 'sre_password')}"
    )


def init_pool(min_conn: int = 1, max_conn: int = 10, max_retries: int = 20) -> None:
    global _pool
    for attempt in range(1, max_retries + 1):
        try:
            _pool = psycopg2.pool.ThreadedConnectionPool(min_conn, max_conn, _dsn())
            logger.info("Postgres pool initialized.")
            return
        except Exception as exc:
            logger.warning("Postgres not ready (attempt %d/%d): %s", attempt, max_retries, exc)
            time.sleep(3)
    raise RuntimeError("Postgres never became available.")


@contextmanager
def get_conn():
    if _pool is None:
        init_pool()
    conn = _pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        _pool.putconn(conn)


def execute(sql: str, params: tuple = ()) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)


def fetch_one(sql: str, params: tuple = ()) -> Optional[dict]:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            return dict(row) if row else None


def fetch_all(sql: str, params: tuple = ()) -> list[dict]:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]


# ─── Incident CRUD ────────────────────────────────────────────────────────────

def insert_incident(incident_id: str, data: dict) -> None:
    execute(
        """
        INSERT INTO incidents
            (id, severity, anomaly_type, affected_services, status,
             detection_time, anomaly_score, trigger_metric,
             observed_value, baseline_value, deviation_sigma, description)
        VALUES (%s, %s, %s, %s, %s, to_timestamp(%s / 1000.0),
                %s, %s, %s, %s, %s, %s)
        ON CONFLICT (id) DO NOTHING
        """,
        (
            incident_id,
            data.get("severity", "HIGH"),
            data.get("anomaly_type", "UNKNOWN"),
            data.get("affected_services", []),
            "DETECTING",
            data.get("detection_time", 0),
            data.get("anomaly_score", 0.0),
            data.get("trigger_metric", ""),
            data.get("observed_value", 0.0),
            data.get("baseline_value", 0.0),
            data.get("deviation_sigma", 0.0),
            data.get("description", ""),
        ),
    )


def update_incident(incident_id: str, updates: dict) -> None:
    if not updates:
        return
    allowed = {
        "status", "resolution_time", "correlation_context", "blast_radius",
        "rca_candidates", "top_root_cause", "rca_confidence",
        "remediation_plan", "postmortem",
    }
    set_parts: list[str] = []
    clean_vals: list[Any] = []

    for k, v in updates.items():
        if k not in allowed:
            continue
        if k == "resolution_time":
            set_parts.append("resolution_time = to_timestamp(%s / 1000.0)")
        elif isinstance(v, (dict, list)):
            set_parts.append(f"{k} = %s::jsonb")
        else:
            set_parts.append(f"{k} = %s")
        clean_vals.append(json.dumps(v) if isinstance(v, (dict, list)) else v)

    if not set_parts:
        return

    clean_vals.append(incident_id)
    execute(
        f"UPDATE incidents SET {', '.join(set_parts)} WHERE id = %s",
        tuple(clean_vals),
    )


def get_incident(incident_id: str) -> Optional[dict]:
    return fetch_one("SELECT * FROM incidents WHERE id = %s", (incident_id,))


def get_active_incidents() -> list[dict]:
    return fetch_all(
        "SELECT * FROM incidents WHERE status != 'RESOLVED' ORDER BY detection_time DESC"
    )


def list_incidents(
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    if status:
        return fetch_all(
            "SELECT * FROM incidents WHERE status = %s ORDER BY detection_time DESC LIMIT %s OFFSET %s",
            (status, limit, offset),
        )
    return fetch_all(
        "SELECT * FROM incidents ORDER BY detection_time DESC LIMIT %s OFFSET %s",
        (limit, offset),
    )


def get_incident_timeline(incident_id: str) -> list[dict]:
    """Returns all agent events for an incident, ordered chronologically."""
    return fetch_all(
        "SELECT * FROM agent_events WHERE incident_id = %s ORDER BY created_at ASC",
        (incident_id,),
    )


# ─── Postmortem ───────────────────────────────────────────────────────────────

def insert_postmortem(
    incident_id: str,
    title: str,
    full_markdown: str,
    executive_summary: str,
    root_cause: str,
    mttr_minutes: float,
    severity: str,
    anomaly_type: str,
) -> None:
    execute(
        """
        INSERT INTO postmortems
            (incident_id, title, full_markdown, executive_summary,
             root_cause, mttr_minutes, severity, anomaly_type)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (incident_id) DO UPDATE SET
            full_markdown = EXCLUDED.full_markdown,
            executive_summary = EXCLUDED.executive_summary
        """,
        (incident_id, title, full_markdown, executive_summary,
         root_cause, mttr_minutes, severity, anomaly_type),
    )


def get_postmortem(incident_id: str) -> Optional[dict]:
    return fetch_one("SELECT * FROM postmortems WHERE incident_id = %s", (incident_id,))


# ─── Service Registry ─────────────────────────────────────────────────────────

def get_service_registry() -> list[dict]:
    return fetch_all("SELECT * FROM service_registry ORDER BY criticality, service_name")


def get_service_info(service_name: str) -> Optional[dict]:
    return fetch_one(
        "SELECT * FROM service_registry WHERE service_name = %s", (service_name,)
    )


def get_service_dependencies(service_name: str) -> list[str]:
    """Returns all first-degree downstream services for a given service."""
    row = fetch_one(
        "SELECT downstream_services FROM service_registry WHERE service_name = %s",
        (service_name,),
    )
    if not row:
        return []
    return list(row.get("downstream_services") or [])


def get_services_depending_on(service_name: str) -> list[str]:
    """Returns services that list this service as a downstream dependency."""
    rows = fetch_all(
        "SELECT service_name FROM service_registry WHERE %s = ANY(downstream_services)",
        (service_name,),
    )
    return [r["service_name"] for r in rows]


def get_recent_deployments(
    services: list[str],
    before_ms: int,
    window_minutes: int = 120,
) -> list[dict]:
    """Returns recent deployments for specified services within the time window."""
    since_ms = before_ms - (window_minutes * 60 * 1000)
    return fetch_all(
        """
        SELECT * FROM deployments
        WHERE service_name = ANY(%s)
          AND deployed_at >= to_timestamp(%s / 1000.0)
          AND deployed_at <= to_timestamp(%s / 1000.0)
        ORDER BY deployed_at DESC
        """,
        (services, since_ms, before_ms),
    )


def record_deployment(data: dict) -> None:
    execute(
        """
        INSERT INTO deployments
            (service_name, version, deployed_by, change_type,
             git_sha, description, known_risks, deployed_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, to_timestamp(%s / 1000.0))
        ON CONFLICT DO NOTHING
        """,
        (
            data.get("source_service", ""),
            data.get("version", ""),
            data.get("deployed_by", "ci-cd-pipeline"),
            data.get("change_type", "CODE"),
            data.get("git_sha", ""),
            data.get("description", ""),
            data.get("known_risks", []),
            data.get("timestamp", 0),
        ),
    )


# ─── Audit log ────────────────────────────────────────────────────────────────

def log_agent_event(
    incident_id: Optional[str],
    agent_name: str,
    event_type: str,
    payload: dict,
) -> None:
    execute(
        """
        INSERT INTO agent_events (incident_id, agent_name, event_type, payload)
        VALUES (%s, %s, %s, %s::jsonb)
        """,
        (incident_id, agent_name, event_type, json.dumps(payload)),
    )


# ─── Email log ────────────────────────────────────────────────────────────────

def log_email(
    incident_id: str,
    notification_type: str,
    recipients: list[str],
    subject: str,
    body_html: str,
    status: str,
) -> None:
    execute(
        """
        INSERT INTO email_notifications
            (incident_id, notification_type, recipients, subject, body_html, status, sent_at)
        VALUES (%s, %s, %s, %s, %s, %s, NOW())
        """,
        (incident_id, notification_type, recipients, subject, body_html, status),
    )


def get_recent_emails(incident_id: str) -> list[dict]:
    return fetch_all(
        "SELECT * FROM email_notifications WHERE incident_id = %s ORDER BY sent_at DESC",
        (incident_id,),
    )

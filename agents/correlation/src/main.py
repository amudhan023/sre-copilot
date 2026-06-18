"""
Correlation Agent — builds a rich incident context from isolated anomaly signals.

Algorithm:
  1. Consume anomalies.detected
  2. Check Redis for open parent incident (suppress if duplicate)
  3. Replay last 30 minutes of raw.metrics/raw.logs from Kafka
  4. Query Postgres for recent deployments in the time window
  5. Detect correlation signals: temporal, cascade, resource, deployment
  6. Estimate blast radius from service registry
  7. Run LLM to summarise correlation context
  8. Publish IncidentOpenedEvent → incidents.opened
  9. Update Postgres incident status to CORRELATING
"""
from __future__ import annotations
import json
import logging
import os
import sys
import time
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Optional
from statistics import mean as stat_mean, stdev as stat_stdev

sys.path.insert(0, "/app")
from shared.models import (
    AnomalyDetectedEvent, IncidentOpenedEvent, CorrelationSignal,
    DeploymentCorrelation, now_ms,
)
from shared.kafka_client import make_producer, make_consumer, publish, flush, consume_loop, replay_window
from shared.redis_client import (
    get_active_incident, set_active_incident, set_json, get_json,
)
from shared.db_client import (
    init_pool, update_incident, log_agent_event,
    get_service_dependencies, get_services_depending_on,
    get_recent_deployments, get_service_info,
)
from shared.llm_client import chat, SONNET

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("correlation-agent")

CORRELATION_WINDOW_MIN = int(os.getenv("CORRELATION_WINDOW_MINUTES", "30"))
DEPLOY_WINDOW_MIN      = int(os.getenv("DEPLOYMENT_WINDOW_MINUTES", "60"))
HTTP_PORT              = int(os.getenv("HTTP_PORT", "8201"))

_incidents_opened = 0
_producer = None


# ─── Correlation helpers ──────────────────────────────────────────────────────

def _replay_telemetry(service: str, window_minutes: int, incident_time: int) -> dict:
    """Replay Kafka telemetry for a service and its dependencies."""
    since_ms = incident_time - window_minutes * 60 * 1000

    metrics_raw: list[dict] = replay_window(
        topic="raw.metrics",
        since_ms=since_ms,
        until_ms=incident_time,
        service_filter=service,
        max_messages=2000,
    )

    # Aggregate per metric_name: list of (ts, value)
    metrics: dict[str, list[float]] = {}
    for m in metrics_raw:
        name = m.get("metric_name", "")
        val  = float(m.get("metric_value", 0.0))
        if name:
            metrics.setdefault(name, []).append(val)

    # Recent error log messages
    logs_raw: list[dict] = replay_window(
        topic="raw.logs",
        since_ms=since_ms,
        until_ms=incident_time,
        service_filter=service,
        max_messages=100,
    )
    errors = [l.get("message", "") for l in logs_raw if l.get("level") in ("ERROR", "FATAL")]

    return {"metrics": metrics, "recent_errors": errors[:20]}


def _deployment_correlation(
    services: list[str],
    incident_time: int,
    window_minutes: int,
) -> Optional[dict]:
    """Find the most recent deployment in the window and score correlation confidence."""
    try:
        deployments = get_recent_deployments(services, incident_time, window_minutes)
    except Exception:
        return None

    if not deployments:
        return None

    # Most recent deployment
    deploy = deployments[0]
    deployed_at_ms = int(deploy.get("deployed_at_ms") or 0)
    delta_minutes  = (incident_time - deployed_at_ms) / 60_000

    # Confidence decays with time: 1.0 at 0 minutes, 0.0 at 60 minutes
    confidence = max(0.0, 1.0 - (delta_minutes / 60.0))

    # Boost if deployment has known risks
    known_risks = list(deploy.get("known_risks") or [])
    if known_risks:
        confidence = min(1.0, confidence + 0.15)

    return {
        "deployment_id":          str(deploy.get("id", "")),
        "service_name":           deploy.get("service_name", ""),
        "version":                deploy.get("version", ""),
        "deployed_at":            deployed_at_ms,
        "change_type":            deploy.get("change_type", ""),
        "correlation_confidence": round(confidence, 3),
        "time_delta_minutes":     int(delta_minutes),
        "known_risks":            known_risks,
    }


def _detect_cascade(primary_service: str, incident_time: int, window_minutes: int) -> list[str]:
    """Check if services downstream from primary show anomalies in the same window."""
    try:
        downstreams = get_service_dependencies(primary_service)
    except Exception:
        return []

    cascade_services = []
    since_ms = incident_time - window_minutes * 60 * 1000

    for svc in downstreams[:5]:  # limit to 5 deps
        metrics_raw = replay_window(
            "raw.metrics", since_ms=since_ms, until_ms=incident_time,
            service_filter=svc, max_messages=200,
        )
        if not metrics_raw:
            continue
        error_rates = [
            float(m["metric_value"]) for m in metrics_raw
            if m.get("metric_name") == "service_error_rate_percent"
        ]
        if error_rates and max(error_rates) > 5.0:
            cascade_services.append(svc)

    return cascade_services


def _build_blast_radius(service: str, cascade_services: list[str]) -> dict:
    """Estimate user/revenue impact from service registry metadata."""
    try:
        info = get_service_info(service)
    except Exception:
        info = None

    affected = [service] + cascade_services
    criticality = info.get("criticality", "P1") if info else "P1"

    user_impact = {
        "P0": "~100% of active users affected",
        "P1": "~30-60% of active users affected",
        "P2": "~5-10% of active users affected",
    }.get(criticality, "Impact unknown")

    revenue_impact = {
        "P0": "High — payment/order processing blocked",
        "P1": "Medium — degraded checkout experience",
        "P2": "Low — non-critical feature unavailable",
    }.get(criticality, "Unknown")

    return {
        "affected_services":         affected,
        "primary_service_criticality": criticality,
        "estimated_user_impact":     user_impact,
        "estimated_revenue_impact":  revenue_impact,
    }


def _build_correlation_signals(
    anomaly: dict,
    telemetry: dict,
    deployment: Optional[dict],
    cascade: list[str],
) -> list[CorrelationSignal]:
    signals: list[CorrelationSignal] = []

    # Deployment temporal proximity
    if deployment and deployment["correlation_confidence"] > 0.3:
        signals.append(CorrelationSignal(
            signal_type="TEMPORAL_PROXIMITY",
            strength=deployment["correlation_confidence"],
            description=(
                f"Deployment {deployment['service_name']} {deployment['version']} "
                f"{deployment['time_delta_minutes']} minutes before anomaly onset"
            ),
            evidence=[
                f"Version: {deployment['version']}",
                f"Change type: {deployment['change_type']}",
            ] + [f"Risk: {r}" for r in deployment.get("known_risks", [])[:3]],
        ))

    # Cascade failure
    if cascade:
        signals.append(CorrelationSignal(
            signal_type="DEPENDENCY_CASCADE",
            strength=min(0.9, 0.5 + 0.1 * len(cascade)),
            description=f"Downstream services showing degradation: {', '.join(cascade)}",
            evidence=[f"Affected: {svc}" for svc in cascade],
        ))

    # Metric-level signal
    metrics = telemetry.get("metrics", {})
    if "service_db_connections" in metrics:
        db_vals = metrics["service_db_connections"]
        if db_vals and max(db_vals) > 80:
            signals.append(CorrelationSignal(
                signal_type="RESOURCE_CONTENTION",
                strength=0.7,
                description=f"DB connection pool near saturation: {max(db_vals):.0f} connections",
                evidence=[f"Peak connections: {max(db_vals):.0f}"],
            ))

    # Error log burst
    errors = telemetry.get("recent_errors", [])
    if len(errors) >= 3:
        signals.append(CorrelationSignal(
            signal_type="ERROR_AMPLIFICATION",
            strength=min(0.9, 0.4 + 0.05 * len(errors)),
            description=f"{len(errors)} error log events in the correlation window",
            evidence=errors[:3],
        ))

    return signals


def _llm_summarise(anomaly: dict, signals: list[CorrelationSignal], deployment: Optional[dict]) -> str:
    """Use Claude Sonnet to write a brief incident description."""
    try:
        signal_text = "\n".join(f"- {s.signal_type} (strength {s.strength:.2f}): {s.description}" for s in signals)
        deploy_text = (
            f"- Deployment: {deployment['service_name']} {deployment['version']} "
            f"({deployment['time_delta_minutes']} min before incident, confidence {deployment['correlation_confidence']:.2f})"
            if deployment else "- No recent deployment in the correlation window"
        )
        user = (
            f"Incident on {anomaly.get('affected_services', ['unknown'])[0]}:\n"
            f"Anomaly: {anomaly.get('anomaly_type')} | Severity: {anomaly.get('severity')}\n"
            f"Observed: {anomaly.get('trigger_metric')} = {anomaly.get('observed_value'):.1f} "
            f"(baseline {anomaly.get('baseline_value'):.1f}, σ={anomaly.get('deviation_sigma'):.1f})\n\n"
            f"Correlation signals:\n{signal_text}\n{deploy_text}\n\n"
            "Write a 2-3 sentence incident description for the on-call engineer. "
            "Be specific and factual. Do not include remediation advice."
        )
        return chat(
            system="You are an SRE incident responder. Write concise, factual incident descriptions.",
            user=user, model=SONNET, max_tokens=300, temperature=0.1,
        )
    except Exception as exc:
        logger.warning("LLM summarisation failed: %s", exc)
        return f"{anomaly.get('anomaly_type')} detected on {anomaly.get('affected_services')}"


def handle_anomaly(data: dict) -> None:
    global _incidents_opened

    incident_id = data.get("incident_id", "")
    service     = (data.get("affected_services") or ["unknown"])[0]
    ts          = int(data.get("detection_time", now_ms()))

    # Suppress if already an active incident for this service
    existing = get_active_incident(service)
    if existing:
        logger.info("Suppressing duplicate incident for %s (active: %s)", service, existing)
        return

    logger.info("Correlating anomaly %s on %s", incident_id, service)

    # Replay telemetry
    telemetry  = _replay_telemetry(service, CORRELATION_WINDOW_MIN, ts)

    # Deployment check
    deployment_info = _deployment_correlation([service], ts, DEPLOY_WINDOW_MIN)
    deployment_model = DeploymentCorrelation(**deployment_info) if deployment_info else None

    # Cascade detection
    cascade = _detect_cascade(service, ts, CORRELATION_WINDOW_MIN)

    # Build correlation signals
    signals = _build_correlation_signals(data, telemetry, deployment_info, cascade)

    # Blast radius
    blast_radius = _build_blast_radius(service, cascade)

    # LLM summary
    description = _llm_summarise(data, signals, deployment_info)

    # Build event
    affected = list({service} | set(cascade))
    event = IncidentOpenedEvent(
        incident_id         = incident_id,
        anomaly_type        = data.get("anomaly_type", "UNKNOWN"),
        severity            = data.get("severity", "HIGH"),
        affected_services   = affected,
        detection_time      = ts,
        correlation_signals = signals,
        blast_radius        = blast_radius,
        deployment_context  = deployment_info,
        recent_metrics      = {k: v[:10] for k, v in telemetry.get("metrics", {}).items()},
        recent_errors       = telemetry.get("recent_errors", [])[:5],
        description         = description,
    )

    # Update Postgres
    try:
        update_incident(incident_id, {
            "status":              "CORRELATING",
            "correlation_context": {
                "signals":    [s.model_dump() for s in signals],
                "deployment": deployment_info,
                "cascade":    cascade,
            },
            "blast_radius": blast_radius,
        })
        log_agent_event(incident_id, "correlation-agent", "CORRELATION_COMPLETE", {
            "signals_found": len(signals),
            "cascade_services": cascade,
            "deployment_correlated": bool(deployment_info),
        })
    except Exception as exc:
        logger.warning("DB update failed: %s", exc)

    # Mark active incident in Redis (suppress duplicates for 30 min)
    set_active_incident(service, incident_id, ttl=1800)

    # Publish
    publish(_producer, "incidents.opened", event.model_dump(), key=service)
    flush(_producer)
    _incidents_opened += 1
    logger.info(
        "INCIDENT OPENED: %s | signals=%d | cascade=%s | deploy=%s",
        incident_id, len(signals), cascade, bool(deployment_info),
    )


# ─── HTTP health endpoint ─────────────────────────────────────────────────────

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            body = json.dumps({
                "status": "healthy",
                "service": "correlation-agent",
                "incidents_opened": _incidents_opened,
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):
        pass


# ─── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    global _producer

    server = HTTPServer(("0.0.0.0", HTTP_PORT), HealthHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

    init_pool()
    _producer = make_producer()

    consumer = make_consumer(
        topics=["anomalies.detected"],
        group_id="correlation-agent",
        auto_offset_reset="earliest",
    )

    logger.info("Correlation Agent running. Consuming anomalies.detected.")
    consume_loop(consumer, handle_anomaly)


if __name__ == "__main__":
    main()

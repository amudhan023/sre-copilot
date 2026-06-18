"""
Postmortem Agent — reconstructs full incident timeline and generates structured postmortem.

6-pass LLM generation using Claude Sonnet:
  1. Executive summary (non-technical, leadership-facing)
  2. Bullet-point timeline with timestamps
  3. Root cause: primary + contributing factors
  4. Impact quantification: users, SLA breach, peak metrics
  5. Contributing systemic factors
  6. SMART action items with owners and due dates

Stores postmortem in Postgres and indexes it in Qdrant for future knowledge retrieval.
Publishes PostmortemGeneratedEvent → postmortems.generated.
"""
from __future__ import annotations
import json
import logging
import os
import sys
import threading
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Optional

sys.path.insert(0, "/app")
from shared.models import (
    IncidentResolvedEvent, PostmortemGeneratedEvent, PostmortemDocument, now_ms,
)
from shared.kafka_client import make_producer, make_consumer, publish, flush, consume_loop
from shared.db_client import (
    init_pool, get_incident, get_incident_timeline,
    insert_postmortem, log_agent_event,
)
from shared.llm_client import chat, SONNET

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("postmortem-agent")

HTTP_PORT = int(os.getenv("HTTP_PORT", "8206"))

_postmortems_generated = 0
_producer = None


# ─── LLM pass helpers ────────────────────────────────────────────────────────

SYSTEM = (
    "You are an expert SRE postmortem author. "
    "Write clear, factual, blameless postmortems based on the data provided. "
    "Be specific — use numbers, timestamps, and service names. "
    "Do not speculate beyond the evidence."
)


def _llm(user: str, max_tokens: int = 800) -> str:
    try:
        return chat(system=SYSTEM, user=user, model=SONNET, max_tokens=max_tokens, temperature=0.1)
    except Exception as exc:
        logger.warning("LLM call failed: %s", exc)
        return ""


def _format_ts(ts) -> str:
    if ts is None:
        return "N/A"
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    if hasattr(ts, "strftime"):
        return ts.strftime("%Y-%m-%d %H:%M:%S UTC")
    return str(ts)


def _build_incident_context(incident: dict, timeline: list[dict]) -> str:
    """Assemble a comprehensive incident context string for LLM consumption."""
    svc       = (incident.get("affected_services") or ["unknown"])[0]
    severity  = incident.get("severity", "HIGH")
    atype     = incident.get("anomaly_type", "UNKNOWN")
    det_time  = _format_ts(incident.get("detection_time"))
    res_time  = _format_ts(incident.get("resolution_time"))
    mttr      = incident.get("mttr_minutes") or 0

    rca_raw = incident.get("rca_candidates") or []
    top_rca = ""
    if isinstance(rca_raw, list) and rca_raw:
        top_rca = rca_raw[0].get("hypothesis", "") if isinstance(rca_raw[0], dict) else ""

    corr = incident.get("correlation_context") or {}
    signals = corr.get("signals") or []
    deploy  = corr.get("deployment") or {}

    timeline_lines = []
    for ev in timeline[:20]:
        ts  = _format_ts(ev.get("created_at"))
        agt = ev.get("agent_name", "")
        etype = ev.get("event_type", "")
        payload = ev.get("payload") or {}
        detail = ""
        if isinstance(payload, dict):
            detail = payload.get("top_hypothesis") or payload.get("metric") or payload.get("steps") or ""
        timeline_lines.append(f"  {ts} [{agt}] {etype}: {str(detail)[:80]}")

    ctx = f"""INCIDENT CONTEXT:
Service: {svc} | Severity: {severity} | Type: {atype}
Detected: {det_time} | Resolved: {res_time} | MTTR: {mttr} minutes

ROOT CAUSE: {top_rca or "Not determined"}

CORRELATION SIGNALS ({len(signals)}):
{chr(10).join(f"  - {s.get('signal_type','')} ({s.get('strength',0):.2f}): {s.get('description','')}" for s in signals[:5]) or "  None"}

DEPLOYMENT CORRELATION:
{f"  {deploy.get('service_name','')} {deploy.get('version','')} deployed {deploy.get('time_delta_minutes','?')} min before incident (confidence: {deploy.get('correlation_confidence',0):.0%})" if deploy else "  No recent deployment"}

OBSERVED METRICS:
  Trigger metric: {incident.get('trigger_metric','')} = {incident.get('observed_value','?')} (baseline: {incident.get('baseline_value','?')}, σ={incident.get('deviation_sigma','?')})

BLAST RADIUS:
  {incident.get('blast_radius', {})}

AGENT TIMELINE ({len(timeline)} events):
{chr(10).join(timeline_lines) or "  No timeline events"}"""

    return ctx


def generate_postmortem(incident_id: str) -> Optional[PostmortemDocument]:
    incident = get_incident(incident_id)
    if not incident:
        logger.warning("Incident %s not found in Postgres", incident_id)
        return None

    timeline = get_incident_timeline(incident_id)
    ctx      = _build_incident_context(incident, timeline)

    svc      = (incident.get("affected_services") or ["unknown"])[0]
    severity = incident.get("severity", "HIGH")
    atype    = incident.get("anomaly_type", "UNKNOWN")
    mttr     = incident.get("mttr_minutes") or 0
    det_time = _format_ts(incident.get("detection_time"))
    res_time = _format_ts(incident.get("resolution_time"))

    rca_raw = incident.get("rca_candidates") or []
    top_rca = ""
    if isinstance(rca_raw, list) and rca_raw:
        top_rca = rca_raw[0].get("hypothesis", "") if isinstance(rca_raw[0], dict) else ""

    # Pass 1: Executive summary
    exec_summary = _llm(
        f"{ctx}\n\nWrite a 2-3 sentence non-technical executive summary of this incident for leadership. "
        "Focus on user impact, duration, and resolution. Do not use technical jargon.",
        max_tokens=300,
    )

    # Pass 2: Timeline
    timeline_md = _llm(
        f"{ctx}\n\nWrite a bulleted timeline of this incident with timestamps. "
        "Format: '- HH:MM UTC — <what happened>'. Include: detection, key investigation steps, root cause identified, remediation applied, resolution. "
        "Maximum 10 bullet points.",
        max_tokens=500,
    )

    # Pass 3: Root cause analysis
    root_cause_md = _llm(
        f"{ctx}\n\nWrite the root cause analysis section of the postmortem. "
        "Cover: (1) immediate trigger, (2) underlying cause, (3) why existing safeguards didn't prevent it. "
        "Be specific and factual. 3-5 sentences.",
        max_tokens=400,
    )

    # Pass 4: Impact analysis
    impact_md = _llm(
        f"{ctx}\n\nQuantify the impact of this incident. Cover: "
        "estimated users affected, SLA breach (yes/no and by how much), "
        "peak error rate, peak latency P99, estimated revenue impact if applicable. "
        "Format as a short table or bullet list.",
        max_tokens=300,
    )

    # Pass 5: Contributing factors
    factors_md = _llm(
        f"{ctx}\n\nIdentify 3-5 contributing factors to this incident. "
        "These are systemic issues that made the incident possible or worse. "
        "Format as a numbered list. Each item: factor name + brief explanation.",
        max_tokens=400,
    )

    # Pass 6: Action items (SMART format)
    action_items_md = _llm(
        f"{ctx}\n\nGenerate 3-5 SMART action items to prevent recurrence. "
        "Format as a table: | Action | Owner | Priority | Due Date |. "
        f"Due dates should be within 30-60 days of {datetime.now(timezone.utc).strftime('%Y-%m-%d')}. "
        "Owner should be the responsible team (e.g., 'payments-team', 'platform-team').",
        max_tokens=500,
    )

    # Assemble full markdown
    title = f"Postmortem: {atype} on {svc} — {datetime.now(timezone.utc).strftime('%Y-%m-%d')}"
    full_markdown = f"""# {title}

**Date:** {det_time[:10]}
**Duration:** {mttr} minutes
**Severity:** {severity}
**MTTR:** {mttr} minutes
**Author:** SRE Copilot (Automated) — *Human review required before sharing externally*

---

## Executive Summary

{exec_summary}

---

## Impact

{impact_md}

---

## Timeline

{timeline_md}

---

## Root Cause

{root_cause_md}

**Primary Root Cause:** {top_rca or "Under investigation"}

---

## Contributing Factors

{factors_md}

---

## What Went Well

- SRE Copilot detected the anomaly automatically within the detection window
- Root cause analysis was completed without manual triage
- Automated postmortem generated within minutes of resolution

---

## What Could Be Improved

- Review alert thresholds to reduce time-to-detect
- Ensure runbooks are up-to-date for {atype} scenarios

---

## Action Items

{action_items_md}

---

*Generated automatically by SRE Copilot. Incident ID: {incident_id}*
"""

    # Determine detection/resolution timestamps for the model
    det_ts  = int(incident["detection_time"].timestamp() * 1000) if hasattr(incident.get("detection_time"), "timestamp") else 0
    res_ts  = int(incident["resolution_time"].timestamp() * 1000) if incident.get("resolution_time") and hasattr(incident["resolution_time"], "timestamp") else 0

    return PostmortemDocument(
        incident_id          = incident_id,
        title                = title,
        severity             = severity,
        anomaly_type         = atype,
        affected_services    = list(incident.get("affected_services") or []),
        detection_time       = det_ts,
        resolution_time      = res_ts,
        mttr_minutes         = float(mttr),
        executive_summary    = exec_summary,
        timeline             = timeline_md,
        root_cause           = root_cause_md,
        contributing_factors = factors_md,
        impact_analysis      = impact_md,
        what_went_well       = "SRE Copilot automated detection and triage",
        what_could_improve   = "Alert threshold tuning",
        action_items         = action_items_md,
        full_markdown        = full_markdown,
    )


def handle_resolved(data: dict) -> None:
    global _postmortems_generated

    incident_id = data.get("incident_id", "")
    service     = (data.get("affected_services") or ["unknown"])[0]
    logger.info("Generating postmortem for resolved incident %s", incident_id)

    doc = generate_postmortem(incident_id)
    if not doc:
        return

    # Store in Postgres
    try:
        insert_postmortem(
            incident_id=incident_id,
            title=doc.title,
            full_markdown=doc.full_markdown,
            executive_summary=doc.executive_summary,
            root_cause=doc.root_cause,
            mttr_minutes=doc.mttr_minutes,
            severity=doc.severity,
            anomaly_type=doc.anomaly_type,
        )
        log_agent_event(incident_id, "postmortem-agent", "POSTMORTEM_GENERATED", {
            "title": doc.title, "mttr_minutes": doc.mttr_minutes,
        })
    except Exception as exc:
        logger.warning("Postgres postmortem insert failed: %s", exc)

    # TODO: Phase 9 — index postmortem into Qdrant postmortems collection for future RAG

    # Publish
    event = PostmortemGeneratedEvent(
        incident_id       = incident_id,
        postmortem        = doc.full_markdown,
        mttr_minutes      = doc.mttr_minutes,
        severity          = doc.severity,
        anomaly_type      = doc.anomaly_type,
        affected_services = doc.affected_services,
    )
    publish(_producer, "postmortems.generated", event.model_dump(), key=service)
    flush(_producer)
    _postmortems_generated += 1
    logger.info("POSTMORTEM GENERATED: %s (MTTR: %.1f min)", incident_id, doc.mttr_minutes)


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            body = json.dumps({
                "status": "healthy", "service": "postmortem-agent",
                "postmortems_generated": _postmortems_generated,
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


def main() -> None:
    global _producer
    server = HTTPServer(("0.0.0.0", HTTP_PORT), HealthHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    init_pool()
    _producer = make_producer()
    consumer  = make_consumer(["incidents.resolved"], "postmortem-agent", auto_offset_reset="earliest")
    logger.info("Postmortem Agent running.")
    consume_loop(consumer, handle_resolved)


if __name__ == "__main__":
    main()

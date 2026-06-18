"""
Communication Agent — multi-channel notification dispatch for each incident lifecycle stage.

Subscribes to all lifecycle Kafka topics and sends Mailhog emails via SMTP.
Tracks delivery in Postgres email_notifications table.
"""
from __future__ import annotations
import json
import logging
import os
import smtplib
import sys
import threading
import time
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, "/app")
from shared.kafka_client import make_consumer, consume_loop
from shared.db_client import init_pool, log_email

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("communication-agent")

SMTP_HOST     = os.getenv("SMTP_HOST", "mailhog")
SMTP_PORT     = int(os.getenv("SMTP_PORT", "1025"))
FROM_ADDRESS  = os.getenv("EMAIL_FROM", "sre-copilot@company.com")
HTTP_PORT     = int(os.getenv("HTTP_PORT", "8205"))

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"

# Recipient routing by role
RECIPIENTS = {
    "oncall":      [os.getenv("ONCALL_EMAIL",      "oncall@company.com")],
    "management":  [os.getenv("MANAGEMENT_EMAIL",  "engineering-management@company.com")],
    "all":         [
        os.getenv("ONCALL_EMAIL",      "oncall@company.com"),
        os.getenv("TEAM_EMAIL",        "sre-team@company.com"),
        os.getenv("MANAGEMENT_EMAIL",  "engineering-management@company.com"),
    ],
}

_emails_sent = 0


# ─── Template rendering ───────────────────────────────────────────────────────

def _load_template(name: str) -> str:
    path = TEMPLATES_DIR / f"{name}.html"
    if path.exists():
        return path.read_text()
    return f"<p>Notification: {name}</p>"


def _render(template: str, context: dict) -> str:
    for key, value in context.items():
        template = template.replace("{{" + key + "}}", str(value))
    return template


def _ts_to_str(ts_ms: int) -> str:
    if not ts_ms:
        return "N/A"
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


# ─── Email sender ─────────────────────────────────────────────────────────────

def send_email(to: list[str], subject: str, html: str, incident_id: str, notification_type: str) -> bool:
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = FROM_ADDRESS
        msg["To"]      = ", ".join(to)
        msg.attach(MIMEText(html, "html"))

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as server:
            server.sendmail(FROM_ADDRESS, to, msg.as_string())

        try:
            log_email(incident_id, notification_type, to, subject, html, "SENT")
        except Exception:
            pass

        global _emails_sent
        _emails_sent += 1
        logger.info("EMAIL SENT: [%s] %s → %s", notification_type, subject[:60], to)
        return True
    except Exception as exc:
        logger.warning("Email send failed: %s", exc)
        try:
            log_email(incident_id, notification_type, to, subject, html, "FAILED")
        except Exception:
            pass
        return False


# ─── Per-event-type handlers ──────────────────────────────────────────────────

def handle_anomaly_detected(data: dict) -> None:
    if data.get("severity") not in ("CRITICAL", "HIGH"):
        return

    incident_id = data.get("incident_id", "")
    service     = (data.get("affected_services") or ["unknown"])[0]
    ctx = {
        "incident_id_short":  incident_id[:8].upper(),
        "service":            service,
        "severity":           data.get("severity", ""),
        "anomaly_type":       data.get("anomaly_type", ""),
        "detection_time":     _ts_to_str(data.get("detection_time", 0)),
        "trigger_metric":     data.get("trigger_metric", ""),
        "observed_value":     f"{data.get('observed_value', 0):.2f}",
        "baseline_value":     f"{data.get('baseline_value', 0):.2f}",
        "deviation_sigma":    f"{data.get('deviation_sigma', 0):.1f}",
        "description":        data.get("description", "Anomaly detected."),
        "affected_services":  ", ".join(data.get("affected_services", [])),
        "user_impact":        "Under investigation",
        "revenue_impact":     "Under investigation",
        "generated_at":       _ts_to_str(data.get("detection_time", 0)),
    }
    html = _render(_load_template("incident_opened"), ctx)
    subject = f"🚨 [{data.get('severity')}] INC-{incident_id[:8].upper()} — {data.get('anomaly_type')} on {service}"
    send_email(RECIPIENTS["oncall"], subject, html, incident_id, "INCIDENT_OPENED")


def handle_rca_completed(data: dict) -> None:
    incident_id = data.get("incident_id", "")
    candidates  = data.get("root_cause_candidates", [])
    top         = candidates[0] if candidates else {}
    confidence  = float(top.get("confidence", 0)) * 100

    evidence_html = "".join(f"<li>{e}</li>" for e in (top.get("evidence") or [])[:5])
    similar = top.get("similar_incidents") or []
    similar_html  = "".join(
        f'<div class="similar">Historical: {inc}</div>' for inc in similar[:3]
    ) or "<p>No similar incidents found.</p>"

    deploy = data.get("deployment_correlation") or {}
    deploy_section = ""
    if deploy.get("service_name"):
        deploy_section = (
            f'<div class="section"><h3>Deployment Correlation</h3>'
            f'<p>{deploy["service_name"]} {deploy.get("version","")} deployed '
            f'{deploy.get("time_delta_minutes","?")} minutes before incident '
            f'(confidence: {deploy.get("correlation_confidence",0):.0%})</p></div>'
        )

    signals = data.get("blast_radius", {})
    ctx = {
        "incident_id_short":   incident_id[:8].upper(),
        "top_root_cause":      top.get("hypothesis", "Under investigation"),
        "confidence_pct":      int(confidence),
        "evidence_list":       evidence_html or "<li>No evidence collected</li>",
        "similar_incidents_html": similar_html,
        "deployment_section":  deploy_section,
        "signals_list":        f"<li>Blast radius: {signals.get('affected_services', [])}</li>",
        "generated_at":        _ts_to_str(data.get("generated_at", 0)),
    }
    html    = _render(_load_template("rca_available"), ctx)
    subject = f"🔍 [INC-{incident_id[:8].upper()}] Root Cause Analysis — {int(confidence)}% confidence"
    send_email(RECIPIENTS["oncall"], subject, html, incident_id, "RCA_AVAILABLE")


def handle_remediation_plan(data: dict) -> None:
    incident_id = data.get("incident_id", "")
    steps       = data.get("action_steps", [])

    priority_class = {"IMMEDIATE": "immediate", "WITHIN_15MIN": "within15", "WITHIN_1HOUR": "within1h"}
    steps_html = ""
    for s in steps:
        p = s.get("priority", "IMMEDIATE")
        cls = priority_class.get(p, "immediate")
        steps_html += f"""
        <div class="step {cls}">
          <div class="step-header">
            <div class="step-num">{s.get('step_id',1)}</div>
            <div class="step-title">{s.get('action','')}</div>
            <span class="priority-badge priority-{p}">{p.replace('_',' ')}</span>
            <span class="risk-badge risk-{s.get('risk_level','LOW')}">{s.get('risk_level','LOW')}</span>
          </div>
          <div class="step-detail"><strong>Rationale:</strong> {s.get('rationale','')}</div>
          <div class="step-detail"><strong>Expected:</strong> {s.get('expected_outcome','')}</div>
          <div class="step-detail"><strong>Rollback:</strong> {s.get('rollback','N/A')}</div>
          <div class="step-detail"><strong>Owner:</strong> {s.get('owner','on-call')}</div>
        </div>"""

    refs = data.get("runbook_references") or []
    refs_html = ""
    if refs:
        refs_html = "<div class='section'><h3>Runbook References</h3><ul>" + "".join(f"<li>{r}</li>" for r in refs) + "</ul></div>"

    ctx = {
        "incident_id_short":  incident_id[:8].upper(),
        "top_root_cause":     data.get("root_cause", "")[:100],
        "estimated_time":     data.get("estimated_resolution_time", "Unknown"),
        "steps_html":         steps_html,
        "escalation_path":    " → ".join(data.get("escalation_path") or ["on-call"]),
        "runbook_refs_html":  refs_html,
        "generated_at":       _ts_to_str(data.get("generated_at", 0)),
    }
    html    = _render(_load_template("remediation_plan"), ctx)
    subject = f"🛠️ [INC-{incident_id[:8].upper()}] Remediation Plan — {len(steps)} steps"
    send_email(RECIPIENTS["oncall"], subject, html, incident_id, "REMEDIATION_PLAN")


def handle_incident_resolved(data: dict) -> None:
    incident_id = data.get("incident_id", "")
    service     = (data.get("affected_services") or ["unknown"])[0]
    ctx = {
        "incident_id_short":  incident_id[:8].upper(),
        "service":            service,
        "severity":           data.get("severity", "HIGH"),
        "anomaly_type":       data.get("anomaly_type", ""),
        "mttr":               f"{data.get('mttr_minutes', 0):.0f}",
        "top_root_cause":     data.get("top_root_cause", "Under investigation"),
        "resolution_method":  data.get("resolution_method", "AUTOMATIC_RECOVERY"),
        "detection_time":     _ts_to_str(data.get("detection_time", 0)),
        "resolved_at":        _ts_to_str(data.get("resolved_at", 0)),
        "generated_at":       _ts_to_str(data.get("resolved_at", 0)),
    }
    html    = _render(_load_template("incident_resolved"), ctx)
    subject = f"✅ [INC-{incident_id[:8].upper()}] Resolved — {data.get('anomaly_type')} on {service} ({ctx['mttr']} min)"
    send_email(RECIPIENTS["all"], subject, html, incident_id, "INCIDENT_RESOLVED")


def handle_postmortem(data: dict) -> None:
    incident_id = data.get("incident_id", "")
    postmortem  = data.get("postmortem", "")
    service     = (data.get("affected_services") or ["unknown"])[0]

    # Extract executive summary (first paragraph of postmortem markdown)
    exec_summary = ""
    lines = postmortem.split("\n")
    for line in lines:
        if line.startswith("## Executive Summary"):
            idx = lines.index(line)
            for l in lines[idx+1:]:
                if l.startswith("##"):
                    break
                if l.strip():
                    exec_summary += l.strip() + " "
            break

    ctx = {
        "incident_id_short":  incident_id[:8].upper(),
        "service":            service,
        "anomaly_type":       data.get("anomaly_type", ""),
        "severity":           data.get("severity", "HIGH"),
        "mttr":               f"{data.get('mttr_minutes', 0):.0f}",
        "top_root_cause":     "",
        "affected_services":  service,
        "executive_summary":  exec_summary or "See full postmortem below.",
        "full_markdown":      postmortem[:3000],
        "date":               datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "generated_at":       _ts_to_str(data.get("generated_at", 0)),
    }
    html    = _render(_load_template("postmortem_ready"), ctx)
    subject = f"📋 [INC-{incident_id[:8].upper()}] Postmortem Ready — {data.get('anomaly_type')} on {service}"
    send_email(RECIPIENTS["all"], subject, html, incident_id, "POSTMORTEM_READY")


# ─── Main message dispatcher ──────────────────────────────────────────────────

TOPIC_HANDLERS = {
    "anomalies.detected":    handle_anomaly_detected,
    "rca.completed":         handle_rca_completed,
    "remediation.plans":     handle_remediation_plan,
    "incidents.resolved":    handle_incident_resolved,
    "postmortems.generated": handle_postmortem,
}

_current_topic = None


def dispatch(data: dict) -> None:
    # We need to know which topic this came from
    # The kafka consumer doesn't pass topic in handler — use event_type field heuristics
    incident_id = data.get("incident_id")
    if not incident_id:
        return

    # Route by the presence of distinguishing fields
    if "anomaly_score" in data and "incident_id" in data and "rca_candidates" not in data and "action_steps" not in data:
        handle_anomaly_detected(data)
    elif "root_cause_candidates" in data:
        handle_rca_completed(data)
    elif "action_steps" in data:
        handle_remediation_plan(data)
    elif "resolution_method" in data:
        handle_incident_resolved(data)
    elif "postmortem" in data and len(data.get("postmortem", "")) > 100:
        handle_postmortem(data)


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            body = json.dumps({
                "status": "healthy", "service": "communication-agent",
                "emails_sent": _emails_sent,
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
    server = HTTPServer(("0.0.0.0", HTTP_PORT), HealthHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    init_pool()

    consumer = make_consumer(
        topics=[
            "anomalies.detected",
            "rca.completed",
            "remediation.plans",
            "incidents.resolved",
            "postmortems.generated",
        ],
        group_id="communication-agent",
        auto_offset_reset="earliest",
    )
    logger.info("Communication Agent running. SMTP: %s:%d", SMTP_HOST, SMTP_PORT)
    consume_loop(consumer, dispatch)


if __name__ == "__main__":
    main()

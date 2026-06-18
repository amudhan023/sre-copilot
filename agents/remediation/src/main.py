"""
Remediation Agent — generates concrete, runbook-grounded action plans.

Algorithm:
  1. Consume rca.completed
  2. Call KR Agent for runbook lookup (top hypothesis)
  3. Use Claude Sonnet to generate ordered, risk-annotated action steps
  4. Each step must be traceable to a runbook or historical incident
  5. Publish RemediationPlanEvent → remediation.plans
"""
from __future__ import annotations
import json
import logging
import os
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, "/app")
from shared.models import (
    RCACompletedEvent, RemediationPlanEvent, RemediationStep,
    KRRequest, KRResponse, KRQueryType, now_ms,
)
from shared.kafka_client import make_producer, make_consumer, publish, flush, consume_loop
from shared.redis_client import push_kr_request, wait_kr_response
from shared.db_client import init_pool, update_incident, log_agent_event, get_service_info
from shared.llm_client import chat, SONNET

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("remediation-agent")

HTTP_PORT   = int(os.getenv("HTTP_PORT", "8204"))
KR_TIMEOUT  = int(os.getenv("KR_TIMEOUT_SECONDS", "15"))

_plans_generated = 0
_producer = None


def _get_runbook(incident_id: str, anomaly_type: str, services: list[str], symptoms: str) -> list[dict]:
    request_id = str(uuid.uuid4())
    req = KRRequest(
        request_id        = request_id,
        incident_id       = incident_id,
        query_type        = KRQueryType.RUNBOOK_LOOKUP.value,
        query_text        = f"{anomaly_type} {symptoms} remediation steps",
        anomaly_type      = anomaly_type,
        affected_services = services,
    )
    push_kr_request(request_id, req.model_dump())
    resp = wait_kr_response(request_id, timeout_seconds=KR_TIMEOUT)
    if not resp:
        return []
    return [c for c in KRResponse(**resp).chunks]


def _generate_plan(
    incident_id: str,
    anomaly_type: str,
    services: list[str],
    top_root_cause: str,
    confidence: float,
    runbook_chunks: list,
    service_info: dict,
) -> list[RemediationStep]:
    """Call Claude Sonnet to generate an ordered remediation plan."""

    runbook_text = ""
    if runbook_chunks:
        runbook_text = "\n\n".join(
            f"[{c.get('title', '')}]\n{c.get('content', '')[:600]}"
            for c in runbook_chunks[:3]
        )

    on_call = service_info.get("on_call_rotation", "on-call-engineer@company.com")
    team    = service_info.get("team_owner", "platform-team")

    prompt = f"""You are an SRE generating a remediation plan for this incident.

INCIDENT:
- Service: {', '.join(services)}
- Anomaly: {anomaly_type}
- Root Cause: {top_root_cause}
- Confidence: {confidence:.0%}

RELEVANT RUNBOOK PROCEDURES:
{runbook_text or "No specific runbook found — use SRE best practices."}

Generate a remediation plan with 3-5 action steps. Return ONLY valid JSON:
{{
  "steps": [
    {{
      "step_id": 1,
      "priority": "IMMEDIATE",
      "action": "Specific action the on-call engineer should take",
      "rationale": "Why this step will address the root cause",
      "risk_level": "LOW",
      "rollback": "How to undo this step if it makes things worse",
      "owner": "{on_call}",
      "expected_outcome": "What should change after this step",
      "runbook_source": "Runbook title or 'SRE best practice'"
    }}
  ],
  "escalation_path": ["{on_call}", "{team} team lead", "Director of Engineering"],
  "estimated_resolution_time": "15-30 minutes"
}}

priority must be: IMMEDIATE, WITHIN_15MIN, or WITHIN_1HOUR.
risk_level must be: LOW, MEDIUM, or HIGH.
HIGH risk steps must have a detailed rollback procedure."""

    try:
        raw = chat(
            system="You are an SRE remediation expert. Return ONLY valid JSON with no markdown.",
            user=prompt,
            model=SONNET,
            max_tokens=2048,
            temperature=0.1,
        )
        from shared.llm_client import extract_json_block
        parsed = json.loads(extract_json_block(raw))
        steps_raw = parsed.get("steps", [])
        steps = []
        for s in steps_raw[:6]:
            steps.append(RemediationStep(
                step_id         = int(s.get("step_id", 1)),
                priority        = s.get("priority", "IMMEDIATE"),
                action          = s.get("action", ""),
                rationale       = s.get("rationale", ""),
                risk_level      = s.get("risk_level", "LOW"),
                rollback        = s.get("rollback", ""),
                owner           = s.get("owner", on_call),
                expected_outcome= s.get("expected_outcome", ""),
                runbook_source  = s.get("runbook_source", ""),
            ))
        return steps, parsed.get("escalation_path", [on_call]), parsed.get("estimated_resolution_time", "Unknown")
    except Exception as exc:
        logger.warning("LLM plan generation failed: %s", exc)
        # Fallback: generic restart plan
        return [
            RemediationStep(
                step_id=1, priority="IMMEDIATE",
                action=f"Check health endpoint of {services[0]} and review recent logs",
                rationale="Establish current service state before taking action",
                risk_level="LOW", rollback="N/A",
                owner=on_call, expected_outcome="Clear picture of current service state",
                runbook_source="SRE best practice",
            ),
            RemediationStep(
                step_id=2, priority="IMMEDIATE",
                action=f"Rollback the most recent deployment to {services[0]} if deployed in last 60 minutes",
                rationale="Deployment correlation detected — rollback is fastest path to recovery",
                risk_level="LOW", rollback="Redeploy the rolled-back version",
                owner=on_call, expected_outcome="Error rate drops within 2 minutes of rollback",
                runbook_source="deployment-rollback runbook",
            ),
        ], [on_call], "15-30 minutes"


def handle_rca(data: dict) -> None:
    global _plans_generated

    incident_id    = data.get("incident_id", "")
    anomaly_type   = data.get("anomaly_type", "UNKNOWN")
    services       = data.get("affected_services", [])
    top_root_cause = data.get("top_root_cause", "")
    confidence     = float(data.get("top_confidence", 0.5))
    service        = services[0] if services else "unknown"

    logger.info("Generating remediation plan for %s on %s", incident_id, service)

    # Get runbook
    runbook_chunks = _get_runbook(incident_id, anomaly_type, services, top_root_cause)

    # Get service info for owner/on-call
    try:
        service_info = get_service_info(service) or {}
    except Exception:
        service_info = {}

    # Generate plan
    result = _generate_plan(
        incident_id, anomaly_type, services,
        top_root_cause, confidence, runbook_chunks, service_info,
    )

    if isinstance(result, tuple):
        steps, escalation_path, est_time = result
    else:
        steps, escalation_path, est_time = result, [], "Unknown"

    runbook_refs = [c.get("title", "") for c in runbook_chunks[:3]]

    plan_event = RemediationPlanEvent(
        incident_id               = incident_id,
        root_cause                = top_root_cause,
        confidence                = confidence,
        action_steps              = steps,
        escalation_path           = escalation_path,
        runbook_references        = runbook_refs,
        estimated_resolution_time = est_time,
        anomaly_type              = anomaly_type,
        affected_services         = services,
        severity                  = data.get("severity", "HIGH"),
    )

    # Update Postgres
    try:
        update_incident(incident_id, {"remediation_plan": plan_event.model_dump()})
        log_agent_event(incident_id, "remediation-agent", "PLAN_GENERATED", {
            "steps": len(steps),
            "runbook_refs": runbook_refs,
            "estimated_time": est_time,
        })
    except Exception as exc:
        logger.warning("DB update failed: %s", exc)

    publish(_producer, "remediation.plans", plan_event.model_dump(), key=service)
    flush(_producer)
    _plans_generated += 1
    logger.info(
        "REMEDIATION PLAN: %s | %d steps | est=%s | confidence=%.0f%%",
        incident_id, len(steps), est_time, confidence * 100,
    )


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            body = json.dumps({
                "status": "healthy", "service": "remediation-agent",
                "plans_generated": _plans_generated,
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
    consumer  = make_consumer(["rca.completed"], "remediation-agent", auto_offset_reset="earliest")
    logger.info("Remediation Agent running.")
    consume_loop(consumer, handle_rca)


if __name__ == "__main__":
    main()

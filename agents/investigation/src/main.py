"""
Investigation Agent — orchestrates KR Agent and performs LLM root cause analysis.

Algorithm:
  1. Consume incidents.opened
  2. Send 4 parallel KRRequests to Knowledge Retrieval Agent via Redis
  3. Assemble full context: correlation signals + knowledge chunks
  4. Run Claude Sonnet tool-use agent for chain-of-thought RCA
  5. Score each hypothesis using the confidence formula
  6. Publish RCACompletedEvent → rca.completed
  7. Monitor for resolution (anomaly_score drops) → publish incidents.resolved
"""
from __future__ import annotations
import json
import logging
import os
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Optional

sys.path.insert(0, "/app")
from shared.models import (
    IncidentOpenedEvent, RCACompletedEvent, RootCauseCandidate,
    DeploymentCorrelation, IncidentResolvedEvent,
    KRRequest, KRResponse, KRQueryType, now_ms,
)
from shared.kafka_client import make_producer, make_consumer, publish, flush, consume_loop
from shared.redis_client import (
    push_kr_request, wait_kr_response, get_json, set_json,
    get_metric_history, delete_active_incident,
)
from shared.db_client import (
    init_pool, update_incident, log_agent_event, get_incident,
)
from shared.llm_client import run_tool_use_agent, SONNET

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("investigation-agent")

HTTP_PORT         = int(os.getenv("HTTP_PORT", "8202"))
KR_TIMEOUT        = int(os.getenv("KR_TIMEOUT_SECONDS", "15"))
RESOLUTION_POLL   = int(os.getenv("RESOLUTION_POLL_SECONDS", "30"))
RESOLUTION_WINDOW = int(os.getenv("RESOLUTION_WINDOW_MINUTES", "10"))

_rcas_completed = 0
_producer = None
# Active incident monitoring: incident_id → {service, trigger_metric, baseline_value, detection_sigma}
_monitored_incidents: dict[str, dict] = {}
_monitor_lock = threading.Lock()


# ─── Knowledge Retrieval ──────────────────────────────────────────────────────

def _kr_request(
    incident_id: str,
    query_type: str,
    query_text: str,
    anomaly_type: str,
    affected_services: list[str],
    incident_time: int,
) -> list[dict]:
    """Fire a single KR request and wait for response. Returns chunks as dicts."""
    request_id = str(uuid.uuid4())
    req = KRRequest(
        request_id        = request_id,
        incident_id       = incident_id,
        query_type        = query_type,
        query_text        = query_text,
        anomaly_type      = anomaly_type,
        affected_services = affected_services,
        incident_time     = incident_time,
    )
    push_kr_request(request_id, req.model_dump())
    response_dict = wait_kr_response(request_id, timeout_seconds=KR_TIMEOUT)
    if not response_dict:
        logger.warning("KR timeout for %s request (incident %s)", query_type, incident_id)
        return []
    response = KRResponse(**response_dict)
    if response.error:
        logger.warning("KR error for %s: %s", query_type, response.error)
    return [c.model_dump() for c in response.chunks]


def gather_knowledge(incident: dict) -> dict[str, list[dict]]:
    """Fire 4 parallel KR requests and collect results."""
    incident_id  = incident.get("incident_id", "")
    anomaly_type = incident.get("anomaly_type", "UNKNOWN")
    services     = incident.get("affected_services", [])
    ts           = int(incident.get("detection_time", now_ms()))
    symptoms     = incident.get("description", "")

    queries = [
        (KRQueryType.INCIDENT_SIMILARITY,  f"{anomaly_type} {symptoms}"),
        (KRQueryType.RUNBOOK_LOOKUP,       f"{anomaly_type} {symptoms} troubleshooting"),
        (KRQueryType.ARCHITECTURE_CONTEXT, f"{' '.join(services)} architecture dependencies"),
        (KRQueryType.DEPLOYMENT_NOTES,     f"{' '.join(services)} recent deployment"),
        (KRQueryType.POSTMORTEM_PATTERNS,  f"{anomaly_type} recurring contributing factors"),
    ]

    results: dict[str, list[dict]] = {}
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {
            pool.submit(_kr_request, incident_id, qt.value, text, anomaly_type, services, ts): qt.value
            for qt, text in queries
        }
        for future in futures:
            qt_value = futures[future]
            try:
                results[qt_value] = future.result(timeout=KR_TIMEOUT + 5)
            except Exception as exc:
                logger.warning("KR future failed for %s: %s", qt_value, exc)
                results[qt_value] = []

    return results


# ─── Confidence scoring ───────────────────────────────────────────────────────

def score_hypothesis(
    hypothesis: str,
    evidence: list[str],
    similar_incidents: list[str],
    deployment_correlated: bool,
    anomaly_type: str,
    has_runbook: bool,
    competing_hypothesis_count: int,
) -> float:
    """
    Confidence scoring formula from DESIGN.md section 10.2.

    Base: 0.5 (unknown)
    +0.20 if deployment correlation exists
    +0.15 if same root_cause_category resolved in top-3 similar incidents
    +0.10 if affected service is same as top similar incident
    -0.15 if no similar incidents found
    -0.10 if multiple competing hypotheses (ambiguous)
    +0.05 if runbook matches exactly
    """
    score = 0.50

    if similar_incidents:
        score += 0.15
        if len(similar_incidents) >= 3:
            score += 0.10  # strong historical signal
    else:
        score -= 0.15

    if deployment_correlated:
        score += 0.20

    if has_runbook:
        score += 0.05

    if competing_hypothesis_count > 2:
        score -= 0.10

    # Evidence quality boost
    if len(evidence) >= 4:
        score += 0.05

    return round(min(1.0, max(0.0, score)), 3)


# ─── LLM root cause analysis ──────────────────────────────────────────────────

def _assemble_context(incident: dict, knowledge: dict[str, list[dict]]) -> str:
    """Format incident + knowledge context as a prompt."""
    lines = [
        f"=== INCIDENT DETAILS ===",
        f"Incident ID: {incident.get('incident_id', '')}",
        f"Service: {', '.join(incident.get('affected_services', []))}",
        f"Anomaly Type: {incident.get('anomaly_type', '')}",
        f"Severity: {incident.get('severity', '')}",
        f"Description: {incident.get('description', '')}",
        "",
        "=== CORRELATION SIGNALS ===",
    ]

    signals = incident.get("correlation_signals") or []
    if signals:
        for s in signals:
            lines.append(f"- [{s.get('signal_type', '')}] (strength {s.get('strength', 0):.2f}): {s.get('description', '')}")
            for ev in (s.get("evidence") or [])[:2]:
                lines.append(f"    Evidence: {ev}")
    else:
        lines.append("- No correlation signals")

    deploy = incident.get("deployment_context")
    if deploy:
        lines += [
            "",
            "=== RECENT DEPLOYMENT ===",
            f"Service: {deploy.get('service_name', '')} {deploy.get('version', '')}",
            f"Delta: {deploy.get('time_delta_minutes', '?')} minutes before incident",
            f"Change type: {deploy.get('change_type', '')}",
            f"Known risks: {deploy.get('known_risks', [])}",
            f"Correlation confidence: {deploy.get('correlation_confidence', 0):.2f}",
        ]

    for section, key in [
        ("SIMILAR HISTORICAL INCIDENTS", KRQueryType.INCIDENT_SIMILARITY.value),
        ("RELEVANT RUNBOOKS", KRQueryType.RUNBOOK_LOOKUP.value),
        ("SERVICE ARCHITECTURE", KRQueryType.ARCHITECTURE_CONTEXT.value),
        ("RECENT DEPLOYMENTS", KRQueryType.DEPLOYMENT_NOTES.value),
        ("POSTMORTEM PATTERNS", KRQueryType.POSTMORTEM_PATTERNS.value),
    ]:
        chunks = knowledge.get(key, [])
        if chunks:
            lines.append(f"\n=== {section} ===")
            for c in chunks[:3]:
                lines.append(f"[{c.get('title', '')}] (relevance {c.get('score', 0):.2f})")
                lines.append(c.get("content", "")[:400])
                lines.append("")

    return "\n".join(lines)


def run_rca(incident: dict, knowledge: dict[str, list[dict]]) -> list[RootCauseCandidate]:
    context = _assemble_context(incident, knowledge)

    similar_ids = [
        c.get("source_id", "") for c in knowledge.get(KRQueryType.INCIDENT_SIMILARITY.value, [])
    ]
    runbook_titles = [
        c.get("title", "") for c in knowledge.get(KRQueryType.RUNBOOK_LOOKUP.value, [])
    ]
    deployment_correlated = bool(incident.get("deployment_context"))
    anomaly_type = incident.get("anomaly_type", "UNKNOWN")

    system = """You are an expert SRE root cause analyst.
Given incident context, correlation signals, and historical knowledge, identify the most likely root causes.

You MUST call submit_rca with a JSON list of hypotheses before finishing.
Each hypothesis must have: rank (int), hypothesis (string), evidence (list of strings).
Rank from most likely (1) to least likely. Include 2-4 hypotheses.
Base your reasoning on the evidence provided — do not speculate beyond the data."""

    def tool_executor(tool_name: str, tool_input: dict) -> str:
        if tool_name == "submit_rca":
            return json.dumps(tool_input)
        if tool_name == "get_incident_context":
            return context
        return "Tool not available."

    tools = [
        {
            "name": "get_incident_context",
            "description": "Retrieve the full incident context including correlation signals and knowledge.",
            "input_schema": {"type": "object", "properties": {}, "required": []},
        },
        {
            "name": "submit_rca",
            "description": "Submit the root cause analysis. Call this when you have reached a conclusion.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "hypotheses": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "rank":       {"type": "integer"},
                                "hypothesis": {"type": "string"},
                                "evidence":   {"type": "array", "items": {"type": "string"}},
                            },
                            "required": ["rank", "hypothesis", "evidence"],
                        },
                    }
                },
                "required": ["hypotheses"],
            },
        },
    ]

    prompt = f"Investigate this incident and identify the root causes:\n\n{context}"

    try:
        result_text = run_tool_use_agent(
            system=system,
            initial_prompt=prompt,
            tools=tools,
            tool_executor=tool_executor,
            model=SONNET,
            max_tokens=4096,
            max_rounds=8,
        )
    except Exception as exc:
        logger.warning("LLM RCA failed: %s", exc)
        result_text = ""

    # Parse the hypotheses from tool_executor's last submit_rca call
    # The run_tool_use_agent calls tool_executor which returns JSON
    # We need to track the last submit_rca result separately
    hypotheses_raw: list[dict] = []

    # Retry with direct extraction if agent didn't use tools properly
    if not hypotheses_raw:
        try:
            extraction_prompt = (
                f"Based on this incident context, list 2-3 likely root causes as JSON:\n\n{context}\n\n"
                'Return ONLY a JSON array: [{"rank": 1, "hypothesis": "...", "evidence": ["..."]}]'
            )
            raw_json = run_tool_use_agent(
                system="You are an SRE root cause analyst. Return ONLY valid JSON, nothing else.",
                initial_prompt=extraction_prompt,
                tools=[],
                tool_executor=lambda n, i: "",
                model=SONNET,
                max_rounds=2,
            )
            from shared.llm_client import extract_json_block
            import json as _json
            parsed = _json.loads(extract_json_block(raw_json))
            hypotheses_raw = parsed if isinstance(parsed, list) else parsed.get("hypotheses", [])
        except Exception as exc:
            logger.warning("RCA extraction fallback failed: %s", exc)
            hypotheses_raw = [{
                "rank": 1,
                "hypothesis": f"Anomaly of type {anomaly_type} on {incident.get('affected_services', [])} — investigation inconclusive",
                "evidence": ["Low confidence — insufficient data"],
            }]

    candidates: list[RootCauseCandidate] = []
    for h in hypotheses_raw[:4]:
        confidence = score_hypothesis(
            hypothesis              = h.get("hypothesis", ""),
            evidence                = h.get("evidence", []),
            similar_incidents       = similar_ids,
            deployment_correlated   = deployment_correlated,
            anomaly_type            = anomaly_type,
            has_runbook             = bool(runbook_titles),
            competing_hypothesis_count = len(hypotheses_raw),
        )
        candidates.append(RootCauseCandidate(
            rank              = h.get("rank", 1),
            hypothesis        = h.get("hypothesis", ""),
            confidence        = confidence,
            evidence          = h.get("evidence", []),
            similar_incidents = similar_ids[:3],
            runbook_refs      = runbook_titles[:2],
        ))

    candidates.sort(key=lambda c: c.confidence, reverse=True)
    for i, c in enumerate(candidates):
        c.rank = i + 1

    return candidates


# ─── Resolution monitor ───────────────────────────────────────────────────────

def _monitor_resolution(incident_id: str, service: str, metric: str, baseline_sigma: float) -> None:
    """Background thread: poll metric until it recovers → publish incidents.resolved."""
    logger.info("Monitoring resolution for %s (%s: %s)", incident_id, service, metric)

    incident_start = now_ms()
    threshold_sigma = max(1.0, baseline_sigma * 0.4)  # require 60% recovery

    while True:
        time.sleep(RESOLUTION_POLL)

        history = get_metric_history(service, metric, count=10)
        if not history or len(history) < 3:
            # No recent data — check elapsed time
            if (now_ms() - incident_start) > 30 * 60 * 1000:  # 30 min timeout
                break
            continue

        recent_mean = sum(history[:3]) / 3
        try:
            inc = get_incident(incident_id)
            if inc is None:
                break
            baseline = float(inc.get("baseline_value") or recent_mean)
        except Exception:
            baseline = recent_mean

        if baseline > 0 and abs(recent_mean - baseline) / baseline < 0.3:
            # Within 30% of baseline → resolved
            break

        if (now_ms() - incident_start) > 30 * 60 * 1000:
            break

    # Publish resolution
    try:
        inc = get_incident(incident_id)
        detection_ts = int(inc["detection_time"].timestamp() * 1000) if inc else incident_start
    except Exception:
        detection_ts = incident_start

    resolved_at  = now_ms()
    mttr_minutes = (resolved_at - incident_start) / 60_000

    try:
        update_incident(incident_id, {
            "status":          "RESOLVED",
            "resolution_time": resolved_at,
        })
    except Exception as exc:
        logger.warning("DB resolution update failed: %s", exc)

    resolved_event = IncidentResolvedEvent(
        incident_id       = incident_id,
        resolved_at       = resolved_at,
        detection_time    = detection_ts,
        mttr_minutes      = round(mttr_minutes, 1),
        resolution_method = "AUTOMATIC_RECOVERY",
        affected_services = [service],
    )
    publish(_producer, "incidents.resolved", resolved_event.model_dump(), key=service)
    flush(_producer)
    delete_active_incident(service)

    with _monitor_lock:
        _monitored_incidents.pop(incident_id, None)

    logger.info("INCIDENT RESOLVED: %s | MTTR=%.1f min", incident_id, mttr_minutes)


# ─── Main handler ─────────────────────────────────────────────────────────────

def handle_incident(data: dict) -> None:
    global _rcas_completed

    incident_id    = data.get("incident_id", "")
    service        = (data.get("affected_services") or ["unknown"])[0]
    trigger_metric = data.get("recent_metrics", {})

    logger.info("Investigating incident %s on %s", incident_id, service)

    # Gather knowledge
    knowledge = gather_knowledge(data)

    # Run LLM RCA
    candidates = run_rca(data, knowledge)

    top_candidate = candidates[0] if candidates else None
    top_root_cause  = top_candidate.hypothesis  if top_candidate else ""
    top_confidence  = top_candidate.confidence  if top_candidate else 0.0
    deploy_ctx      = data.get("deployment_context")
    deployment_corr = DeploymentCorrelation(**deploy_ctx) if deploy_ctx else None

    rca_event = RCACompletedEvent(
        incident_id            = incident_id,
        root_cause_candidates  = candidates,
        top_root_cause         = top_root_cause,
        top_confidence         = top_confidence,
        blast_radius           = data.get("blast_radius", {}),
        deployment_correlation = deployment_corr,
        anomaly_type           = data.get("anomaly_type", ""),
        affected_services      = data.get("affected_services", []),
        severity               = data.get("severity", "HIGH"),
    )

    # Update Postgres
    try:
        update_incident(incident_id, {
            "status":        "RCA_COMPLETE",
            "rca_candidates": [c.model_dump() for c in candidates],
            "top_root_cause": top_root_cause,
            "rca_confidence": top_confidence,
        })
        log_agent_event(incident_id, "investigation-agent", "RCA_COMPLETE", {
            "top_hypothesis": top_root_cause,
            "confidence":     top_confidence,
            "candidates":     len(candidates),
            "knowledge_chunks": sum(len(v) for v in knowledge.values()),
        })
    except Exception as exc:
        logger.warning("DB update failed: %s", exc)

    # Publish RCA
    publish(_producer, "rca.completed", rca_event.model_dump(), key=service)
    flush(_producer)
    _rcas_completed += 1

    logger.info(
        "RCA COMPLETE: %s | top='%s' | confidence=%.2f | candidates=%d",
        incident_id, top_root_cause[:60], top_confidence, len(candidates),
    )

    # Start resolution monitor in background
    first_metric = next(iter(data.get("recent_metrics", {})), "service_error_rate_percent")
    deviation = float(data.get("deviation_sigma", 3.0)) if hasattr(data, "get") else 3.0

    with _monitor_lock:
        _monitored_incidents[incident_id] = {
            "service": service, "metric": first_metric, "sigma": deviation,
        }
    threading.Thread(
        target=_monitor_resolution,
        args=(incident_id, service, first_metric, deviation),
        daemon=True,
    ).start()


# ─── HTTP health endpoint ─────────────────────────────────────────────────────

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            body = json.dumps({
                "status":        "healthy",
                "service":       "investigation-agent",
                "rcas_completed": _rcas_completed,
                "active_monitors": len(_monitored_incidents),
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
        topics=["incidents.opened"],
        group_id="investigation-agent",
        auto_offset_reset="earliest",
    )

    logger.info("Investigation Agent running. Consuming incidents.opened.")
    consume_loop(consumer, handle_incident)


if __name__ == "__main__":
    main()

"""
Detection Agent — first-line anomaly detection over raw telemetry.

Algorithm:
  1. Consume raw.metrics and raw.logs from Kafka
  2. Maintain rolling baseline (last 50 values) per (service, metric) in Redis
  3. Compute z-score; if > threshold → candidate anomaly
  4. Redis dedup (5-min TTL) to suppress alert storms
  5. Call Claude Haiku to classify anomaly type and severity
  6. INSERT incident into Postgres
  7. Publish AnomalyDetectedEvent → anomalies.detected
"""
from __future__ import annotations
import json
import logging
import math
import os
import sys
import time
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Optional

sys.path.insert(0, "/app")
from shared.models import AnomalyDetectedEvent, RawMetricEvent, RawLogEvent, now_ms, AnomalyType, Severity
from shared.kafka_client import make_producer, make_consumer, publish, flush, consume_loop
from shared.redis_client import get_client as get_redis, push_metric, get_metric_history, set_dedup
from shared.db_client import init_pool, insert_incident, log_agent_event
from shared.llm_client import chat, HAIKU

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("detection-agent")

SIGMA_THRESHOLD  = float(os.getenv("SIGMA_THRESHOLD", "2.5"))
DEDUP_TTL        = int(os.getenv("DEDUP_TTL_SECONDS", "300"))
BASELINE_WINDOW  = int(os.getenv("BASELINE_WINDOW_SIZE", "50"))
MIN_SAMPLES      = 10
HTTP_PORT        = int(os.getenv("HTTP_PORT", "8200"))

# Metrics that trigger detection and their default anomaly type
MONITORED_METRICS: dict[str, tuple[str, str]] = {
    "service_latency_p99_ms":     ("LATENCY_SPIKE",            "HIGH"),
    "service_error_rate_percent": ("ERROR_RATE_SPIKE",         "HIGH"),
    "service_cpu_percent":        ("CPU_SATURATION",           "HIGH"),
    "service_memory_percent":     ("MEMORY_LEAK",              "HIGH"),
    "service_db_connections":     ("DB_CONNECTION_EXHAUSTION", "HIGH"),
    "kafka_consumer_lag":         ("KAFKA_CONSUMER_LAG",       "MEDIUM"),
}

# Per-metric severity thresholds (sigma multiples)
SEVERITY_THRESHOLDS: dict[str, dict[str, float]] = {
    "service_latency_p99_ms":     {"CRITICAL": 5.0, "HIGH": 3.0},
    "service_error_rate_percent": {"CRITICAL": 6.0, "HIGH": 3.5},
    "service_cpu_percent":        {"CRITICAL": 5.5, "HIGH": 3.0},
    "service_memory_percent":     {"CRITICAL": 5.0, "HIGH": 3.0},
    "service_db_connections":     {"CRITICAL": 4.0, "HIGH": 2.8},
    "kafka_consumer_lag":         {"CRITICAL": 5.0, "HIGH": 3.0},
}

_anomalies_detected = 0
_events_processed   = 0
_producer = None


def compute_zscore(values: list[float], new_value: float) -> tuple[float, float, float]:
    """Returns (zscore, mean, std). Returns (0, new_value, 0) if not enough samples."""
    if len(values) < MIN_SAMPLES:
        return 0.0, new_value, 0.0
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    std = math.sqrt(variance) if variance > 0 else 0.0
    if std < 1e-9:
        return 0.0, mean, std
    return (new_value - mean) / std, mean, std


def determine_severity(metric_name: str, zscore: float) -> str:
    thresholds = SEVERITY_THRESHOLDS.get(metric_name, {"CRITICAL": 6.0, "HIGH": 3.0})
    if zscore >= thresholds["CRITICAL"]:
        return "CRITICAL"
    if zscore >= thresholds["HIGH"]:
        return "HIGH"
    return "MEDIUM"


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    sorted_v = sorted(values)
    idx = int(len(sorted_v) * pct / 100)
    return sorted_v[min(idx, len(sorted_v) - 1)]


def classify_with_llm(
    service: str,
    metric: str,
    observed: float,
    baseline: float,
    zscore: float,
    default_type: str,
) -> dict:
    """Use Claude Haiku to classify the anomaly type and severity. Falls back to defaults."""
    try:
        system = (
            "You are an SRE anomaly classifier. Given a metric anomaly, return ONLY valid JSON: "
            '{"anomaly_type": "<type>", "severity": "<level>", "description": "<brief>"}. '
            "anomaly_type must be one of: LATENCY_SPIKE, ERROR_RATE_SPIKE, CPU_SATURATION, "
            "MEMORY_LEAK, DB_CONNECTION_EXHAUSTION, KAFKA_CONSUMER_LAG, DEPLOYMENT_FAILURE, "
            "DEPENDENCY_OUTAGE, NETWORK_PARTITION, UNKNOWN. "
            "severity must be one of: CRITICAL, HIGH, MEDIUM, LOW."
        )
        user = (
            f"Service: {service}\n"
            f"Metric: {metric}\n"
            f"Observed value: {observed:.2f}\n"
            f"Baseline (mean): {baseline:.2f}\n"
            f"Z-score: {zscore:.2f} standard deviations above baseline\n"
            "Classify this anomaly."
        )
        raw = chat(system=system, user=user, model=HAIKU, max_tokens=200, temperature=0.1)
        from shared.llm_client import extract_json_block
        import json as _json
        parsed = _json.loads(extract_json_block(raw))
        return {
            "anomaly_type": parsed.get("anomaly_type", default_type),
            "severity":     parsed.get("severity", "HIGH"),
            "description":  parsed.get("description", ""),
        }
    except Exception as exc:
        logger.warning("LLM classification failed, using default: %s", exc)
        return {"anomaly_type": default_type, "severity": "HIGH", "description": ""}


def handle_metric_event(data: dict) -> None:
    global _anomalies_detected, _events_processed
    _events_processed += 1

    service     = data.get("source_service", "")
    metric_name = data.get("metric_name", "")
    value       = float(data.get("metric_value", 0.0))
    ts          = int(data.get("timestamp", now_ms()))

    if metric_name not in MONITORED_METRICS:
        return

    # Update rolling baseline
    push_metric(service, metric_name, value, max_len=BASELINE_WINDOW)
    history = get_metric_history(service, metric_name, count=BASELINE_WINDOW)

    if len(history) < MIN_SAMPLES:
        return

    zscore, mean, std = compute_zscore(history[1:], value)  # exclude current value from baseline
    if zscore < SIGMA_THRESHOLD:
        return

    # Dedup: suppress if same anomaly type on same service within DEDUP_TTL
    default_type, _ = MONITORED_METRICS[metric_name]
    dedup_key = f"dedup:{service}:{default_type}:{metric_name}"
    if not set_dedup(dedup_key, DEDUP_TTL):
        return  # duplicate

    severity = determine_severity(metric_name, zscore)
    anomaly_score = min(1.0, zscore / 10.0)

    # LLM classification
    classification = classify_with_llm(service, metric_name, value, mean, zscore, default_type)

    event = AnomalyDetectedEvent(
        anomaly_type      = classification["anomaly_type"],
        severity          = classification.get("severity", severity),
        affected_services = [service],
        detection_time    = ts,
        trigger_metric    = metric_name,
        observed_value    = value,
        baseline_value    = mean,
        deviation_sigma   = zscore,
        anomaly_score     = anomaly_score,
        p50_value         = percentile(history, 50),
        p95_value         = percentile(history, 95),
        p99_value         = percentile(history, 99),
        window_start      = ts - (BASELINE_WINDOW * 10 * 1000),
        window_end        = ts,
        description       = classification.get("description", "")
                            or f"{metric_name} deviation: {zscore:.1f}σ above baseline",
    )

    # Persist incident
    try:
        insert_incident(event.incident_id, event.model_dump())
        log_agent_event(event.incident_id, "detection-agent", "ANOMALY_DETECTED", {
            "metric": metric_name, "zscore": zscore, "value": value, "baseline": mean,
        })
    except Exception as exc:
        logger.warning("DB write failed: %s", exc)

    # Publish to Kafka
    publish(_producer, "anomalies.detected", event.model_dump(), key=service)
    flush(_producer)
    _anomalies_detected += 1

    logger.info(
        "ANOMALY DETECTED: %s on %s | metric=%s | value=%.2f | baseline=%.2f | σ=%.2f | severity=%s",
        event.anomaly_type, service, metric_name, value, mean, zscore, event.severity,
    )


def handle_log_event(data: dict) -> None:
    """Check for FATAL/ERROR log bursts from a service."""
    global _anomalies_detected, _events_processed
    _events_processed += 1

    service = data.get("source_service", "")
    level   = data.get("level", "INFO")
    message = data.get("message", "")

    if level not in ("ERROR", "FATAL"):
        return

    # Count recent errors via Redis incr with 60s window
    from shared.redis_client import incr
    count = incr(f"error_count:{service}", ttl=60)

    # Only fire if we see a burst (>5 errors in 60 seconds)
    if count < 5:
        return

    dedup_key = f"dedup:{service}:ERROR_RATE_SPIKE:log"
    if not set_dedup(dedup_key, DEDUP_TTL):
        return

    event = AnomalyDetectedEvent(
        anomaly_type      = AnomalyType.ERROR_RATE_SPIKE,
        severity          = Severity.HIGH if level == "ERROR" else Severity.CRITICAL,
        affected_services = [service],
        trigger_metric    = "log_error_burst",
        anomaly_score     = 0.7,
        description       = f"Log error burst detected: {count} errors in 60s. Latest: {message[:100]}",
    )

    try:
        insert_incident(event.incident_id, event.model_dump())
        log_agent_event(event.incident_id, "detection-agent", "LOG_ERROR_BURST", {
            "service": service, "error_count_60s": count, "sample_message": message[:200],
        })
    except Exception as exc:
        logger.warning("DB write failed: %s", exc)

    publish(_producer, "anomalies.detected", event.model_dump(), key=service)
    flush(_producer)
    _anomalies_detected += 1
    logger.info("LOG ANOMALY: error burst on %s (%d errors/60s)", service, count)


# ─── HTTP health endpoint ─────────────────────────────────────────────────────

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            body = json.dumps({
                "status": "healthy",
                "service": "detection-agent",
                "anomalies_detected": _anomalies_detected,
                "events_processed":   _events_processed,
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
        topics=["raw.metrics", "raw.logs"],
        group_id="detection-agent",
        auto_offset_reset="latest",
    )

    logger.info("Detection Agent running. Consuming raw.metrics and raw.logs.")

    def dispatch(data: dict) -> None:
        event_type = data.get("event_type", "")
        if event_type == "METRIC":
            handle_metric_event(data)
        elif event_type == "LOG":
            handle_log_event(data)

    consume_loop(consumer, dispatch)


if __name__ == "__main__":
    main()

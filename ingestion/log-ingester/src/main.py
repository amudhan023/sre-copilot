"""
Log Ingester — tails Loki and publishes ERROR/WARN log events to Kafka raw.logs.

Polls Loki's query_range API every LOG_POLL_INTERVAL seconds.
Tracks the last timestamp processed in Redis to avoid re-publishing.
"""
from __future__ import annotations
import logging
import os
import sys
import time
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests

sys.path.insert(0, "/app")
from shared.models import RawLogEvent, now_ms
from shared.kafka_client import make_producer, publish, flush
from shared.redis_client import set_str, get_str

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("log-ingester")

LOKI_URL         = os.getenv("LOKI_URL", "http://loki:3100")
POLL_INTERVAL    = int(os.getenv("POLL_INTERVAL_SECONDS", "5"))
HTTP_PORT        = int(os.getenv("HTTP_PORT", "8111"))
LOOKBACK_SECONDS = int(os.getenv("LOOKBACK_SECONDS", "30"))

# Only publish logs at or above this level to reduce noise
MIN_LEVEL_PUBLISH = {"ERROR", "FATAL", "WARN"}

SERVICES = [
    "api-gateway",
    "payment-service",
    "order-service",
    "user-service",
    "notification-service",
    "inventory-service",
]

_published_count = 0


def _wait_for_loki() -> None:
    for attempt in range(30):
        try:
            r = requests.get(f"{LOKI_URL}/ready", timeout=5)
            if r.status_code == 200:
                logger.info("Loki is ready.")
                return
        except Exception:
            pass
        logger.warning("Waiting for Loki... (attempt %d/30)", attempt + 1)
        time.sleep(5)
    logger.warning("Loki not confirmed ready — proceeding anyway.")


def _query_loki_range(start_ns: int, end_ns: int) -> list[dict]:
    """
    Query Loki for all log streams in the given nanosecond time range.
    Returns list of log entry dicts with service, level, message, timestamp.
    """
    try:
        r = requests.get(
            f"{LOKI_URL}/loki/api/v1/query_range",
            params={
                "query": '{job=~".+"}',
                "start": start_ns,
                "end":   end_ns,
                "limit": 500,
                "direction": "forward",
            },
            timeout=10,
        )
        data = r.json()
        if data.get("status") != "success":
            return []

        entries = []
        for stream in data.get("data", {}).get("result", []):
            labels  = stream.get("stream", {})
            service = labels.get("service", labels.get("app", labels.get("job", "unknown")))
            for ts_ns, line in stream.get("values", []):
                level = _detect_level(line)
                if level in MIN_LEVEL_PUBLISH:
                    entries.append({
                        "service": service,
                        "level":   level,
                        "message": line[:500],           # truncate long lines
                        "ts_ns":   int(ts_ns),
                    })
        return entries
    except Exception as exc:
        logger.warning("Loki query failed: %s", exc)
        return []


def _detect_level(line: str) -> str:
    upper = line.upper()
    if "FATAL" in upper or "PANIC" in upper:
        return "FATAL"
    if "ERROR" in upper or "EXCEPTION" in upper or "STACKTRACE" in upper:
        return "ERROR"
    if "WARN" in upper or "WARNING" in upper:
        return "WARN"
    return "INFO"


def _cursor_key() -> str:
    return "li:cursor:last_ns"


def poll_and_publish(producer) -> None:
    global _published_count

    # Determine time window
    now_ns = now_ms() * 1_000_000
    last_ns_str = get_str(_cursor_key())
    start_ns = int(last_ns_str) if last_ns_str else (now_ns - LOOKBACK_SECONDS * 1_000_000_000)

    entries = _query_loki_range(start_ns, now_ns)

    new_max_ns = start_ns
    for entry in entries:
        svc   = entry["service"]
        level = entry["level"]
        msg   = entry["message"]
        ts_ns = entry["ts_ns"]

        # Only publish services we know about
        if not any(s in svc for s in SERVICES):
            continue

        # Map service fragment to full name
        full_svc = next((s for s in SERVICES if s in svc), svc)

        event = RawLogEvent(
            source_service=full_svc,
            level=level,
            message=msg,
            timestamp=ts_ns // 1_000_000,  # ns → ms
        )
        publish(producer, "raw.logs", event.model_dump(), key=full_svc)
        _published_count += 1
        new_max_ns = max(new_max_ns, ts_ns + 1)

    if new_max_ns > start_ns:
        set_str(_cursor_key(), str(new_max_ns), ttl=3600)

    if entries:
        flush(producer)
        logger.debug("Published %d log events from Loki", len(entries))


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            body = f'{{"status":"healthy","service":"log-ingester","published":{_published_count}}}'.encode()
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
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

    _wait_for_loki()
    producer = make_producer()
    logger.info("Log ingester running. Polling Loki every %ds.", POLL_INTERVAL)

    while True:
        start = time.time()
        poll_and_publish(producer)
        elapsed = time.time() - start
        time.sleep(max(0, POLL_INTERVAL - elapsed))


if __name__ == "__main__":
    main()

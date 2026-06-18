"""
Metrics Ingester — polls Prometheus HTTP API and publishes to Kafka raw.metrics.

Scrapes every SCRAPE_INTERVAL seconds. Deduplicates via Redis so the same
data point is never published twice. Partitions by service_name.
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
from shared.models import RawMetricEvent, now_ms
from shared.kafka_client import make_producer, publish, flush
from shared.redis_client import get_client as get_redis, set_str, get_str

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("metrics-ingester")

PROMETHEUS_URL   = os.getenv("PROMETHEUS_URL", "http://prometheus:9090")
SCRAPE_INTERVAL  = int(os.getenv("SCRAPE_INTERVAL_SECONDS", "15"))
HTTP_PORT        = int(os.getenv("HTTP_PORT", "8110"))

SERVICES = [
    "api-gateway",
    "payment-service",
    "order-service",
    "user-service",
    "notification-service",
    "inventory-service",
]

# These metric names match what traffic-simulator exposes
METRICS_TO_SCRAPE = [
    "service_latency_p99_ms",
    "service_error_rate_percent",
    "service_cpu_percent",
    "service_memory_percent",
    "service_db_connections",
    "kafka_consumer_lag",
]

_published_count = 0


def _wait_for_prometheus() -> None:
    for attempt in range(30):
        try:
            r = requests.get(f"{PROMETHEUS_URL}/-/ready", timeout=5)
            if r.status_code == 200:
                logger.info("Prometheus is ready.")
                return
        except Exception:
            pass
        logger.warning("Waiting for Prometheus... (attempt %d/30)", attempt + 1)
        time.sleep(5)
    logger.warning("Prometheus not confirmed ready — proceeding anyway.")


def _query_metric(metric_name: str) -> list[dict]:
    """Query Prometheus instant vector for a metric. Returns list of {service, value}."""
    try:
        r = requests.get(
            f"{PROMETHEUS_URL}/api/v1/query",
            params={"query": metric_name},
            timeout=10,
        )
        data = r.json()
        if data.get("status") != "success":
            return []
        results = []
        for result in data["data"]["result"]:
            service = result["metric"].get("service", "")
            if service in SERVICES:
                value = float(result["value"][1])
                results.append({"service": service, "value": value})
        return results
    except Exception as exc:
        logger.warning("Prometheus query failed for %s: %s", metric_name, exc)
        return []


def scrape_and_publish(producer) -> None:
    global _published_count
    ts = now_ms()

    for metric_name in METRICS_TO_SCRAPE:
        results = _query_metric(metric_name)
        for item in results:
            svc   = item["service"]
            value = item["value"]

            # Dedup: skip if we published this exact (service, metric) in the last 10s
            dedup_key = f"mi:dedup:{svc}:{metric_name}"
            if get_str(dedup_key):
                continue

            event = RawMetricEvent(
                source_service=svc,
                metric_name=metric_name,
                metric_value=value,
                timestamp=ts,
            )
            publish(producer, "raw.metrics", event.model_dump(), key=svc)
            set_str(dedup_key, "1", ttl=SCRAPE_INTERVAL - 1)
            _published_count += 1

    flush(producer)


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            body = f'{{"status":"healthy","service":"metrics-ingester","published":{_published_count}}}'.encode()
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

    _wait_for_prometheus()
    producer = make_producer()
    logger.info("Metrics ingester running. Scraping every %ds.", SCRAPE_INTERVAL)

    while True:
        start = time.time()
        scrape_and_publish(producer)
        elapsed = time.time() - start
        sleep_time = max(0, SCRAPE_INTERVAL - elapsed)
        time.sleep(sleep_time)


if __name__ == "__main__":
    main()

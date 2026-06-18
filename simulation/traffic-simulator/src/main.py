"""
Traffic Simulator — generates realistic baseline service metrics and logs.

Responsibilities:
  1. Expose /metrics Prometheus endpoint with live service metrics
  2. Publish RawMetricEvent to Kafka raw.metrics every 10 seconds
  3. Publish RawLogEvent to Kafka raw.logs every 3 seconds
  4. Read failure state from Redis and blend anomalous values into metrics
"""
from __future__ import annotations
import json
import logging
import math
import os
import random
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Optional

sys.path.insert(0, "/app")
from shared.models import RawMetricEvent, RawLogEvent, now_ms
from shared.kafka_client import make_producer, publish, flush
from shared.redis_client import get_client as get_redis, get_failure_state

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("traffic-simulator")

METRICS_PORT     = int(os.getenv("METRICS_PORT", "8100"))
PUBLISH_INTERVAL = 10    # Kafka metric publish interval (seconds)
LOG_INTERVAL     = 3     # Kafka log publish interval (seconds)
PROM_INTERVAL    = 5     # Prometheus scrape interval (seconds)
INITIAL_QUIET    = int(os.getenv("INITIAL_QUIET_PERIOD_SECONDS", "30"))

SERVICES = [
    "api-gateway",
    "payment-service",
    "order-service",
    "user-service",
    "notification-service",
    "inventory-service",
]

# ─── Service baseline state ───────────────────────────────────────────────────

@dataclass
class ServiceState:
    name: str
    base_request_rate:   float = 50.0
    base_error_rate:     float = 0.5
    base_latency_p99:    float = 200.0
    base_cpu:            float = 25.0
    base_memory:         float = 45.0
    base_db_conn:        float = 20.0
    base_kafka_lag:      float = 0.0
    # Actual live values (modified by failure state)
    request_rate:        float = field(init=False)
    error_rate:          float = field(init=False)
    latency_p99:         float = field(init=False)
    cpu:                 float = field(init=False)
    memory:              float = field(init=False)
    db_connections:      float = field(init=False)
    kafka_lag:           float = field(init=False)
    total_requests:      int   = 0
    total_errors:        int   = 0

    def __post_init__(self):
        self.request_rate   = self.base_request_rate
        self.error_rate     = self.base_error_rate
        self.latency_p99    = self.base_latency_p99
        self.cpu            = self.base_cpu
        self.memory         = self.base_memory
        self.db_connections = self.base_db_conn
        self.kafka_lag      = self.base_kafka_lag


BASELINES: dict[str, dict] = {
    "api-gateway":          {"request_rate": 150, "error_rate": 0.3, "latency_p99": 180, "cpu": 22, "memory": 38, "db_conn": 0,  "kafka_lag": 0},
    "payment-service":      {"request_rate": 50,  "error_rate": 0.1, "latency_p99": 420, "cpu": 28, "memory": 52, "db_conn": 18, "kafka_lag": 0},
    "order-service":        {"request_rate": 80,  "error_rate": 0.4, "latency_p99": 360, "cpu": 32, "memory": 46, "db_conn": 12, "kafka_lag": 50},
    "user-service":         {"request_rate": 200, "error_rate": 0.3, "latency_p99": 210, "cpu": 18, "memory": 44, "db_conn": 8,  "kafka_lag": 0},
    "notification-service": {"request_rate": 30,  "error_rate": 0.8, "latency_p99": 850, "cpu": 12, "memory": 42, "db_conn": 0,  "kafka_lag": 20},
    "inventory-service":    {"request_rate": 40,  "error_rate": 0.9, "latency_p99": 310, "cpu": 15, "memory": 40, "db_conn": 10, "kafka_lag": 0},
}

# Live service states (shared with the prometheus handler)
service_states: dict[str, ServiceState] = {
    svc: ServiceState(
        name=svc,
        base_request_rate=BASELINES[svc]["request_rate"],
        base_error_rate=BASELINES[svc]["error_rate"],
        base_latency_p99=BASELINES[svc]["latency_p99"],
        base_cpu=BASELINES[svc]["cpu"],
        base_memory=BASELINES[svc]["memory"],
        base_db_conn=BASELINES[svc]["db_conn"],
        base_kafka_lag=BASELINES[svc]["kafka_lag"],
    )
    for svc in SERVICES
}

_lock = threading.Lock()


def _noise(pct: float = 0.05) -> float:
    """Return a random noise factor: 1 ± pct."""
    return 1.0 + random.uniform(-pct, pct)


def _diurnal_factor(period_minutes: int = 30) -> float:
    """Compressed diurnal cycle: request rate oscillates with a sine wave."""
    t = time.time()
    cycle_pos = (t % (period_minutes * 60)) / (period_minutes * 60)
    return 0.8 + 0.4 * math.sin(2 * math.pi * cycle_pos)


def _update_states() -> None:
    """Blend baseline + diurnal + failure state into live metrics."""
    diurnal = _diurnal_factor()

    with _lock:
        for svc, state in service_states.items():
            # Check Redis for active failure injection
            failure = get_failure_state(svc)

            if failure:
                # Failure state overrides normal baseline
                state.latency_p99   = failure.get("latency_p99",   state.base_latency_p99)   * _noise(0.03)
                state.error_rate    = failure.get("error_rate",    state.base_error_rate)    * _noise(0.03)
                state.cpu           = failure.get("cpu",           state.base_cpu)           * _noise(0.02)
                state.memory        = failure.get("memory",        state.base_memory)        * _noise(0.01)
                state.db_connections= failure.get("db_connections",state.base_db_conn)       * _noise(0.03)
                state.kafka_lag     = failure.get("kafka_lag",     state.base_kafka_lag)     * _noise(0.05)
                state.request_rate  = state.base_request_rate * diurnal * _noise(0.08)
            else:
                # Normal baseline with diurnal variation and small noise
                state.latency_p99   = state.base_latency_p99   * diurnal * _noise(0.08)
                state.error_rate    = state.base_error_rate    * _noise(0.15)
                state.cpu           = state.base_cpu           * diurnal * _noise(0.10)
                state.memory        = state.base_memory                  * _noise(0.03)
                state.db_connections= state.base_db_conn       * diurnal * _noise(0.10)
                state.kafka_lag     = state.base_kafka_lag               * _noise(0.20)
                state.request_rate  = state.base_request_rate  * diurnal * _noise(0.08)

            # Clamp to realistic bounds
            state.latency_p99    = max(10.0, state.latency_p99)
            state.error_rate     = max(0.0, min(100.0, state.error_rate))
            state.cpu            = max(1.0, min(100.0, state.cpu))
            state.memory         = max(10.0, min(100.0, state.memory))
            state.db_connections = max(0.0, state.db_connections)
            state.kafka_lag      = max(0.0, state.kafka_lag)
            state.request_rate   = max(1.0, state.request_rate)

            # Accumulate counters
            rps = state.request_rate / 10  # approximate per interval
            state.total_requests += int(rps * PUBLISH_INTERVAL)
            state.total_errors   += int(rps * PUBLISH_INTERVAL * state.error_rate / 100)


# ─── Prometheus /metrics endpoint ────────────────────────────────────────────

class MetricsHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/metrics":
            with _lock:
                lines = []
                for svc, s in service_states.items():
                    lbl = f'service="{svc}"'
                    lines += [
                        f'service_latency_p99_ms{{{lbl}}} {s.latency_p99:.1f}',
                        f'service_error_rate_percent{{{lbl}}} {s.error_rate:.2f}',
                        f'service_cpu_percent{{{lbl}}} {s.cpu:.1f}',
                        f'service_memory_percent{{{lbl}}} {s.memory:.1f}',
                        f'service_db_connections{{{lbl}}} {s.db_connections:.0f}',
                        f'kafka_consumer_lag{{{lbl},consumer_group="main"}} {s.kafka_lag:.0f}',
                        f'http_requests_total{{{lbl}}} {s.total_requests}',
                    ]
                body = "\n".join(lines).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.end_headers()
            self.wfile.write(body)

        elif self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"healthy","service":"traffic-simulator"}')

        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):
        pass  # suppress HTTP access logs


# ─── Kafka publishers ─────────────────────────────────────────────────────────

LOG_TEMPLATES: dict[str, list[str]] = {
    "nominal": [
        "Request processed successfully",
        "Cache hit for key {key}",
        "Database query completed in {ms}ms",
        "Message published to topic {topic}",
        "User session validated",
    ],
    "warning": [
        "Slow database query detected: {ms}ms",
        "Cache miss rate elevated: {pct}%",
        "Retry attempt {n} for downstream call",
        "Response time approaching SLA threshold",
    ],
    "error": [
        "ERROR: Connection timeout to {dep}: {ms}ms",
        "ERROR: Database query failed: {err}",
        "ERROR: Upstream returned {code}: {msg}",
        "FATAL: Unable to connect to Redis after {n} retries",
        "EXCEPTION: NullPointerException in {module}.{method}",
    ],
}

ERROR_DEPS   = ["postgres", "redis", "payment-gateway", "inventory-service"]
ERROR_CODES  = [500, 502, 503, 504]
ERROR_MSGS   = ["internal server error", "service unavailable", "bad gateway", "timeout"]
MODULES      = ["PaymentProcessor", "OrderHandler", "UserAuth", "CacheManager"]
METHODS      = ["process", "validate", "execute", "handle"]


def _make_log_event(svc: str, state: ServiceState) -> RawLogEvent:
    # Determine log level based on error rate
    if state.error_rate > 10:
        level = "ERROR"
        templates = LOG_TEMPLATES["error"]
    elif state.error_rate > 2 or state.latency_p99 > state.base_latency_p99 * 2:
        level = "WARN"
        templates = LOG_TEMPLATES["warning"]
    else:
        level = "INFO"
        templates = LOG_TEMPLATES["nominal"]

    tpl = random.choice(templates)
    msg = tpl.format(
        key=f"user:{random.randint(1000,9999)}",
        ms=int(state.latency_p99 * random.uniform(0.3, 1.2)),
        topic="order.events",
        pct=round(100 - random.uniform(60, 80), 1),
        dep=random.choice(ERROR_DEPS),
        err="connection pool exhausted",
        code=random.choice(ERROR_CODES),
        msg=random.choice(ERROR_MSGS),
        n=random.randint(1, 5),
        module=random.choice(MODULES),
        method=random.choice(METHODS),
    )
    return RawLogEvent(source_service=svc, level=level, message=msg)


def _publish_metrics(producer) -> None:
    """Publish one RawMetricEvent per service per metric to Kafka."""
    METRIC_FIELDS = [
        ("service_latency_p99_ms",     "latency_p99"),
        ("service_error_rate_percent", "error_rate"),
        ("service_cpu_percent",        "cpu"),
        ("service_memory_percent",     "memory"),
        ("service_db_connections",     "db_connections"),
        ("kafka_consumer_lag",         "kafka_lag"),
    ]
    with _lock:
        for svc, state in service_states.items():
            for metric_name, attr in METRIC_FIELDS:
                event = RawMetricEvent(
                    source_service=svc,
                    metric_name=metric_name,
                    metric_value=getattr(state, attr),
                )
                publish(producer, "raw.metrics", event.model_dump(), key=svc)


def _publish_logs(producer) -> None:
    """Publish one log event per service to Kafka."""
    with _lock:
        for svc, state in service_states.items():
            event = _make_log_event(svc, state)
            publish(producer, "raw.logs", event.model_dump(), key=svc)


# ─── Main loop ────────────────────────────────────────────────────────────────

def run_simulator() -> None:
    logger.info("Traffic simulator starting. Quiet period: %ds", INITIAL_QUIET)
    time.sleep(INITIAL_QUIET)

    producer = make_producer()
    logger.info("Kafka producer ready. Starting traffic simulation.")

    last_metrics = 0.0
    last_logs    = 0.0

    while True:
        now = time.time()
        _update_states()

        if now - last_metrics >= PUBLISH_INTERVAL:
            _publish_metrics(producer)
            flush(producer)
            last_metrics = now

        if now - last_logs >= LOG_INTERVAL:
            _publish_logs(producer)
            last_logs = now

        time.sleep(1)


def main() -> None:
    # Start Prometheus metrics HTTP server in a daemon thread
    server = HTTPServer(("0.0.0.0", METRICS_PORT), MetricsHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    logger.info("Prometheus metrics server on :%d", METRICS_PORT)

    run_simulator()


if __name__ == "__main__":
    main()

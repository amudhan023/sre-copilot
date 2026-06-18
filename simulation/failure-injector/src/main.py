"""
Failure Injector — injects anomalous metric states into the simulation.

Communicates with traffic-simulator via Redis:
  - Sets failure state: Redis key failure:state:{service}
  - Traffic-simulator reads these and blends anomalous values into metrics

Also publishes RawDeploymentEvent to Kafka for DEPLOYMENT_FAILURE scenario.

Scenarios run in round-robin with randomised inter-failure gaps (5-10 min).
"""
from __future__ import annotations
import logging
import os
import random
import sys
import time
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Optional

sys.path.insert(0, "/app")
from shared.models import RawDeploymentEvent, now_ms
from shared.kafka_client import make_producer, publish, flush
from shared.redis_client import set_failure_state, clear_failure_state

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("failure-injector")

INITIAL_QUIET       = int(os.getenv("INITIAL_QUIET_PERIOD_SECONDS", "120"))
FAILURE_GAP_MIN     = int(os.getenv("FAILURE_GAP_MIN_SECONDS", "300"))
FAILURE_GAP_MAX     = int(os.getenv("FAILURE_GAP_MAX_SECONDS", "600"))
HTTP_PORT           = int(os.getenv("HTTP_PORT", "8101"))

_current_failure: Optional[str] = None
_injector_active = True


# ─── Failure scenario definitions ────────────────────────────────────────────

@dataclass
class FailureScenario:
    name:            str
    service:         str
    anomaly_type:    str
    duration_seconds: int
    failure_state:   dict   # values to set in Redis for traffic-simulator
    description:     str


SCENARIOS: list[FailureScenario] = [
    FailureScenario(
        name="LATENCY_SPIKE",
        service="payment-service",
        anomaly_type="LATENCY_SPIKE",
        duration_seconds=300,   # 5 minutes
        failure_state={
            "latency_p99":    8500.0,
            "error_rate":     3.5,
            "cpu":            55.0,
            "memory":         60.0,
            "db_connections": 42.0,
            "kafka_lag":      0.0,
        },
        description="P99 latency spike to 8500ms on payment-service (connection pool pressure from slow queries)",
    ),
    FailureScenario(
        name="ERROR_RATE_SPIKE",
        service="order-service",
        anomaly_type="ERROR_RATE_SPIKE",
        duration_seconds=180,   # 3 minutes
        failure_state={
            "latency_p99":    1200.0,
            "error_rate":     45.0,
            "cpu":            48.0,
            "memory":         50.0,
            "db_connections": 20.0,
            "kafka_lag":      200.0,
        },
        description="Error rate spike to 45% on order-service (NullPointerException in payment integration)",
    ),
    FailureScenario(
        name="CPU_SATURATION",
        service="api-gateway",
        anomaly_type="CPU_SATURATION",
        duration_seconds=480,   # 8 minutes
        failure_state={
            "latency_p99":    4200.0,
            "error_rate":     2.0,
            "cpu":            95.0,
            "memory":         62.0,
            "db_connections": 0.0,
            "kafka_lag":      0.0,
        },
        description="CPU saturation to 95% on api-gateway (goroutine leak from async logging middleware)",
    ),
    FailureScenario(
        name="MEMORY_LEAK",
        service="notification-service",
        anomaly_type="MEMORY_LEAK",
        duration_seconds=720,   # 12 minutes
        failure_state={
            "latency_p99":    2200.0,
            "error_rate":     1.5,
            "cpu":            18.0,
            "memory":         96.0,
            "db_connections": 0.0,
            "kafka_lag":      5000.0,
        },
        description="Memory growing to 96% on notification-service (unbounded email template cache)",
    ),
    FailureScenario(
        name="DB_CONNECTION_EXHAUSTION",
        service="payment-service",
        anomaly_type="DB_CONNECTION_EXHAUSTION",
        duration_seconds=360,   # 6 minutes
        failure_state={
            "latency_p99":    12000.0,
            "error_rate":     35.0,
            "cpu":            40.0,
            "memory":         58.0,
            "db_connections": 99.0,
            "kafka_lag":      0.0,
        },
        description="DB connection pool exhausted (100/100) on payment-service (batch job consuming connections)",
    ),
    FailureScenario(
        name="KAFKA_CONSUMER_LAG",
        service="order-service",
        anomaly_type="KAFKA_CONSUMER_LAG",
        duration_seconds=600,   # 10 minutes
        failure_state={
            "latency_p99":    950.0,
            "error_rate":     1.2,
            "cpu":            20.0,
            "memory":         48.0,
            "db_connections": 12.0,
            "kafka_lag":      52000.0,
        },
        description="Kafka consumer lag reaching 52,000 messages on order-service (slow SMTP blocking consumer)",
    ),
    FailureScenario(
        name="DEPENDENCY_OUTAGE",
        service="inventory-service",
        anomaly_type="DEPENDENCY_OUTAGE",
        duration_seconds=300,   # 5 minutes
        failure_state={
            "latency_p99":    5500.0,
            "error_rate":     98.0,
            "cpu":            8.0,
            "memory":         42.0,
            "db_connections": 2.0,
            "kafka_lag":      0.0,
        },
        description="inventory-service returning 503 (postgres checkpoint storm blocking all writes)",
    ),
    FailureScenario(
        name="DEPLOYMENT_FAILURE",
        service="user-service",
        anomaly_type="DEPLOYMENT_FAILURE",
        duration_seconds=240,   # 4 minutes
        failure_state={
            "latency_p99":    800.0,
            "error_rate":     100.0,
            "cpu":            12.0,
            "memory":         45.0,
            "db_connections": 6.0,
            "kafka_lag":      0.0,
        },
        description="user-service 100% error rate after deployment (Redis URI format incompatibility in v5.1.0)",
    ),
    FailureScenario(
        name="NETWORK_PARTITION",
        service="payment-service",
        anomaly_type="NETWORK_PARTITION",
        duration_seconds=180,   # 3 minutes
        failure_state={
            "latency_p99":    6000.0,
            "error_rate":     62.0,
            "cpu":            35.0,
            "memory":         55.0,
            "db_connections": 28.0,
            "kafka_lag":      0.0,
        },
        description="Network partition causing intermittent connection resets on payment-service (BGP route asymmetry)",
    ),
]


def _inject_scenario(scenario: FailureScenario, producer) -> None:
    global _current_failure
    _current_failure = scenario.name

    logger.info(
        "INJECTING FAILURE: %s on %s (duration: %ds) — %s",
        scenario.name,
        scenario.service,
        scenario.duration_seconds,
        scenario.description,
    )

    # For deployment failure: also publish a deployment event to Kafka
    if scenario.anomaly_type == "DEPLOYMENT_FAILURE":
        deploy_event = RawDeploymentEvent(
            source_service=scenario.service,
            version="v5.1.0-bad",
            deployed_by="ci-cd-pipeline",
            change_type="CODE",
            git_sha="badbeef",
            description="Migrated Redis URI format — may cause incompatibility with older client",
            known_risks=["Redis URI format change", "Client library mismatch possible"],
        )
        publish(producer, "raw.deployments", deploy_event.model_dump(), key=scenario.service)
        flush(producer)
        logger.info("Published DEPLOYMENT event for %s", scenario.service)

    # Set failure state in Redis (traffic-simulator reads this)
    set_failure_state(scenario.service, scenario.failure_state, ttl=scenario.duration_seconds + 60)
    logger.info("Failure state set in Redis for %s", scenario.service)

    # Wait for the scenario duration
    time.sleep(scenario.duration_seconds)

    # Clear the failure state (normal metrics resume)
    clear_failure_state(scenario.service)
    _current_failure = None
    logger.info("FAILURE CLEARED: %s on %s — service returning to normal", scenario.name, scenario.service)


# ─── Status HTTP server ───────────────────────────────────────────────────────

class StatusHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            body = b'{"status":"healthy","service":"failure-injector"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        elif self.path == "/status":
            import json
            status = {
                "active_failure": _current_failure,
                "injector_active": _injector_active,
            }
            body = json.dumps(status).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        elif self.path.startswith("/inject/"):
            # Manual trigger: GET /inject/<scenario_name>
            name = self.path.split("/inject/")[1]
            matched = [s for s in SCENARIOS if s.name == name]
            if matched:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(f'{{"triggered":"{name}"}}'.encode())
            else:
                self.send_response(404)
                self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):
        pass


# ─── Main loop ────────────────────────────────────────────────────────────────

def run_injector() -> None:
    logger.info("Failure injector starting. Quiet period: %ds", INITIAL_QUIET)
    time.sleep(INITIAL_QUIET)

    producer = make_producer()
    logger.info("Kafka producer ready. Failure injection loop starting.")

    # Randomise order to vary the demo
    scenario_list = list(SCENARIOS)
    random.shuffle(scenario_list)
    idx = 0

    while _injector_active:
        scenario = scenario_list[idx % len(scenario_list)]
        idx += 1

        # Inject the failure
        _inject_scenario(scenario, producer)

        # Random gap between failures
        gap = random.randint(FAILURE_GAP_MIN, FAILURE_GAP_MAX)
        logger.info("Next failure in %d seconds.", gap)
        time.sleep(gap)


def main() -> None:
    server = HTTPServer(("0.0.0.0", HTTP_PORT), StatusHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    logger.info("Status server on :%d", HTTP_PORT)

    run_injector()


if __name__ == "__main__":
    main()

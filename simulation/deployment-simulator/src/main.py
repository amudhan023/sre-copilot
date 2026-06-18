"""
Deployment Simulator — periodically generates realistic CI/CD deployment events.

Publishes RawDeploymentEvent to Kafka raw.deployments and records them in Postgres.
Occasionally deploys a "bad" version to create a deployment-correlated incident.

Schedule: random deployment every 15-30 minutes (configurable).
"""
from __future__ import annotations
import logging
import os
import random
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, "/app")
from shared.models import RawDeploymentEvent, now_ms
from shared.kafka_client import make_producer, publish, flush
from shared.db_client import init_pool, record_deployment

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("deployment-simulator")

INITIAL_QUIET  = int(os.getenv("INITIAL_QUIET_PERIOD_SECONDS", "90"))
DEPLOY_MIN_S   = int(os.getenv("DEPLOY_INTERVAL_MIN_SECONDS", "900"))    # 15 minutes
DEPLOY_MAX_S   = int(os.getenv("DEPLOY_INTERVAL_MAX_SECONDS", "1800"))   # 30 minutes
HTTP_PORT      = int(os.getenv("HTTP_PORT", "8102"))

SERVICES = [
    "api-gateway",
    "payment-service",
    "order-service",
    "user-service",
    "notification-service",
    "inventory-service",
]

CHANGE_TYPES = ["CODE", "CONFIG", "DEPENDENCY", "INFRASTRUCTURE"]

DESCRIPTIONS = [
    "Performance improvements and bug fixes",
    "Security patch for dependency vulnerabilities",
    "New feature: {feature} — reviewed and approved",
    "Configuration update: tuned {param} for improved stability",
    "Dependency upgrade: updated {lib} to latest version",
    "Hotfix for {bug} reported in previous release",
    "Infrastructure change: updated {infra} configuration",
    "Refactoring: improved {module} for maintainability",
]

FEATURES  = ["pagination", "search", "notifications", "analytics", "export", "caching"]
PARAMS    = ["connection pool", "timeout values", "retry logic", "batch size"]
LIBS      = ["requests", "psycopg2", "kafka-python", "anthropic", "redis", "fastapi"]
BUGS      = ["NullPointerException in payment handler", "memory leak in cache layer", "connection timeout regression"]
INFRAS    = ["TLS certificates", "DNS settings", "network policy", "resource limits"]
MODULES   = ["authentication", "payment processing", "order validation", "notification dispatch"]

BAD_DEPLOYMENT_CHANCE = 0.15  # 15% chance of a "risky" deployment


def _gen_version(service: str) -> str:
    major = random.randint(1, 6)
    minor = random.randint(0, 20)
    patch = random.randint(0, 10)
    return f"v{major}.{minor}.{patch}"


def _gen_description(is_risky: bool) -> tuple[str, list[str], str]:
    tpl = random.choice(DESCRIPTIONS)
    desc = tpl.format(
        feature=random.choice(FEATURES),
        param=random.choice(PARAMS),
        lib=random.choice(LIBS),
        bug=random.choice(BUGS),
        infra=random.choice(INFRAS),
        module=random.choice(MODULES),
    )
    change_type = random.choice(CHANGE_TYPES)

    if is_risky:
        known_risks = random.sample([
            "New query pattern not indexed",
            "Cache eviction policy not configured",
            "Connection pool max not tested at scale",
            "Breaking API change — downstream consumers not updated",
            "Synchronous operation on hot path",
            "Memory allocation pattern changed",
            "Configuration value in wrong unit",
            "External dependency API version changed",
        ], k=random.randint(1, 3))
    else:
        known_risks = []

    return desc, known_risks, change_type


def deploy_service(producer, service: str) -> None:
    is_risky = random.random() < BAD_DEPLOYMENT_CHANCE
    version = _gen_version(service)
    description, known_risks, change_type = _gen_description(is_risky)
    git_sha = uuid.uuid4().hex[:8]

    event = RawDeploymentEvent(
        source_service=service,
        version=version,
        deployed_by="ci-cd-pipeline",
        change_type=change_type,
        git_sha=git_sha,
        description=description,
        known_risks=known_risks,
    )

    publish(producer, "raw.deployments", event.model_dump(), key=service)
    flush(producer)

    try:
        record_deployment(event.model_dump())
    except Exception as exc:
        logger.warning("Could not record deployment in Postgres: %s", exc)

    risk_flag = " [RISKY]" if known_risks else ""
    logger.info(
        "DEPLOYED%s: %s %s (%s) — %s",
        risk_flag, service, version, change_type, description[:60],
    )


# ─── HTTP status server ───────────────────────────────────────────────────────

class StatusHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"healthy","service":"deployment-simulator"}')
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):
        pass


# ─── Main loop ────────────────────────────────────────────────────────────────

def run_simulator() -> None:
    logger.info("Deployment simulator starting. Quiet period: %ds", INITIAL_QUIET)
    time.sleep(INITIAL_QUIET)

    producer = make_producer()
    try:
        init_pool()
    except Exception as exc:
        logger.warning("Postgres pool init failed (deployments won't be recorded): %s", exc)

    logger.info("Kafka producer ready. Deployment simulation starting.")

    while True:
        # Deploy a random service
        service = random.choice(SERVICES)
        deploy_service(producer, service)

        # Wait before next deployment
        gap = random.randint(DEPLOY_MIN_S, DEPLOY_MAX_S)
        logger.info("Next deployment in %ds.", gap)
        time.sleep(gap)


def main() -> None:
    server = HTTPServer(("0.0.0.0", HTTP_PORT), StatusHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    logger.info("Status server on :%d", HTTP_PORT)

    run_simulator()


if __name__ == "__main__":
    main()

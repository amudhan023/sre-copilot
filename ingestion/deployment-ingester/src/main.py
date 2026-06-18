"""
Deployment Ingester — polls Postgres deployments table and forwards new rows to Kafka.

Simpler alternative to a webhook receiver: the deployment-simulator writes to Postgres,
and this service polls for new rows and forwards them to raw.deployments Kafka topic.

Tracks last processed row via Redis cursor.
"""
from __future__ import annotations
import logging
import os
import sys
import time
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, "/app")
from shared.models import RawDeploymentEvent, now_ms
from shared.kafka_client import make_producer, publish, flush
from shared.db_client import init_pool, fetch_all
from shared.redis_client import set_str, get_str

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("deployment-ingester")

POLL_INTERVAL = int(os.getenv("POLL_INTERVAL_SECONDS", "10"))
HTTP_PORT     = int(os.getenv("HTTP_PORT", "8112"))

_published_count = 0


def _cursor_key() -> str:
    return "di:cursor:last_id"


def poll_and_publish(producer) -> None:
    global _published_count

    last_id = get_str(_cursor_key()) or "00000000-0000-0000-0000-000000000000"

    rows = fetch_all(
        """
        SELECT id, service_name, version, deployed_by, change_type,
               git_sha, description, known_risks,
               EXTRACT(EPOCH FROM deployed_at)::BIGINT * 1000 AS deployed_at_ms
        FROM deployments
        WHERE id::text > %s
        ORDER BY deployed_at ASC
        LIMIT 50
        """,
        (last_id,),
    )

    if not rows:
        return

    new_last_id = last_id
    for row in rows:
        event = RawDeploymentEvent(
            source_service=row["service_name"],
            version=row["version"],
            deployed_by=row.get("deployed_by", "ci-cd-pipeline"),
            change_type=row.get("change_type", "CODE"),
            git_sha=row.get("git_sha", ""),
            description=row.get("description", ""),
            known_risks=list(row.get("known_risks") or []),
            timestamp=row["deployed_at_ms"],
        )
        publish(producer, "raw.deployments", event.model_dump(), key=event.source_service)
        _published_count += 1
        new_last_id = str(row["id"])

    flush(producer)
    set_str(_cursor_key(), new_last_id, ttl=86400)
    logger.debug("Forwarded %d deployment events to Kafka", len(rows))


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            body = f'{{"status":"healthy","service":"deployment-ingester","published":{_published_count}}}'.encode()
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

    init_pool()
    producer = make_producer()
    logger.info("Deployment ingester running. Polling every %ds.", POLL_INTERVAL)

    while True:
        start = time.time()
        try:
            poll_and_publish(producer)
        except Exception as exc:
            logger.warning("Poll cycle failed: %s", exc)
        elapsed = time.time() - start
        time.sleep(max(0, POLL_INTERVAL - elapsed))


if __name__ == "__main__":
    main()

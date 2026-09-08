from __future__ import annotations

import hashlib
import hmac
import logging
import os
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from kafka import KafkaProducer

from sre_copilot.alerting.dedup import RedisDeduplicator
from sre_copilot.alerting.events import IncidentEvent, alertmanager_to_event

logger = logging.getLogger(__name__)


def _authorized(token: str | None, authorization: str | None) -> bool:
    if not token:
        return True
    expected = f"Bearer {token}".encode()
    actual = (authorization or "").encode()
    return hmac.compare_digest(actual, expected)


class IncidentPublisher:
    def __init__(self, bootstrap: str, topic: str):
        self.topic = topic
        self.producer = KafkaProducer(
            bootstrap_servers=[item.strip() for item in bootstrap.split(",") if item.strip()],
            value_serializer=lambda value: value.encode("utf-8"),
            key_serializer=lambda value: value.encode("utf-8"),
            acks="all",
            retries=5,
            linger_ms=5,
        )

    def publish(self, event: IncidentEvent) -> None:
        future = self.producer.send(
            self.topic,
            key=f"{event.tenant}/{event.service}",
            value=event.to_json(),
        )
        # Wait for broker acknowledgement before returning HTTP 200. This lets
        # Alertmanager retry on a 5xx instead of silently losing an incident.
        future.get(timeout=10)

    def close(self) -> None:
        self.producer.flush(timeout=10)
        self.producer.close()


app = FastAPI(title="SRE Copilot Alert Receiver")

_RECEIVER_TOKEN = os.getenv("RECEIVER_TOKEN")
_KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP", "localhost:29092")
_KAFKA_TOPIC = os.getenv("KAFKA_INCIDENT_TOPIC", "incidents")
_DEDUP_TTL = int(os.getenv("DEDUP_TTL_SECONDS", str(4 * 60 * 60)))

_dedup = RedisDeduplicator(os.getenv("REDIS_URL", "redis://localhost:6379/0"))
_publisher = IncidentPublisher(_KAFKA_BOOTSTRAP, _KAFKA_TOPIC)


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    kafka_ok = True
    try:
        _publisher.producer.bootstrap_connected()
    except Exception:
        kafka_ok = False
    return {
        "status": "ok" if kafka_ok else "degraded",
        "kafka_reachable": kafka_ok,
        "dedup": "redis",
        "token_required": bool(_RECEIVER_TOKEN),
    }


@app.post("/alerts")
def receive_alert(payload: dict[str, Any], authorization: str | None = Header(default=None)) -> dict[str, Any]:
    if not _authorized(_RECEIVER_TOKEN, authorization):
        raise HTTPException(status_code=401, detail="unauthorized")

    try:
        event = alertmanager_to_event(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Keep lifecycle transitions independent. A resolved notification must not
    # be swallowed by the firing notification's dedup key.
    dedup_key = f"sre-copilot:dedup:{event.incident_key}:{event.status}"
    if not _dedup.claim(dedup_key, _DEDUP_TTL):
        return {
            "status": "duplicate",
            "incident_id": event.incident_id,
            "incident_key": event.incident_key,
        }

    try:
        _publisher.publish(event)
    except Exception as exc:
        # Do not acknowledge Alertmanager when Kafka failed. This is important:
        # Alertmanager will retry the webhook. Redis may have claimed the key,
        # so a later retry can be suppressed incorrectly; remove the claim when
        # possible so the failed publish is safely retryable.
        logger.exception("Kafka publish failed for %s", event.incident_key)
        try:
            _dedup._client.delete(dedup_key)
        except Exception:
            pass
        raise HTTPException(status_code=503, detail="incident publish failed") from exc

    return {
        "status": "published",
        "incident_id": event.incident_id,
        "incident_key": event.incident_key,
        "topic": _KAFKA_TOPIC,
    }

from fastapi import FastAPI, Request, HTTPException
from functools import lru_cache
from starlette.concurrency import run_in_threadpool
import json
from kafka import KafkaProducer
from kafka.errors import KafkaError
import os
import hashlib
import redis
from dotenv import load_dotenv

load_dotenv()

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
KAFKA_BOOTSTRAP = os.environ.get("KAFKA_BOOTSTRAP", "localhost:29092")

app = FastAPI()

# The front door for incoming alerts (Alertmanager-style webhooks). Its main
# job is deduplication: Alertmanager can fire the same alert repeatedly, so
# we hash tenant+service+alertname into a fingerprint and store it in Redis
# with a 5-minute TTL (nx=True means "only set if it doesn't already exist").
# If the key already exists, this alert is a repeat and gets dropped. If it's
# genuinely new, we publish it as an incident onto the Kafka "incidents"
# topic for the rest of the pipeline to pick up. Kafka's client is
# synchronous, so the send is pushed onto a thread pool to avoid blocking
# FastAPI's async event loop.


def create_fingerprint(tenant, service, alertname):
    value = f"{tenant}:{service}:{alertname}"
    return hashlib.sha256(value.encode()).hexdigest()

@lru_cache(maxsize=1)
def get_redis():
    return redis.Redis.from_url(REDIS_URL, decode_responses=True)

@lru_cache(maxsize=1)
def get_producer():
    return KafkaProducer(
        bootstrap_servers=KAFKA_BOOTSTRAP,
        value_serializer=lambda v: json.dumps(v).encode(),
        # fail fast when the broker is down: bootstrap_timeout_ms governs the
        # constructor, max_block_ms the send. Both needed.
        bootstrap_timeout_ms=3000,
        max_block_ms=3000,
    )


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.post("/alerts")
async def receive_alert(request: Request):
    payload = await request.json()
    group_labels = payload.get("groupLabels", {})
    tenant = group_labels.get("tenant")
    service = group_labels.get("service")

    if not tenant or not service:
        raise HTTPException(status_code=400, detail="missing tenant/service in groupLabels")

    incident = {
        "tenant": tenant,
        "service": service,
        "alertname": payload.get("commonLabels", {}).get("alertname"),
        "status": payload.get("status"),
    }
    fingerprint = create_fingerprint(
        tenant,
        service,
        incident["alertname"],
    )

    key = f"sre:dedup:{fingerprint}"

    try:
        is_new = get_redis().set(
            key,
            "1",
            nx=True,
            ex=300,
        )

        if not is_new:
            return {"received": True, "duplicate": True}

    except redis.RedisError as e:
        raise HTTPException(
            status_code=503,
            detail=f"redis unavailable: {e}",
        )

    try:
        # kafka-python is sync: keep it off the event loop
        await run_in_threadpool(
            lambda: get_producer().send("incidents", incident).get(timeout=10)
        )
    except KafkaError as e:
        raise HTTPException(status_code=503, detail=f"kafka unavailable: {e}")

    return {"received": True}

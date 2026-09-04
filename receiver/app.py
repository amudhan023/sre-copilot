# receiver/app.py
from fastapi import FastAPI, Request, HTTPException
from functools import lru_cache
from starlette.concurrency import run_in_threadpool
import json
from kafka import KafkaProducer
from kafka.errors import KafkaError

import os

KAFKA_BOOTSTRAP = os.environ.get("KAFKA_BOOTSTRAP", "localhost:29092")

app = FastAPI()


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
    try:
        # kafka-python is sync: keep it off the event loop
        await run_in_threadpool(
            lambda: get_producer().send("incidents", incident).get(timeout=10)
        )
    except KafkaError as e:
        raise HTTPException(status_code=503, detail=f"kafka unavailable: {e}")

    return {"received": True}

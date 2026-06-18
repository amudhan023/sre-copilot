"""Redis client wrapper — metric baseline, dedup, KR pub/sub, active incident tracking."""
from __future__ import annotations
import json
import logging
import os
import time
from typing import Optional

import redis

logger = logging.getLogger(__name__)

_client: Optional[redis.Redis] = None


def get_client(max_retries: int = 20) -> redis.Redis:
    global _client
    if _client is not None:
        return _client
    host = os.getenv("REDIS_HOST", "redis")
    port = int(os.getenv("REDIS_PORT", "6379"))
    for attempt in range(1, max_retries + 1):
        try:
            r = redis.Redis(host=host, port=port, decode_responses=True)
            r.ping()
            _client = r
            logger.info("Redis connected at %s:%s", host, port)
            return _client
        except Exception as exc:
            logger.warning("Redis not ready (attempt %d/%d): %s", attempt, max_retries, exc)
            time.sleep(2)
    raise RuntimeError("Redis never became available.")


# ─── Deduplication ────────────────────────────────────────────────────────────

def set_dedup(key: str, ttl_seconds: int) -> bool:
    """Returns True if this is a new (non-duplicate) key, False if duplicate."""
    return bool(get_client().set(key, "1", nx=True, ex=ttl_seconds))


# ─── Metric rolling baseline ──────────────────────────────────────────────────

def push_metric(service: str, metric: str, value: float, max_len: int = 100) -> None:
    r = get_client()
    key = f"metric:{service}:{metric}"
    r.lpush(key, str(value))
    r.ltrim(key, 0, max_len - 1)
    r.expire(key, 3600)


def get_metric_history(service: str, metric: str, count: int = 50) -> list[float]:
    r = get_client()
    key = f"metric:{service}:{metric}"
    vals = r.lrange(key, 0, count - 1)
    return [float(v) for v in vals]


# ─── Generic key/value ────────────────────────────────────────────────────────

def set_json(key: str, value: dict, ttl: int = 3600) -> None:
    get_client().set(key, json.dumps(value), ex=ttl)


def get_json(key: str) -> Optional[dict]:
    val = get_client().get(key)
    return json.loads(val) if val else None


def set_str(key: str, value: str, ttl: int = 3600) -> None:
    get_client().set(key, value, ex=ttl)


def get_str(key: str) -> Optional[str]:
    return get_client().get(key)


def delete(key: str) -> None:
    get_client().delete(key)


def incr(key: str, ttl: int = 3600) -> int:
    r = get_client()
    val = r.incr(key)
    r.expire(key, ttl)
    return val


# ─── Active incident tracking (prevents duplicate incidents per service) ──────

def set_active_incident(service: str, incident_id: str, ttl: int = 1800) -> None:
    get_client().set(f"active_incident:{service}", incident_id, ex=ttl)


def get_active_incident(service: str) -> Optional[str]:
    return get_client().get(f"active_incident:{service}")


def delete_active_incident(service: str) -> None:
    get_client().delete(f"active_incident:{service}")


# ─── Knowledge Retrieval request/response via Redis lists ────────────────────
#
# Pattern: RPUSH request → BLPOP response (reliable, no message loss vs pubsub)
# Channel naming: kr:req:{request_id}, kr:res:{request_id}
#

def push_kr_request(request_id: str, payload: dict, ttl: int = 60) -> None:
    r = get_client()
    channel = f"kr:req:{request_id}"
    r.rpush(channel, json.dumps(payload))
    r.expire(channel, ttl)


def pop_kr_request(timeout_seconds: int = 10) -> Optional[tuple[str, dict]]:
    """
    Blocking pop on all kr:req:* channels.
    Returns (request_id, payload) or None on timeout.
    KR Agent calls this in a loop.
    """
    r = get_client()
    # Scan for any pending request key
    keys = r.keys("kr:req:*")
    if not keys:
        time.sleep(0.1)
        return None
    # BLPOP on found keys
    result = r.blpop(keys, timeout=timeout_seconds)
    if result is None:
        return None
    key, raw = result
    request_id = key.split("kr:req:")[1]
    return request_id, json.loads(raw)


def push_kr_response(request_id: str, payload: dict, ttl: int = 60) -> None:
    r = get_client()
    channel = f"kr:res:{request_id}"
    r.rpush(channel, json.dumps(payload))
    r.expire(channel, ttl)


def wait_kr_response(request_id: str, timeout_seconds: int = 15) -> Optional[dict]:
    """
    Blocking wait for a KR response. Investigation Agent calls this.
    Returns the response payload or None on timeout.
    """
    r = get_client()
    channel = f"kr:res:{request_id}"
    result = r.blpop([channel], timeout=timeout_seconds)
    if result is None:
        return None
    _, raw = result
    return json.loads(raw)


# ─── Failure state sharing (between traffic-simulator and failure-injector) ───

def set_failure_state(service: str, state: dict, ttl: int = 1800) -> None:
    set_json(f"failure:state:{service}", state, ttl=ttl)


def get_failure_state(service: str) -> Optional[dict]:
    return get_json(f"failure:state:{service}")


def clear_failure_state(service: str) -> None:
    delete(f"failure:state:{service}")

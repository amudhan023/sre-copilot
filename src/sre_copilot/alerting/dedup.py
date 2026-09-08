from __future__ import annotations

import logging
import os
from typing import Protocol

logger = logging.getLogger(__name__)


class Deduplicator(Protocol):
    def claim(self, key: str, ttl_seconds: int) -> bool: ...
    def release(self, key: str) -> None: ...


class RedisDeduplicator:
    """Redis-backed best-effort deduplication using atomic SET NX EX."""

    def __init__(self, url: str | None = None):
        import redis
        self._client = redis.Redis.from_url(
            url or os.getenv("REDIS_URL", "redis://localhost:6379/0"),
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )

    def claim(self, key: str, ttl_seconds: int) -> bool:
        try:
            return bool(self._client.set(key, "1", nx=True, ex=ttl_seconds))
        except Exception:
            logger.exception("Redis dedup unavailable; failing open for %s", key)
            return True

    def release(self, key: str) -> None:
        try:
            self._client.delete(key)
        except Exception:
            logger.exception("Could not release Redis dedup key %s", key)


class InMemoryDeduplicator:
    """Small test double with the same claim/release semantics."""

    def __init__(self):
        self._keys: set[str] = set()

    def claim(self, key: str, ttl_seconds: int) -> bool:
        del ttl_seconds
        if key in self._keys:
            return False
        self._keys.add(key)
        return True

    def release(self, key: str) -> None:
        self._keys.discard(key)

"""
Stub out heavy infrastructure dependencies for unit tests.

This conftest runs before any test module is imported, so the stubs are in place
when agent source files do their top-level imports of confluent_kafka, qdrant_client, etc.
"""
import sys
from unittest.mock import MagicMock

def _stub(name, *sub_names):
    mod = MagicMock()
    sys.modules[name] = mod
    for sub in sub_names:
        full = f"{name}.{sub}"
        sys.modules[full] = MagicMock()
    return mod

# Kafka
_stub("confluent_kafka", "admin")
_stub("confluent_kafka.admin")

# Qdrant
_stub("qdrant_client")
_stub("qdrant_client.models")

# Sentence transformers
_stub("sentence_transformers")

# Psycopg2 (database)
pg_mock = _stub("psycopg2", "pool", "extras")
_stub("psycopg2.pool")
_stub("psycopg2.extras")

# Redis
_stub("redis")

# Anthropic
anth = _stub("anthropic")
anth.RateLimitError = type("RateLimitError", (Exception,), {})
anth.APIError       = type("APIError",       (Exception,), {})

# Requests (for ingestion services)
_stub("requests")

# Aiosmtplib
_stub("aiosmtplib")

# FastAPI (not needed for unit tests that test pure logic)
_stub("fastapi")
_stub("fastapi.responses")
_stub("fastapi.middleware")
_stub("fastapi.middleware.cors")
_stub("uvicorn")

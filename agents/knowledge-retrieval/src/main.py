"""
Knowledge Retrieval Agent — serves vector search requests from Investigation Agent.

Communication pattern:
  - Investigation Agent pushes KRRequest to Redis key kr:req:{request_id}
  - This agent pops requests, performs parallel Qdrant searches, pushes KRResponse
    to kr:res:{request_id}

Collections searched:
  - incidents:      semantic similarity for historical incident lookup
  - runbooks:       anomaly-type + symptom search for operational procedures
  - architecture:   exact service metadata retrieval
  - deployments:    time-range + service filter for deployment correlation
  - postmortems:    pattern matching for recurring systemic issues
"""
from __future__ import annotations
import json
import logging
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Optional

from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchValue, Range

sys.path.insert(0, "/app")
from shared.models import KRRequest, KRResponse, KnowledgeChunk, KRQueryType, now_ms
from shared.redis_client import pop_kr_request, push_kr_response

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("knowledge-retrieval-agent")

QDRANT_URL     = os.getenv("QDRANT_URL", "http://qdrant:6333")
HTTP_PORT      = int(os.getenv("HTTP_PORT", "8203"))
MAX_TOKENS     = int(os.getenv("MAX_CONTEXT_TOKENS", "4096"))
EMBEDDING_DIM  = int(os.getenv("EMBEDDING_DIM", "384"))   # 384 for local model, 3072 for text-embedding-3-large

_requests_served = 0
_qdrant: Optional[QdrantClient] = None

# ─── Embedding model ──────────────────────────────────────────────────────────

_embed_model = None

def get_embedder():
    global _embed_model
    if _embed_model is None:
        try:
            from sentence_transformers import SentenceTransformer
            model_name = os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2")
            _embed_model = SentenceTransformer(model_name)
            logger.info("Loaded embedding model: %s", model_name)
        except ImportError:
            logger.error("sentence-transformers not installed.")
            raise
    return _embed_model


def embed(text: str) -> list[float]:
    model = get_embedder()
    vec = model.encode(text, normalize_embeddings=True)
    return vec.tolist()


# ─── Qdrant client ────────────────────────────────────────────────────────────

def get_qdrant() -> QdrantClient:
    global _qdrant
    if _qdrant is None:
        for attempt in range(20):
            try:
                _qdrant = QdrantClient(url=QDRANT_URL)
                _qdrant.get_collections()
                logger.info("Qdrant connected at %s", QDRANT_URL)
                break
            except Exception as exc:
                logger.warning("Qdrant not ready (attempt %d/20): %s", attempt + 1, exc)
                time.sleep(5)
        else:
            raise RuntimeError("Qdrant never became available.")
    return _qdrant


# ─── Token budget enforcement ────────────────────────────────────────────────

APPROX_CHARS_PER_TOKEN = 4


def _count_tokens(text: str) -> int:
    return len(text) // APPROX_CHARS_PER_TOKEN


def _enforce_token_budget(
    chunks: list[KnowledgeChunk],
    max_tokens: int = MAX_TOKENS,
) -> tuple[list[KnowledgeChunk], int]:
    """
    Trim chunks to fit within token budget.
    Priority order: incidents > runbooks > architecture > deployments > postmortems
    """
    priority_order = ["incidents", "runbooks", "architecture", "deployments", "postmortems"]
    by_type: dict[str, list[KnowledgeChunk]] = {t: [] for t in priority_order}

    for c in chunks:
        bucket = c.source_type if c.source_type in by_type else "architecture"
        by_type[bucket].append(c)

    result: list[KnowledgeChunk] = []
    total_tokens = 0

    for source_type in priority_order:
        for chunk in by_type[source_type]:
            tokens = _count_tokens(chunk.content)
            if total_tokens + tokens > max_tokens:
                break
            result.append(chunk)
            total_tokens += tokens

    return result, total_tokens


# ─── Per-query-type search handlers ──────────────────────────────────────────

def _search_incidents(req: KRRequest) -> list[KnowledgeChunk]:
    client = get_qdrant()
    query_text = f"{req.anomaly_type} {' '.join(req.affected_services)} {req.query_text}"
    vector = embed(query_text)

    try:
        results = client.search(
            collection_name="incidents",
            query_vector=vector,
            limit=20,
            query_filter=Filter(
                must=[
                    FieldCondition(key="environment", match=MatchValue(value="production")),
                    FieldCondition(key="resolved",    match=MatchValue(value=True)),
                ]
            ),
            with_payload=True,
        )
    except Exception as exc:
        logger.warning("Qdrant incidents search failed: %s", exc)
        return []

    # Group by incident_id and take best chunk per incident
    seen: dict[str, float] = {}
    chunks: list[KnowledgeChunk] = []

    for hit in results:
        p = hit.payload or {}
        incident_id = p.get("incident_id", hit.id)
        score = hit.score

        if incident_id in seen and seen[incident_id] >= score:
            continue
        seen[incident_id] = score

        content = (
            f"Incident: {p.get('incident_id', '')}\n"
            f"Service: {p.get('service_name', '')} | Severity: {p.get('severity', '')} | "
            f"Type: {p.get('anomaly_type', '')}\n"
            f"Symptoms: {p.get('symptoms_observed', '')}\n"
            f"Root Cause: {p.get('root_cause', '')}\n"
            f"Resolution: {p.get('resolution_steps', '')}\n"
            f"MTTR: {p.get('mttr_minutes', '?')} minutes"
        )
        chunks.append(KnowledgeChunk(
            chunk_id=str(hit.id),
            source_type="incidents",
            source_id=incident_id,
            title=f"{p.get('incident_id', 'Incident')} — {p.get('anomaly_type', '')}",
            content=content,
            score=score,
            metadata=p,
        ))

    # Return top 5 after deduplication
    chunks.sort(key=lambda c: c.score, reverse=True)
    return chunks[:5]


def _search_runbooks(req: KRRequest) -> list[KnowledgeChunk]:
    client = get_qdrant()
    query_text = f"{req.anomaly_type} {req.query_text} troubleshooting remediation"
    vector = embed(query_text)

    try:
        # Try with anomaly_type filter first
        results = client.search(
            collection_name="runbooks",
            query_vector=vector,
            limit=10,
            with_payload=True,
        )
    except Exception as exc:
        logger.warning("Qdrant runbooks search failed: %s", exc)
        return []

    chunks: list[KnowledgeChunk] = []
    for hit in results:
        p = hit.payload or {}
        score = hit.score

        # Boost score for service-specific runbooks
        if req.affected_services:
            runbook_services = p.get("services", [])
            if any(s in runbook_services for s in req.affected_services):
                score = min(1.0, score + 0.15)

        # Boost for matching anomaly type
        anomaly_types = p.get("anomaly_types", [])
        if req.anomaly_type in anomaly_types:
            score = min(1.0, score + 0.10)

        content = (
            f"Runbook: {p.get('title', '')}\n"
            f"For: {p.get('anomaly_types', [])} on {p.get('services', [])}\n\n"
            f"{p.get('content', '')}"
        )
        chunks.append(KnowledgeChunk(
            chunk_id=str(hit.id),
            source_type="runbooks",
            source_id=p.get("runbook_id", str(hit.id)),
            title=p.get("title", "Runbook"),
            content=content,
            score=score,
            metadata=p,
        ))

    chunks.sort(key=lambda c: c.score, reverse=True)
    return chunks[:5]


def _search_architecture(req: KRRequest) -> list[KnowledgeChunk]:
    client = get_qdrant()
    chunks: list[KnowledgeChunk] = []

    for svc in req.affected_services:
        try:
            results = client.search(
                collection_name="architecture",
                query_vector=embed(f"{svc} service architecture dependencies"),
                limit=3,
                query_filter=Filter(
                    must=[FieldCondition(key="service_name", match=MatchValue(value=svc))]
                ),
                with_payload=True,
            )
        except Exception as exc:
            logger.warning("Qdrant architecture search failed for %s: %s", svc, exc)
            continue

        for hit in results:
            p = hit.payload or {}
            content = (
                f"Service: {p.get('service_name', svc)}\n"
                f"Team: {p.get('team_owner', '')} | Criticality: {p.get('criticality', '')}\n"
                f"SLA: P99 < {p.get('sla_p99_latency_ms', '?')}ms, error rate < {p.get('sla_error_rate_pct', '?')}%\n"
                f"On-call: {p.get('on_call_rotation', '')}\n"
                f"Downstream: {p.get('downstream_services', [])}\n"
                f"Description: {p.get('description', '')}\n"
                f"Known failure modes: {p.get('known_failure_modes', [])}"
            )
            chunks.append(KnowledgeChunk(
                chunk_id=str(hit.id),
                source_type="architecture",
                source_id=svc,
                title=f"Architecture: {svc}",
                content=content,
                score=hit.score,
                metadata=p,
            ))

    return chunks


def _search_deployments(req: KRRequest) -> list[KnowledgeChunk]:
    client = get_qdrant()
    chunks: list[KnowledgeChunk] = []

    window_start = (req.incident_time - req.time_window_minutes * 60 * 1000) / 1000
    window_end   = req.incident_time / 1000

    for svc in req.affected_services:
        try:
            results = client.search(
                collection_name="deployments",
                query_vector=embed(f"{svc} deployment release change"),
                limit=5,
                query_filter=Filter(
                    must=[
                        FieldCondition(key="service_name", match=MatchValue(value=svc)),
                        FieldCondition(key="deployed_at_epoch",
                                       range=Range(gte=window_start, lte=window_end)),
                    ]
                ),
                with_payload=True,
            )
        except Exception:
            # Fall back to unfiltered search if time-range filter fails
            try:
                results = client.search(
                    collection_name="deployments",
                    query_vector=embed(f"{svc} deployment release"),
                    limit=5,
                    query_filter=Filter(
                        must=[FieldCondition(key="service_name", match=MatchValue(value=svc))]
                    ),
                    with_payload=True,
                )
            except Exception as exc:
                logger.warning("Deployment search failed for %s: %s", svc, exc)
                continue

        for hit in results:
            p = hit.payload or {}
            risks = p.get("known_risks", [])
            risk_text = "\n".join(f"  - {r}" for r in risks) if risks else "  None flagged"
            content = (
                f"Deployment: {p.get('service_name', '')} {p.get('version', '')}\n"
                f"Deployed at: {p.get('deployed_at', '')} by {p.get('deployed_by', '')}\n"
                f"Change type: {p.get('change_type', '')}\n"
                f"Description: {p.get('description', '')}\n"
                f"Known risks:\n{risk_text}"
            )
            chunks.append(KnowledgeChunk(
                chunk_id=str(hit.id),
                source_type="deployments",
                source_id=p.get("deployment_id", str(hit.id)),
                title=f"Deploy: {p.get('service_name', '')} {p.get('version', '')}",
                content=content,
                score=hit.score,
                metadata=p,
            ))

    return chunks


def _search_postmortems(req: KRRequest) -> list[KnowledgeChunk]:
    client = get_qdrant()
    query_text = f"{req.anomaly_type} recurring systemic contributing factors"
    vector = embed(query_text)

    try:
        results = client.search(
            collection_name="postmortems",
            query_vector=vector,
            limit=5,
            with_payload=True,
        )
    except Exception as exc:
        logger.warning("Qdrant postmortems search failed: %s", exc)
        return []

    chunks: list[KnowledgeChunk] = []
    for hit in results:
        p = hit.payload or {}
        content = (
            f"Postmortem: {p.get('incident_id', '')}\n"
            f"Service: {p.get('service', '')} | Category: {p.get('root_cause_category', '')}\n"
            f"Contributing factors:\n{p.get('contributing_factors', '')}\n"
            f"Preventative actions:\n{p.get('preventative_actions', '')}"
        )
        chunks.append(KnowledgeChunk(
            chunk_id=str(hit.id),
            source_type="postmortems",
            source_id=p.get("incident_id", str(hit.id)),
            title=f"Postmortem: {p.get('incident_id', '')}",
            content=content,
            score=hit.score,
            metadata=p,
        ))

    return chunks


# ─── Request handler ──────────────────────────────────────────────────────────

QUERY_HANDLERS = {
    KRQueryType.INCIDENT_SIMILARITY:  _search_incidents,
    KRQueryType.RUNBOOK_LOOKUP:       _search_runbooks,
    KRQueryType.ARCHITECTURE_CONTEXT: _search_architecture,
    KRQueryType.DEPLOYMENT_NOTES:     _search_deployments,
    KRQueryType.POSTMORTEM_PATTERNS:  _search_postmortems,
}


def handle_request(request_id: str, payload: dict) -> None:
    global _requests_served
    try:
        req = KRRequest(**payload)
        query_type = KRQueryType(req.query_type)

        handler = QUERY_HANDLERS.get(query_type)
        if not handler:
            logger.warning("Unknown query type: %s", req.query_type)
            push_kr_response(request_id, KRResponse(
                request_id=request_id,
                incident_id=req.incident_id,
                query_type=req.query_type,
                error=f"Unknown query type: {req.query_type}",
            ).model_dump())
            return

        chunks = handler(req)
        trimmed, total_tokens = _enforce_token_budget(chunks)

        response = KRResponse(
            request_id=request_id,
            incident_id=req.incident_id,
            query_type=req.query_type,
            chunks=trimmed,
            total_tokens=total_tokens,
        )
        push_kr_response(request_id, response.model_dump())
        _requests_served += 1
        logger.info(
            "Served %s request for incident %s: %d chunks (%d tokens)",
            query_type.value, req.incident_id, len(trimmed), total_tokens,
        )

    except Exception as exc:
        logger.exception("Error handling KR request %s: %s", request_id, exc)
        push_kr_response(request_id, KRResponse(
            request_id=request_id,
            incident_id=payload.get("incident_id", ""),
            query_type=payload.get("query_type", ""),
            error=str(exc),
        ).model_dump())


# ─── HTTP health endpoint ─────────────────────────────────────────────────────

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            body = (
                f'{{"status":"healthy","service":"knowledge-retrieval-agent",'
                f'"requests_served":{_requests_served}}}'
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):
        pass


# ─── Main loop ────────────────────────────────────────────────────────────────

def main() -> None:
    server = HTTPServer(("0.0.0.0", HTTP_PORT), HealthHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    logger.info("Knowledge Retrieval Agent starting on port %d", HTTP_PORT)

    # Warm up embedding model and Qdrant connection
    try:
        get_qdrant()
        embed("warm up embedding model")
        logger.info("Embedding model and Qdrant ready.")
    except Exception as exc:
        logger.warning("Startup warm-up failed: %s", exc)

    logger.info("Listening for KR requests on Redis...")

    while True:
        result = pop_kr_request(timeout_seconds=5)
        if result is None:
            continue
        request_id, payload = result
        handle_request(request_id, payload)


if __name__ == "__main__":
    main()

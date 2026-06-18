"""
Knowledge Seeder — one-shot job that populates all 5 Qdrant collections.

Collections:
  incidents    — 50 historical incidents (chunked into 4 types each)
  runbooks     — 9 runbook markdown files (chunked by section)
  architecture — 6 service documents
  deployments  — 30 deployment records
  postmortems  — 10 postmortem markdown files

Embedding: sentence-transformers all-MiniLM-L6-v2 (384 dims, no API key needed)
Idempotent: skips existing vectors based on document hash stored in payload.
"""
from __future__ import annotations
import hashlib
import json
import logging
import os
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance, VectorParams, PointStruct, Filter, FieldCondition, MatchValue,
    PayloadSchemaType,
)
from sentence_transformers import SentenceTransformer

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("knowledge-seeder")

QDRANT_URL     = os.getenv("QDRANT_URL",     "http://qdrant:6333")
KNOWLEDGE_ROOT = Path(__file__).parent.parent.parent  # /app/knowledge

MODEL_NAME  = os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2")
VECTOR_DIM  = 384  # all-MiniLM-L6-v2 output dimension
BATCH_SIZE  = 50   # upsert batch size


# ─── Qdrant setup ─────────────────────────────────────────────────────────────

def get_qdrant() -> QdrantClient:
    for attempt in range(20):
        try:
            client = QdrantClient(url=QDRANT_URL)
            client.get_collections()
            logger.info("Qdrant connected at %s", QDRANT_URL)
            return client
        except Exception as exc:
            logger.warning("Qdrant not ready (attempt %d/20): %s", attempt + 1, exc)
            time.sleep(5)
    raise RuntimeError("Qdrant never became available.")


def ensure_collection(client: QdrantClient, name: str, indexed_fields: list[str] = None) -> None:
    existing = [c.name for c in client.get_collections().collections]
    if name not in existing:
        client.create_collection(
            collection_name=name,
            vectors_config=VectorParams(size=VECTOR_DIM, distance=Distance.COSINE),
        )
        logger.info("Created collection: %s", name)
        # Create payload indexes for filtering
        for field in (indexed_fields or []):
            try:
                client.create_payload_index(name, field, PayloadSchemaType.KEYWORD)
            except Exception:
                pass
    else:
        logger.info("Collection exists: %s", name)


# ─── Embedding ────────────────────────────────────────────────────────────────

_model = None

def get_model() -> SentenceTransformer:
    global _model
    if _model is None:
        logger.info("Loading embedding model: %s", MODEL_NAME)
        _model = SentenceTransformer(MODEL_NAME)
        logger.info("Embedding model loaded.")
    return _model


def embed(texts: list[str]) -> list[list[float]]:
    model = get_model()
    vecs = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
    return [v.tolist() for v in vecs]


def doc_hash(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()[:16]


# ─── Upsert helper ────────────────────────────────────────────────────────────

def upsert_points(client: QdrantClient, collection: str, points: list[PointStruct]) -> int:
    if not points:
        return 0
    for i in range(0, len(points), BATCH_SIZE):
        batch = points[i:i + BATCH_SIZE]
        client.upsert(collection_name=collection, points=batch)
    return len(points)


# ─── Chunkers ─────────────────────────────────────────────────────────────────

def chunk_by_sections(text: str, max_chunk_tokens: int = 400) -> list[str]:
    """Split markdown by ## and ### headers."""
    sections = re.split(r"(?=^#{2,3} )", text, flags=re.MULTILINE)
    chunks = []
    for section in sections:
        section = section.strip()
        if not section:
            continue
        # Rough token estimate: chars / 4
        if len(section) / 4 <= max_chunk_tokens:
            chunks.append(section)
        else:
            # Split long sections by paragraph
            paras = section.split("\n\n")
            current = ""
            for p in paras:
                if (len(current) + len(p)) / 4 <= max_chunk_tokens:
                    current = (current + "\n\n" + p).strip()
                else:
                    if current:
                        chunks.append(current)
                    current = p
            if current:
                chunks.append(current)
    return chunks or [text[:1600]]  # fallback


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Extract YAML frontmatter from markdown."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("---", 3)
    if end == -1:
        return {}, text
    fm_text = text[3:end]
    body    = text[end + 3:].strip()
    meta: dict = {}
    for line in fm_text.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            raw = v.strip().strip('"')
            # Parse lists like ["A", "B"]
            if raw.startswith("["):
                try:
                    meta[k.strip()] = json.loads(raw)
                except Exception:
                    meta[k.strip()] = raw
            else:
                meta[k.strip()] = raw
    return meta, body


# ─── Collection seeders ───────────────────────────────────────────────────────

def seed_incidents(client: QdrantClient) -> int:
    path = KNOWLEDGE_ROOT / "incidents" / "incidents.json"
    if not path.exists():
        logger.warning("incidents.json not found")
        return 0

    data = json.loads(path.read_text())
    incidents = data.get("incidents", [])
    points: list[PointStruct] = []

    for inc in incidents:
        iid = inc["incident_id"]

        # 4 chunks per incident
        chunk_defs = [
            ("summary",    f"{iid} {inc.get('anomaly_type','')} {inc.get('service_name','')} {inc.get('severity','')} {inc.get('symptoms_observed','')}"),
            ("symptoms",   inc.get("symptoms_observed", "")),
            ("root_cause", inc.get("root_cause", "")),
            ("resolution", inc.get("resolution_steps", "")),
        ]
        for chunk_type, text in chunk_defs:
            if not text.strip():
                continue
            h = doc_hash(f"{iid}:{chunk_type}")
            payload = {
                "incident_id":          iid,
                "chunk_type":           chunk_type,
                "service_name":         inc.get("service_name", ""),
                "severity":             inc.get("severity", ""),
                "anomaly_type":         inc.get("anomaly_type", ""),
                "environment":          inc.get("environment", "production"),
                "resolved":             inc.get("resolved", True),
                "root_cause_category":  inc.get("root_cause_category", ""),
                "mttr_minutes":         inc.get("mttr_minutes", 0),
                "deployment_correlated": inc.get("deployment_correlated", False),
                "content":              text[:800],
                "doc_hash":             h,
                # Epoch seconds for time-range filters
                "occurred_at_epoch":    0,
            }
            points.append(PointStruct(id=str(uuid.uuid5(uuid.NAMESPACE_DNS, h)), vector=[], payload=payload))

    # Embed all texts in one batch
    texts = [p.payload["content"] for p in points]
    vectors = embed(texts)
    for p, v in zip(points, vectors):
        p.vector = v

    return upsert_points(client, "incidents", points)


def seed_runbooks(client: QdrantClient) -> int:
    runbooks_dir = KNOWLEDGE_ROOT / "runbooks"
    if not runbooks_dir.exists():
        return 0

    points: list[PointStruct] = []
    for md_file in sorted(runbooks_dir.glob("*.md")):
        text = md_file.read_text()
        meta, body = parse_frontmatter(text)
        chunks = chunk_by_sections(body)

        for i, chunk in enumerate(chunks):
            if not chunk.strip():
                continue
            h = doc_hash(f"{md_file.stem}:chunk{i}")
            payload = {
                "runbook_id":    meta.get("runbook_id", md_file.stem),
                "title":         meta.get("title", md_file.stem),
                "anomaly_types": meta.get("anomaly_types", []),
                "services":      meta.get("services", []),
                "tags":          meta.get("tags", []),
                "chunk_index":   i,
                "content":       chunk[:800],
                "doc_hash":      h,
            }
            points.append(PointStruct(id=str(uuid.uuid5(uuid.NAMESPACE_DNS, h)), vector=[], payload=payload))

    texts = [p.payload["content"] for p in points]
    vectors = embed(texts)
    for p, v in zip(points, vectors):
        p.vector = v

    return upsert_points(client, "runbooks", points)


def seed_architecture(client: QdrantClient) -> int:
    path = KNOWLEDGE_ROOT / "architecture" / "services.json"
    if not path.exists():
        return 0

    data = json.loads(path.read_text())
    services = data.get("services", [])
    points: list[PointStruct] = []

    for svc in services:
        name = svc.get("service_name", "")
        text = (
            f"{name} {svc.get('description','')} "
            f"team:{svc.get('team_owner','')} "
            f"criticality:{svc.get('criticality','')} "
            f"downstream:{svc.get('downstream_services',[])} "
            f"failure_modes:{svc.get('known_failure_modes',[])} "
            f"sla_p99:{svc.get('sla_p99_latency_ms','')}ms "
            f"error_rate_sla:{svc.get('sla_error_rate_pct','')}%"
        )
        h = doc_hash(name)
        payload = {
            "service_name":       name,
            "team_owner":         svc.get("team_owner", ""),
            "criticality":        svc.get("criticality", ""),
            "sla_p99_latency_ms": svc.get("sla_p99_latency_ms", 500),
            "sla_error_rate_pct": svc.get("sla_error_rate_pct", 1.0),
            "on_call_rotation":   svc.get("on_call_rotation", ""),
            "downstream_services":svc.get("downstream_services", []),
            "known_failure_modes":svc.get("known_failure_modes", []),
            "description":        svc.get("description", "")[:400],
            "content":            text[:800],
            "doc_hash":           h,
        }
        points.append(PointStruct(id=str(uuid.uuid5(uuid.NAMESPACE_DNS, h)), vector=[], payload=payload))

    texts = [p.payload["content"] for p in points]
    vectors = embed(texts)
    for p, v in zip(points, vectors):
        p.vector = v

    return upsert_points(client, "architecture", points)


def seed_deployments(client: QdrantClient) -> int:
    path = KNOWLEDGE_ROOT / "deployments" / "deployments.json"
    if not path.exists():
        return 0

    data = json.loads(path.read_text())
    deployments = data.get("deployments", [])
    points: list[PointStruct] = []

    for dep in deployments:
        text = (
            f"{dep.get('service_name','')} {dep.get('version','')} "
            f"{dep.get('change_type','')} {dep.get('description','')} "
            f"risks:{dep.get('known_risks',[])}"
        )
        h = doc_hash(dep.get("deployment_id", text[:50]))

        import datetime as _dt
        dep_ts = 0
        if dep.get("deployed_at"):
            try:
                dep_ts = _dt.datetime.fromisoformat(dep["deployed_at"].replace("Z", "+00:00")).timestamp()
            except Exception:
                pass

        payload = {
            "deployment_id":      dep.get("deployment_id", ""),
            "service_name":       dep.get("service_name", ""),
            "version":            dep.get("version", ""),
            "deployed_at":        dep.get("deployed_at", ""),
            "deployed_at_epoch":  dep_ts,
            "change_type":        dep.get("change_type", ""),
            "known_risks":        dep.get("known_risks", []),
            "requires_migration": dep.get("requires_migration", False),
            "description":        dep.get("description", "")[:400],
            "content":            text[:800],
            "doc_hash":           h,
        }
        points.append(PointStruct(id=str(uuid.uuid5(uuid.NAMESPACE_DNS, h)), vector=[], payload=payload))

    texts = [p.payload["content"] for p in points]
    vectors = embed(texts)
    for p, v in zip(points, vectors):
        p.vector = v

    return upsert_points(client, "deployments", points)


def seed_postmortems(client: QdrantClient) -> int:
    pm_dir = KNOWLEDGE_ROOT / "postmortems"
    if not pm_dir.exists():
        return 0

    points: list[PointStruct] = []
    for md_file in sorted(pm_dir.glob("*.md")):
        text = md_file.read_text()
        meta, body = parse_frontmatter(text)

        # Extract sections
        sections = {"contributing_factors": "", "preventative_actions": ""}
        current_section = None
        for line in body.splitlines():
            if "Contributing Factor" in line:
                current_section = "contributing_factors"
            elif "Preventative" in line or "Action" in line:
                current_section = "preventative_actions"
            elif line.startswith("## "):
                current_section = None
            elif current_section:
                sections[current_section] += line + "\n"

        text_for_embed = (
            f"{meta.get('incident_id','')} {meta.get('service','')} "
            f"{meta.get('root_cause_category','')} "
            f"{sections['contributing_factors']} {sections['preventative_actions']}"
        )
        h = doc_hash(md_file.stem)
        payload = {
            "incident_id":          meta.get("incident_id", md_file.stem),
            "service":              meta.get("service", ""),
            "severity":             meta.get("severity", "HIGH"),
            "root_cause_category":  meta.get("root_cause_category", ""),
            "occurred_at":          meta.get("occurred_at", ""),
            "mttr_minutes":         int(meta.get("mttr_minutes", 0)),
            "contributing_factors": sections["contributing_factors"][:600],
            "preventative_actions": sections["preventative_actions"][:600],
            "content":              text_for_embed[:800],
            "doc_hash":             h,
        }
        points.append(PointStruct(id=str(uuid.uuid5(uuid.NAMESPACE_DNS, h)), vector=[], payload=payload))

    texts = [p.payload["content"] for p in points]
    vectors = embed(texts)
    for p, v in zip(points, vectors):
        p.vector = v

    return upsert_points(client, "postmortems", points)


# ─── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    logger.info("Knowledge Seeder starting.")
    client = get_qdrant()

    # Create all 5 collections
    ensure_collection(client, "incidents",    ["service_name", "anomaly_type", "environment", "resolved"])
    ensure_collection(client, "runbooks",     ["anomaly_types", "services"])
    ensure_collection(client, "architecture", ["service_name", "criticality"])
    ensure_collection(client, "deployments",  ["service_name", "change_type"])
    ensure_collection(client, "postmortems",  ["root_cause_category", "service"])

    # Seed each collection
    counts = {
        "incidents":    seed_incidents(client),
        "runbooks":     seed_runbooks(client),
        "architecture": seed_architecture(client),
        "deployments":  seed_deployments(client),
        "postmortems":  seed_postmortems(client),
    }

    total = sum(counts.values())
    logger.info("Knowledge Seeder complete. Vectors upserted: %s | Total: %d", counts, total)


if __name__ == "__main__":
    main()

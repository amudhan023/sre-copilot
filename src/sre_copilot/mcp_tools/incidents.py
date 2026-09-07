"""
Historical incident retrieval for the SRE Copilot.

Purpose:
    Finds historically similar incidents that can provide additional
    evidence during an SRE investigation.

High-level flow:
    Current incident context
        -> Hybrid retrieval (dense + sparse)
        -> RRF candidate ranking
        -> Cross-encoder reranking
        -> Document-level deduplication
        -> Top historical incidents

Important:
    Historical incidents are supporting evidence, not proof of the
    current incident's root cause.

    The LLM must distinguish between:
      - evidence observed in the current incident
      - patterns observed in historical incidents
      - hypotheses inferred from those patterns

Tenant isolation:
    Every retrieval is scoped to the incident's tenant. The tenant is
    supplied by the caller (the agent state, via the MCP tool) and is
    passed straight through to ``HybridRetriever.retrieve``. There is no
    default and no unscoped search path.

The underlying RAG pipeline uses:
    BGE-M3 -> pgvector + PostgreSQL FTS -> RRF -> BGE reranker
"""

from sre_copilot.rag.retriever import HybridRetriever


DENSE_LIMIT = 20
SPARSE_LIMIT = 20
RRF_LIMIT = 20
MAX_INCIDENTS = 5

# Rerank the whole RRF candidate set instead of just the first MAX_INCIDENTS
# chunks. Several chunks can belong to the same historical incident, so
# deduplication needs more chunks than the number of incidents it must
# return - otherwise five chunks from two incidents can only ever yield two
# results. This costs no extra model work: the cross-encoder scores every
# candidate it is handed and only then truncates to top_k (see
# rag/reranker.py), so a larger top_k just keeps rows that were already
# scored. The MCP response is still capped at MAX_INCIDENTS.
TOP_K = RRF_LIMIT

# The retriever owns the BGE-M3 embedder and the BGE cross-encoder, so it is
# built once and reused. It is created lazily rather than at import time so
# that importing this module (for the MCP server, or for a unit test) does
# not load the models until an incident search actually needs them.
_retriever: HybridRetriever | None = None


def get_retriever() -> HybridRetriever:
    """Return the shared retriever, building it on first use."""
    global _retriever

    if _retriever is None:
        _retriever = HybridRetriever()

    return _retriever


def find_similar_incidents(
    tenant: str,
    service: str,
    query: str,
) -> dict:
    """Return historical incidents similar to the current incident.

    ``tenant`` scopes the retrieval; the search never crosses tenants.
    """
    if not tenant or not tenant.strip():
        raise ValueError("tenant is required for historical incident search")

    retrieval = get_retriever().retrieve(
        query=query,
        tenant=tenant,
        dense_limit=DENSE_LIMIT,
        sparse_limit=SPARSE_LIMIT,
        rrf_limit=RRF_LIMIT,
        top_k=TOP_K,
    )

    # Results arrive sorted by rerank_score, so the first chunk seen for a
    # historical incident is its best-ranked chunk. Later chunks from the
    # same incident would only repeat it, so they are dropped.
    incidents = []
    seen_documents = set()

    for result in retrieval["results"]:
        document_id = result["document_id"]

        if document_id in seen_documents:
            continue

        seen_documents.add(document_id)

        incidents.append(
            {
                "incident_id": document_id,
                "title": result["title"],
                "content": result["content"],
                "score": result["rerank_score"],
            }
        )

        if len(incidents) == MAX_INCIDENTS:
            break

    return {
        "tenant": tenant,
        "service": service,
        "query": query,
        "incidents": incidents,
    }

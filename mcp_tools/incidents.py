"""
Historical incident retrieval for the SRE Copilot.

Purpose:
    Finds historically similar incidents that can provide additional
    evidence during an SRE investigation.

High-level flow:
    Current incident context
        -> Hybrid retrieval
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

The underlying RAG pipeline uses:
    BGE-M3 -> pgvector + PostgreSQL FTS -> RRF -> BGE reranker
"""

from mcp_tools.rag.retriever import HybridRetriever
from mcp_tools.rag.reranker import BGEReranker


retriever = HybridRetriever()
reranker = BGEReranker()


def find_similar_incidents(
    service: str,
    query: str,
) -> dict:
    candidates = retriever.hybrid_search(
        query=query,
        service=service,
        candidate_limit=20,
        limit=20,
    )

    reranked = reranker.rerank(
        query=query,
        results=candidates,
        top_k=10,
    )

    # Keep only the best chunk from each historical incident.
    incidents = []
    seen_documents = set()

    for result in reranked:
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

    return {
        "service": service,
        "query": query,
        "incidents": incidents[:5],
    }
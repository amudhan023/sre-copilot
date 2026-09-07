import pytest

from sre_copilot.mcp_tools import incidents

# Tests the historical incident MCP tool against a fake HybridRetriever, so
# the real BGE-M3 / BGE reranker models and PostgreSQL are never touched.
# What matters here is the contract between the MCP tool and the retriever:
# the tenant reaches retrieve() untouched, and chunks collapse back into one
# entry per historical incident.


def chunk(document_id, chunk_index, rerank_score):
    return {
        "chunk_id": f"{document_id}-chunk-{chunk_index}",
        "document_id": document_id,
        "tenant": "default",
        "service": "payment-api",
        "document_type": "incident",
        "title": f"{document_id} summary",
        "content": f"{document_id} chunk {chunk_index}",
        "rrf_score": 0.03,
        "rerank_score": rerank_score,
    }


class FakeRetriever:
    """Records the arguments retrieve() was called with."""

    def __init__(self, results):
        self.results = results
        self.calls = []

    def retrieve(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "query": kwargs["query"],
            "tenant": kwargs["tenant"],
            "dense": [],
            "sparse": [],
            "rrf": self.results,
            "results": self.results,
        }


@pytest.fixture
def fake_retriever(monkeypatch):
    def install(results):
        retriever = FakeRetriever(results)
        monkeypatch.setattr(incidents, "get_retriever", lambda: retriever)
        return retriever

    return install


def test_retrieve_is_called_with_tenant_and_pipeline_limits(fake_retriever):
    retriever = fake_retriever([chunk("INC-1042", 0, 0.91)])

    incidents.find_similar_incidents(
        tenant="acme",
        service="payment-api",
        query="database connection pool exhaustion",
    )

    assert retriever.calls == [
        {
            "query": "database connection pool exhaustion",
            "tenant": "acme",
            "dense_limit": 20,
            "sparse_limit": 20,
            "rrf_limit": 20,
            # top_k covers the whole RRF candidate set so deduplication has
            # enough chunks to still find five distinct incidents.
            "top_k": 20,
        }
    ]


def test_top_k_leaves_room_for_deduplication():
    assert incidents.TOP_K >= incidents.MAX_INCIDENTS


def test_chunks_collapse_into_one_entry_per_incident(fake_retriever):
    fake_retriever(
        [
            chunk("INC-1042", 0, 0.91),
            chunk("INC-1042", 1, 0.72),
            chunk("INC-0987", 0, 0.64),
        ]
    )

    result = incidents.find_similar_incidents(
        tenant="default",
        service="payment-api",
        query="database timeouts",
    )

    assert [incident["incident_id"] for incident in result["incidents"]] == [
        "INC-1042",
        "INC-0987",
    ]
    # The best-ranked chunk of each incident is the one that survives.
    assert result["incidents"][0]["content"] == "INC-1042 chunk 0"
    assert result["incidents"][0]["score"] == 0.91
    assert result["tenant"] == "default"
    assert result["service"] == "payment-api"
    assert result["query"] == "database timeouts"


def test_five_incidents_are_returned_when_earlier_chunks_repeat(fake_retriever):
    # The top chunks all belong to one incident, so a retriever that only
    # returned MAX_INCIDENTS chunks could never reach five distinct results.
    fake_retriever(
        [chunk("INC-1042", index, 0.99 - index / 100) for index in range(5)]
        + [chunk(f"INC-{index}", 0, 0.5 - index / 100) for index in range(4)]
    )

    result = incidents.find_similar_incidents(
        tenant="default",
        service="payment-api",
        query="database timeouts",
    )

    assert [incident["incident_id"] for incident in result["incidents"]] == [
        "INC-1042",
        "INC-0",
        "INC-1",
        "INC-2",
        "INC-3",
    ]


def test_at_most_five_incidents_are_returned(fake_retriever):
    fake_retriever(
        [
            chunk(f"INC-{index}", 0, 1.0 - index / 100)
            for index in range(8)
        ]
    )

    result = incidents.find_similar_incidents(
        tenant="default",
        service="payment-api",
        query="database timeouts",
    )

    assert len(result["incidents"]) == 5


def test_tenant_is_passed_through_untouched(fake_retriever):
    retriever = fake_retriever([chunk("INC-1042", 0, 0.91)])

    result = incidents.find_similar_incidents(
        tenant="team-a",
        service="payment-api",
        query="database timeouts",
    )

    assert retriever.calls[0]["tenant"] == "team-a"
    assert result["tenant"] == "team-a"


@pytest.mark.parametrize("tenant", ["", "   "])
def test_missing_tenant_is_rejected_instead_of_searching_unscoped(fake_retriever, tenant):
    retriever = fake_retriever([chunk("INC-1042", 0, 0.91)])

    with pytest.raises(ValueError):
        incidents.find_similar_incidents(
            tenant=tenant,
            service="payment-api",
            query="database timeouts",
        )

    assert retriever.calls == []

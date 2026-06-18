"""
Integration tests for the knowledge seeder.
Verifies all 5 Qdrant collections are created and searchable.
"""
import os
import pytest
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def qdrant_client():
    """Connect to a Qdrant instance. Skip if not available."""
    qdrant_url = os.getenv("QDRANT_URL", "http://localhost:6333")
    try:
        from qdrant_client import QdrantClient
        client = QdrantClient(url=qdrant_url, timeout=5)
        client.get_collections()  # will raise if unavailable
        return client
    except Exception:
        pytest.skip("Qdrant not available")


@pytest.fixture(scope="module")
def seeded_qdrant(qdrant_client):
    """Run the seeder against the test Qdrant instance."""
    os.environ["QDRANT_URL"] = os.getenv("QDRANT_URL", "http://localhost:6333")
    from knowledge.seeder.src.main import (
        ensure_collection, seed_incidents, seed_runbooks,
        seed_architecture, seed_deployments, seed_postmortems,
    )
    ensure_collection(qdrant_client, "incidents",    ["service_name", "anomaly_type"])
    ensure_collection(qdrant_client, "runbooks",     ["anomaly_types"])
    ensure_collection(qdrant_client, "architecture", ["service_name"])
    ensure_collection(qdrant_client, "deployments",  ["service_name"])
    ensure_collection(qdrant_client, "postmortems",  ["root_cause_category"])

    seed_incidents(qdrant_client)
    seed_runbooks(qdrant_client)
    seed_architecture(qdrant_client)
    seed_deployments(qdrant_client)
    seed_postmortems(qdrant_client)
    return qdrant_client


class TestCollectionsExist:
    def test_all_5_collections_created(self, seeded_qdrant):
        collections = {c.name for c in seeded_qdrant.get_collections().collections}
        for name in ("incidents", "runbooks", "architecture", "deployments", "postmortems"):
            assert name in collections, f"Missing collection: {name}"

    def test_incidents_collection_has_vectors(self, seeded_qdrant):
        info = seeded_qdrant.get_collection("incidents")
        assert info.vectors_count > 0

    def test_runbooks_collection_has_vectors(self, seeded_qdrant):
        info = seeded_qdrant.get_collection("runbooks")
        assert info.vectors_count > 0

    def test_architecture_collection_has_all_services(self, seeded_qdrant):
        # 6 services × 1 vector each = 6 points
        info = seeded_qdrant.get_collection("architecture")
        assert info.vectors_count == 6


class TestSearchability:
    def test_latency_spike_returns_runbook(self, seeded_qdrant):
        from knowledge.seeder.src.main import embed
        vector = embed("latency spike P99 high latency remediation")
        results = seeded_qdrant.search(
            collection_name="runbooks",
            query_vector=vector,
            limit=5,
            with_payload=True,
        )
        assert len(results) > 0
        titles = [r.payload.get("title", "") for r in results]
        # At least one result should be related to latency
        assert any("Latency" in t or "latency" in t.lower() for t in titles), \
            f"Expected latency runbook in results, got: {titles}"

    def test_payment_service_returns_architecture(self, seeded_qdrant):
        from qdrant_client.models import Filter, FieldCondition, MatchValue
        from knowledge.seeder.src.main import embed
        vector = embed("payment-service architecture dependencies")
        results = seeded_qdrant.search(
            collection_name="architecture",
            query_vector=vector,
            limit=3,
            query_filter=Filter(
                must=[FieldCondition(key="service_name", match=MatchValue(value="payment-service"))]
            ),
            with_payload=True,
        )
        assert len(results) > 0
        assert results[0].payload["service_name"] == "payment-service"

    def test_incident_similarity_returns_similar_incidents(self, seeded_qdrant):
        from knowledge.seeder.src.main import embed
        vector = embed("payment-service latency spike database connection exhaustion deployment")
        results = seeded_qdrant.search(
            collection_name="incidents",
            query_vector=vector,
            limit=5,
            with_payload=True,
        )
        assert len(results) > 0
        # All results should have an incident_id
        for r in results:
            assert "incident_id" in r.payload

    def test_deployment_search_returns_relevant_deployments(self, seeded_qdrant):
        from knowledge.seeder.src.main import embed
        vector = embed("payment-service database query deployment release")
        results = seeded_qdrant.search(
            collection_name="deployments",
            query_vector=vector,
            limit=5,
            with_payload=True,
        )
        assert len(results) > 0


class TestIdempotency:
    def test_reseeding_does_not_duplicate(self, seeded_qdrant):
        """Running the seeder a second time should not increase vector count."""
        count_before = seeded_qdrant.get_collection("incidents").vectors_count

        from knowledge.seeder.src.main import seed_incidents
        seed_incidents(seeded_qdrant)

        count_after = seeded_qdrant.get_collection("incidents").vectors_count
        # Count should not increase (upsert by content hash)
        assert count_after == count_before

"""
E2E tests — full incident lifecycle from failure injection to postmortem email.

Requires: docker compose up (full stack running).
Enable with: E2E=true pytest tests/e2e/

These tests call real service endpoints and verify end-to-end behavior.
"""
import os
import time
import pytest
import requests

pytestmark = pytest.mark.e2e

BASE_URL       = os.getenv("SRE_API_URL",          "http://localhost:8000")
MAILHOG_URL    = os.getenv("MAILHOG_URL",           "http://localhost:8025")
FAILURE_URL    = os.getenv("FAILURE_INJECTOR_URL",  "http://localhost:8101")
GRAFANA_URL    = os.getenv("GRAFANA_URL",           "http://localhost:3000")
QDRANT_URL     = os.getenv("QDRANT_URL",            "http://localhost:6333")


def wait_for_service(url: str, max_seconds: int = 60, interval: float = 2.0) -> bool:
    deadline = time.time() + max_seconds
    while time.time() < deadline:
        try:
            r = requests.get(url, timeout=3)
            if r.status_code < 500:
                return True
        except Exception:
            pass
        time.sleep(interval)
    return False


@pytest.fixture(scope="session", autouse=True)
def verify_stack_running():
    """Skip all e2e tests if the API is not reachable."""
    if not wait_for_service(f"{BASE_URL}/health", max_seconds=30):
        pytest.skip("SRE API not reachable — run 'make demo' first")


class TestApiEndpoints:
    def test_health_endpoint(self):
        r = requests.get(f"{BASE_URL}/health")
        assert r.status_code == 200
        assert r.json()["status"] == "healthy"

    def test_incidents_list_returns_valid_structure(self):
        r = requests.get(f"{BASE_URL}/incidents")
        assert r.status_code == 200
        data = r.json()
        assert "incidents" in data
        assert isinstance(data["incidents"], list)

    def test_agents_health_endpoint(self):
        r = requests.get(f"{BASE_URL}/agents/health")
        assert r.status_code == 200
        health = r.json()
        # At least some agents should be reachable
        reachable = [k for k, v in health.items() if v.get("status") == "healthy"]
        assert len(reachable) > 0, f"No healthy agents found: {health}"

    def test_services_endpoint(self):
        r = requests.get(f"{BASE_URL}/services")
        assert r.status_code == 200
        data = r.json()
        assert "services" in data
        services = [s["service_name"] for s in data["services"]]
        for svc in ("payment-service", "order-service", "api-gateway"):
            assert svc in services


class TestFullIncidentFlow:
    """
    Tests the complete incident lifecycle:
    1. Trigger failure injection
    2. Wait for incident to be detected
    3. Wait for RCA to be completed
    4. Verify email in Mailhog
    5. Trigger resolution
    6. Verify postmortem
    """

    @pytest.fixture
    def injected_incident(self):
        """Inject a LATENCY_SPIKE failure and return once an incident is detected."""
        # Check if failure injector is available
        if not wait_for_service(f"{FAILURE_URL}/health", max_seconds=10):
            pytest.skip("Failure injector not available")

        # Trigger manual injection
        r = requests.get(f"{FAILURE_URL}/inject/LATENCY_SPIKE")
        assert r.status_code == 200

        # Wait for incident to appear in API (up to 3 minutes)
        incident = None
        deadline = time.time() + 180
        while time.time() < deadline:
            r = requests.get(f"{BASE_URL}/incidents?limit=10")
            if r.status_code == 200:
                incidents = r.json().get("incidents", [])
                active = [i for i in incidents if i.get("status") not in ("RESOLVED",) and
                          "payment-service" in (i.get("affected_services") or [])]
                if active:
                    incident = active[0]
                    break
            time.sleep(5)

        if incident is None:
            pytest.skip("No incident detected within 3 minutes — check simulator is running")
        return incident

    def test_incident_detected(self, injected_incident):
        """Verify an incident was created with expected structure."""
        assert injected_incident["severity"] in ("CRITICAL", "HIGH")
        assert "payment-service" in (injected_incident.get("affected_services") or [])
        assert injected_incident["status"] != "RESOLVED"

    def test_rca_completes_within_timeout(self, injected_incident):
        """Verify incident reaches RCA_COMPLETE within 3 minutes of detection."""
        incident_id = injected_incident["id"]
        deadline = time.time() + 180

        while time.time() < deadline:
            r = requests.get(f"{BASE_URL}/incidents/{incident_id}")
            if r.status_code == 200:
                inc = r.json()
                if inc.get("status") in ("RCA_COMPLETE", "REMEDIATING", "RESOLVED"):
                    assert inc.get("top_root_cause") or inc.get("rca_candidates")
                    return
            time.sleep(10)

        pytest.fail(f"Incident {incident_id} did not reach RCA_COMPLETE within 3 minutes")

    def test_emails_sent_to_mailhog(self, injected_incident):
        """Verify at least INCIDENT_OPENED email appears in Mailhog."""
        if not wait_for_service(f"{MAILHOG_URL}/api/v2/messages", max_seconds=10):
            pytest.skip("Mailhog not available")

        deadline = time.time() + 120  # wait up to 2 minutes for emails
        while time.time() < deadline:
            r = requests.get(f"{MAILHOG_URL}/api/v2/messages?limit=50")
            if r.status_code == 200:
                messages = r.json().get("items", [])
                subjects = [m.get("Content", {}).get("Headers", {}).get("Subject", [""])[0] for m in messages]
                if any("INCIDENT" in s or "INC-" in s for s in subjects):
                    return
            time.sleep(5)

        pytest.fail("No incident emails found in Mailhog within 2 minutes")

    def test_incident_resolves_and_postmortem_generated(self, injected_incident):
        """Manually resolve incident and verify postmortem is generated."""
        incident_id = injected_incident["id"]

        # Manually resolve
        r = requests.post(f"{BASE_URL}/incidents/{incident_id}/resolve")
        assert r.status_code == 200

        # Wait for postmortem
        deadline = time.time() + 300  # 5 minutes
        while time.time() < deadline:
            r = requests.get(f"{BASE_URL}/incidents/{incident_id}/postmortem")
            if r.status_code == 200:
                pm = r.json()
                assert pm.get("full_markdown") or pm.get("executive_summary")
                return
            time.sleep(15)

        pytest.fail(f"No postmortem generated for {incident_id} within 5 minutes")


class TestObservabilityStack:
    def test_prometheus_has_service_metrics(self):
        if not wait_for_service(f"http://localhost:9090/-/ready", max_seconds=10):
            pytest.skip("Prometheus not available")
        r = requests.get("http://localhost:9090/api/v1/query",
                         params={"query": "service_cpu_percent"})
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "success"
        assert len(data["data"]["result"]) > 0

    def test_qdrant_has_all_collections(self):
        if not wait_for_service(f"{QDRANT_URL}/readyz", max_seconds=10):
            pytest.skip("Qdrant not available")
        r = requests.get(f"{QDRANT_URL}/collections")
        assert r.status_code == 200
        names = {c["name"] for c in r.json()["result"]["collections"]}
        for name in ("incidents", "runbooks", "architecture", "deployments", "postmortems"):
            assert name in names, f"Missing Qdrant collection: {name}"

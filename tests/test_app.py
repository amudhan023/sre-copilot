from unittest.mock import patch
from fastapi.testclient import TestClient
from receiver.app import app

# Tests the alert receiver end to end through FastAPI's TestClient: health
# check, request validation, successful publish to Kafka, Kafka being down,
# and the dedup path (posting the same alert twice should only publish once).
# Redis and Kafka are mocked/patched out so these tests don't need either
# service running.

client = TestClient(app)


def test_healthz():
    assert client.get("/healthz").json() == {"status": "ok"}


def test_missing_labels_rejected():
    r = client.post("/alerts", json={"groupLabels": {"tenant": "acme"}})
    assert r.status_code == 400


def test_alert_published():
    with patch("receiver.app.get_producer") as gp, patch("receiver.app.get_redis"):
        r = client.post("/alerts", json={
            "groupLabels": {"tenant": "acme", "service": "api"},
            "commonLabels": {"alertname": "HighLatency"},
            "status": "firing",
        })
        assert r.status_code == 200, r.text
        topic, incident = gp.return_value.send.call_args[0]
        assert topic == "incidents"
        assert incident["tenant"] == "acme" and incident["alertname"] == "HighLatency"


def test_kafka_down_returns_503():
    from kafka.errors import KafkaError
    with patch("receiver.app.get_producer", side_effect=KafkaError("boom")), \
         patch("receiver.app.get_redis"):
        r = client.post("/alerts", json={"groupLabels": {"tenant": "a", "service": "b"}})
        assert r.status_code == 503

def test_duplicate_alert_only_published_once():
    class FakeRedis:
        calls = 0

        def set(self, key, value, nx=False, ex=None):
            FakeRedis.calls += 1
            # first alert is new, the identical second one is a duplicate
            return FakeRedis.calls == 1

    payload = {
        "groupLabels": {"tenant": "team-a", "service": "payments"},
        "commonLabels": {"alertname": "HighCPU"},
        "status": "firing",
    }

    with patch("receiver.app.get_producer") as gp, \
         patch("receiver.app.get_redis", return_value=FakeRedis()):
        first = client.post("/alerts", json=payload)
        second = client.post("/alerts", json=payload)

        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text
        assert first.json() == {"received": True}
        assert second.json()["duplicate"] is True
        # kafka sees the alert only once
        assert gp.return_value.send.call_count == 1


if __name__ == "__main__":
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_"):
            fn()
            print("ok", name)

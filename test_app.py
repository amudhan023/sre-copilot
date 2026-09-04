from unittest.mock import patch
from fastapi.testclient import TestClient
from receiver.app import app

client = TestClient(app)


def test_healthz():
    assert client.get("/healthz").json() == {"status": "ok"}


def test_missing_labels_rejected():
    r = client.post("/alerts", json={"groupLabels": {"tenant": "acme"}})
    assert r.status_code == 400


def test_alert_published():
    with patch("receiver.app.get_producer") as gp:
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
    with patch("receiver.app.get_producer", side_effect=KafkaError("boom")):
        r = client.post("/alerts", json={"groupLabels": {"tenant": "a", "service": "b"}})
        assert r.status_code == 503


if __name__ == "__main__":
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_"):
            fn()
            print("ok", name)

import json

from sre_copilot.alerting.dedup import InMemoryDeduplicator
from sre_copilot.alerting.events import alertmanager_to_event


def payload(status="firing"):
    return {
        "version": "4",
        "status": status,
        "groupLabels": {"tenant": "acme", "service": "payment-api"},
        "commonLabels": {"severity": "critical"},
        "alerts": [
            {
                "status": status,
                "labels": {
                    "alertname": "PaymentAPIHighLatency",
                    "tenant": "acme",
                    "service": "payment-api",
                    "severity": "critical",
                },
                "annotations": {"summary": "high latency"},
                "startsAt": "2026-09-07T10:00:00Z",
                "fingerprint": "abc123",
            },
            {
                "status": status,
                "labels": {
                    "alertname": "PaymentAPIDatabaseErrors",
                    "tenant": "acme",
                    "service": "payment-api",
                    "severity": "critical",
                },
                "annotations": {"summary": "db errors"},
                "startsAt": "2026-09-07T10:01:00Z",
                "fingerprint": "def456",
            },
        ],
    }


def test_alertmanager_payload_becomes_grouped_incident():
    event = alertmanager_to_event(payload())
    data = event.to_dict()

    assert data["schema"] == "sre-copilot.incident.v1"
    assert data["tenant"] == "acme"
    assert data["service"] == "payment-api"
    assert data["alert_count"] == 2
    assert data["alert_names"] == ["PaymentAPIDatabaseErrors", "PaymentAPIHighLatency"]
    assert data["severity"] == "critical"
    assert data["incident_key"].startswith("acme/payment-api/")
    json.loads(event.to_json())


def test_same_group_has_stable_incident_key():
    first = alertmanager_to_event(payload())
    second = alertmanager_to_event(payload())
    assert first.incident_id == second.incident_id
    assert first.incident_key == second.incident_key


def test_resolved_event_has_same_incident_identity_but_distinct_dedup_state():
    firing = alertmanager_to_event(payload("firing"))
    resolved = alertmanager_to_event(payload("resolved"))
    assert firing.incident_id == resolved.incident_id
    assert firing.status != resolved.status

    dedup = InMemoryDeduplicator()
    assert dedup.claim(f"{firing.incident_key}:{firing.status}", 100)
    assert not dedup.claim(f"{firing.incident_key}:{firing.status}", 100)
    assert dedup.claim(f"{resolved.incident_key}:{resolved.status}", 100)


def test_dedup_release_allows_retry_after_publish_failure():
    dedup = InMemoryDeduplicator()
    key = "sre-copilot:dedup:acme/payment-api/x:firing"
    assert dedup.claim(key, 100)
    assert not dedup.claim(key, 100)
    dedup.release(key)
    assert dedup.claim(key, 100)

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

SCHEMA = "sre-copilot.incident.v1"


def _stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def incident_fingerprint(
    tenant: str, service: str, alerts: list[dict[str, Any]], group_key: str | None = None
) -> str:
    """Create a stable fingerprint for one Alertmanager group."""
    if group_key:
        raw = f"{tenant}|{service}|{group_key}"
    else:
        identities = []
        for alert in alerts:
            labels = alert.get("labels") or {}
            identities.append({
                "alertname": labels.get("alertname") or labels.get("alert_name"),
                "severity": labels.get("severity"),
                "fingerprint": alert.get("fingerprint"),
            })
        identities.sort(key=_stable_json)
        raw = _stable_json({"tenant": tenant, "service": service, "alerts": identities})
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _resolve_group(payload: dict[str, Any], alerts: list[dict[str, Any]]) -> tuple[str, str]:
    group = payload.get("groupLabels") or {}
    common = payload.get("commonLabels") or {}
    first_labels = (alerts[0].get("labels") or {}) if alerts else {}
    tenant = group.get("tenant") or common.get("tenant") or first_labels.get("tenant") or "unknown"
    service = group.get("service") or common.get("service") or first_labels.get("service") or "unknown"
    return str(tenant), str(service)


@dataclass(frozen=True)
class IncidentEvent:
    incident_id: str
    tenant: str
    service: str
    status: str
    severity: str
    alert_names: list[str]
    alerts: list[dict[str, Any]]
    incident_key: str
    started_at: str | None = None
    received_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    schema: str = SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "incident_id": self.incident_id,
            "incident_key": self.incident_key,
            "tenant": self.tenant,
            "service": self.service,
            "status": self.status,
            "severity": self.severity,
            "alert_count": len(self.alerts),
            "alert_names": self.alert_names,
            "alerts": self.alerts,
            "started_at": self.started_at,
            "received_at": self.received_at,
        }

    def to_json(self) -> str:
        return _stable_json(self.to_dict())


def alertmanager_to_event(payload: dict[str, Any]) -> IncidentEvent:
    """Normalize an Alertmanager webhook v4 payload into our Kafka contract."""
    if payload.get("version") != "4":
        raise ValueError("unsupported Alertmanager webhook version")
    alerts = payload.get("alerts")
    if not isinstance(alerts, list) or not alerts:
        raise ValueError("Alertmanager payload must contain at least one alert")

    tenant, service = _resolve_group(payload, alerts)
    status = str(payload.get("status") or alerts[0].get("status") or "firing")
    names = sorted({
        str((alert.get("labels") or {}).get("alertname"))
        for alert in alerts
        if (alert.get("labels") or {}).get("alertname")
    })
    severities = [str((alert.get("labels") or {}).get("severity"))
                  for alert in alerts if (alert.get("labels") or {}).get("severity")]
    severity = max(
        severities,
        key=lambda s: {"critical": 4, "warning": 3, "info": 1}.get(s.lower(), 2),
        default="unknown",
    )

    fingerprint = incident_fingerprint(tenant, service, alerts, payload.get("groupKey"))
    incident_key = f"{tenant}/{service}/{fingerprint}"
    incident_id = hashlib.sha256(incident_key.encode()).hexdigest()[:24]
    starts = [a.get("startsAt") for a in alerts if a.get("startsAt")]

    return IncidentEvent(
        incident_id=incident_id,
        incident_key=incident_key,
        tenant=tenant,
        service=service,
        status=status,
        severity=severity,
        alert_names=names,
        alerts=alerts,
        started_at=min(starts) if starts else None,
    )

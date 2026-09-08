# Event-driven alerting pipeline

SRE Copilot receives incidents from Alertmanager instead of continuously polling telemetry for new incidents.

```text
Prometheus / signal adapters
        |
        v
   Alertmanager
   group tenant+service
        |
        v
 POST /alerts
        |
        +--> Redis SET NX EX (dedup)
        |
        v
 Kafka topic: incidents
```

## Alert grouping

Alertmanager groups by `tenant` and `service`. A latency alert and a database-error alert for the same service therefore arrive in one notification and become one `IncidentEvent`.

`infra/prometheus/alerts.yml` contains baseline latency, 5xx, CPU, and database-error rules. Other signal systems such as OpenSearch log alerts or trace anomaly detectors should eventually publish Alertmanager-compatible alerts (or use a small adapter) so the receiver remains unaware of telemetry-specific detection logic.

## Redis deduplication

The receiver claims:

```text
sre-copilot:dedup:<incident-key>:<status>
```

using atomic `SET NX EX`. `firing` and `resolved` are intentionally separate lifecycle states. Redis is non-authoritative and fails open: Redis failure can create a duplicate, but never prevents an alert from reaching Kafka.

If Kafka publication fails after a dedup claim, the claim is released and HTTP 503 is returned. Alertmanager will retry the webhook.

## Kafka contract

Topic: `incidents`

Message key:

```text
<tenant>/<service>
```

This preserves ordering for incidents belonging to the same service while allowing different services to use different Kafka partitions.

Payload schema:

```json
{
  "schema": "sre-copilot.incident.v1",
  "incident_id": "...",
  "incident_key": "acme/payment-api/...",
  "tenant": "acme",
  "service": "payment-api",
  "status": "firing",
  "severity": "critical",
  "alert_count": 2,
  "alert_names": ["PaymentAPIHighLatency", "PaymentAPIDatabaseErrors"],
  "alerts": [],
  "started_at": "...",
  "received_at": "..."
}
```

## Local run

Start Kafka and Redis plus Prometheus/Alertmanager:

```bash
docker compose -f infra/docker-compose.alerting.yml up -d
```

Run the receiver on the host:

```bash
KAFKA_BOOTSTRAP=localhost:29092 \
REDIS_URL=redis://localhost:6379/0 \
uv run python scripts/alert_receiver.py
```

Then run a service that exposes Prometheus metrics with `tenant` and `service` labels. Triggering a matching rule will flow through Alertmanager to `/alerts` and then Kafka.

Inspect the topic:

```bash
docker compose -f infra/docker-compose.alerting.yml exec kafka \
  kafka-console-consumer --bootstrap-server kafka:9092 \
  --topic incidents --from-beginning
```

## Security

Set `RECEIVER_TOKEN` outside local development. Alertmanager should send `Authorization: Bearer <token>` using a mounted credentials file rather than committing a secret to `alertmanager.yml`.

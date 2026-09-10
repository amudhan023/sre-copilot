# SignalOps end-to-end demo

This directory contains the reproducible observability environment used to demonstrate SRE Copilot end-to-end. It is adapted from `amudhan023/signalops-ai` and intentionally kept outside the production `src/sre_copilot` package.

## Start

From the repository root:

```bash
cp demo/signalops/infra/.env.example demo/signalops/infra/.env
bash scripts/demo-up.sh
```

`demo-up.sh` starts the Docker infrastructure, the payment API simulator, and the SRE Copilot Alertmanager receiver.

Then trigger the deterministic incident:

```bash
curl -X POST localhost:8000/break
```

The simulator changes the database pool from 50 to 10. Prometheus detects the resulting latency/database-error symptoms, Alertmanager groups them by tenant and service, and the SRE Copilot receiver deduplicates and publishes the normalized event to Kafka.

Heal it with:

```bash
curl -X POST localhost:8000/heal
```

Stop everything with:

```bash
bash scripts/demo-down.sh
```

## Flow

```text
simulator
  ├── metrics ──> Prometheus ──> Alertmanager ──> SRE Copilot /alerts
  ├── logs ────────────────────> OTel ──────────> OpenSearch
  └── traces ──────────────────> OTel ──────────> OpenSearch
                                                     │
                                                     ▼
                                                  Redis
                                                     │
                                                     ▼
                                                Kafka incidents
```

The Kafka incident worker is intentionally the next layer. This demo establishes the complete signal plane that feeds it.

## Services

- Prometheus: `localhost:9090`
- Alertmanager: `localhost:9093`
- Grafana: `localhost:3000`
- OpenSearch: `localhost:9200`
- OTLP HTTP: `localhost:4318`
- OTLP gRPC: `localhost:4317`
- Kafka: `localhost:29092`
- Redis: `localhost:6379`
- Simulator: `localhost:8000`
- SRE Copilot receiver: `localhost:8080`

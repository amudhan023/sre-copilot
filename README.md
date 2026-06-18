# SRE Copilot — AI-Assisted Incident Response

AI-powered SRE Copilot using LangGraph, Kafka, and Flink to ingest real-time alerts, logs, metrics, and traces, correlating multi-service failures and leveraging Prometheus, ELK, and deployment events to generate root-cause hypotheses, incident summaries, and mitigation recommendations using LLM-based analysis, delivered via Slack and PagerDuty.

## Quick Start

```bash
# 1. Configure (only ANTHROPIC_API_KEY is required)
cp .env.example .env
# edit .env and add your API key

# 2. Launch everything
make demo

# 3. Access points (ready in ~3 minutes)
#   SRE Dashboard : http://localhost:8000
#   Grafana       : http://localhost:3000  (admin/admin)
#   Kafka UI      : http://localhost:8080
#   Mailhog       : http://localhost:8025  ← watch AI emails appear here
#   Prometheus    : http://localhost:9090
#   Qdrant        : http://localhost:6333/dashboard
```

## Testing

```bash
# Run the full test suite (unit + integration, no docker-compose needed)
make test

# Fast unit tests only — completes in < 1 second
make test-unit

# Full E2E tests (requires make demo running)
make test-e2e
```

## Architecture

```
Simulation Layer          Ingestion Layer         Agent Layer
──────────────────        ───────────────         ────────────────────────
traffic-simulator   ──→   metrics-ingester  ──→   Detection Agent
failure-injector    ──→   log-ingester      ──→   Correlation Agent
deployment-sim      ──→   deployment-ingr   ──→   Investigation Agent
                                            ──→   Knowledge Retrieval Agent
                    Kafka Event Bus         ──→   Remediation Agent
                    ═════════════           ──→   Communication Agent
                                            ──→   Postmortem Agent
                    Knowledge Layer
                    ────────────────
                    Qdrant (5 collections):
                    • incidents (50 historical)
                    • runbooks (9 procedures)
                    • architecture (6 services)
                    • deployments (30 records)
                    • postmortems (7 documents)
```

## Failure Scenarios

The failure injector cycles through 9 scenarios automatically:

| Scenario | Service | Signal | Duration |
|----------|---------|--------|----------|
| LATENCY_SPIKE | payment-service | P99 → 8500ms | 5 min |
| ERROR_RATE_SPIKE | order-service | error rate → 45% | 3 min |
| CPU_SATURATION | api-gateway | CPU → 95% | 8 min |
| MEMORY_LEAK | notification-service | memory → 96% | 12 min |
| DB_CONNECTION_EXHAUSTION | payment-service | connections → 99/100 | 6 min |
| KAFKA_CONSUMER_LAG | order-service | lag → 52,000 msgs | 10 min |
| DEPENDENCY_OUTAGE | inventory-service | errors → 98% | 5 min |
| DEPLOYMENT_FAILURE | user-service | errors → 100% | 4 min |
| NETWORK_PARTITION | payment-service | connection resets | 3 min |

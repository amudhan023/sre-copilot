---
runbook_id: RB-001
title: "High API Latency (P99 Spike)"
anomaly_types: ["LATENCY_SPIKE"]
services: ["api-gateway", "payment-service", "order-service", "user-service"]
severity_levels: ["CRITICAL", "HIGH"]
tags: ["latency", "performance", "p99", "api", "timeout"]
version: "1.2"
---

# High API Latency (P99 Spike)

## Overview

This runbook covers investigation and remediation of P99 latency spikes on HTTP API services. Latency spikes above 2× baseline for more than 2 minutes require immediate action.

## Detection Criteria

- P99 latency exceeds 2000ms (2× baseline of ~200-500ms typical)
- Upstream error rate may increase due to client timeouts
- CPU and thread pool metrics often correlate

## Investigation Steps

### Step 1: Identify the Scope

Determine whether the spike is isolated to one service or cascading:
- Check Grafana dashboard: compare latency across all services simultaneously
- Look for temporal correlation: did multiple services spike at the same time?
- If all services spike simultaneously → likely shared infrastructure (DB, Redis, Kafka)
- If one service spikes first → cascade from that origin

### Step 2: Check Resource Utilisation

For the affected service:
- CPU: >80% sustained indicates thread contention or hot loop
- Memory: >90% may trigger GC pauses causing latency spikes
- Thread pool exhaustion: if connection pool is saturated, requests queue
- Check `db_connections_active / db_connections_max` ratio

### Step 3: Inspect Recent Deployments

- Was a deployment made in the last 60 minutes to this service?
- Review the deployment notes for performance-sensitive changes (N+1 queries, removed caching, increased timeout values, synchronous external calls added)
- If yes: rollback is the fastest remediation path

### Step 4: Check Downstream Dependencies

- Query the dependency graph for this service
- For each downstream service, check their latency/error rate
- A slow database query or slow Redis call will manifest as upstream latency
- Check `db_query_duration_seconds` metrics specifically

### Step 5: Check Kafka Consumer Lag (if order-service)

- High consumer lag causes backpressure and delayed processing
- Check `kafka_consumer_lag` for all consumer groups for this service
- If lag > 10,000 messages: consumers are processing slower than producers

## Remediation Procedure

### Immediate Actions (< 5 minutes)

1. **Scale the service horizontally** (if running Kubernetes):
   ```
   kubectl scale deployment <service-name> --replicas=<current + 2>
   ```
   Risk: LOW | Rollback: scale back down

2. **Increase connection pool size** if DB connections are saturated:
   - Update `DB_POOL_MAX` environment variable
   - Rolling restart the service
   Risk: MEDIUM | Rollback: revert env var and restart

3. **Enable circuit breaker** for downstream slow dependency:
   - If a downstream service is slow, fail fast and return cached/degraded response
   Risk: MEDIUM | Rollback: disable circuit breaker flag

### Short-Term Actions (< 30 minutes)

4. **Rollback the most recent deployment** if deployment-correlated:
   - Identify the previous good version from deployment history
   - Trigger rollback deployment in CI/CD system
   Risk: LOW | Rollback: redeploy the rolled-back version

5. **Increase timeout thresholds** temporarily to prevent cascading failures:
   - Upstream callers hitting this service may be set to aggressive timeouts
   Risk: LOW | Rollback: revert timeout config

## Escalation Path

If latency is not resolved within 15 minutes of starting remediation:
- Escalate to the service-owning team's on-call engineer
- Page the platform-team for shared infrastructure issues
- Consider enabling degraded mode (disable non-critical features)

## Prevention

- Add P99 latency alerting at 1000ms (50% of SLA)
- Profile query plans quarterly, add DB indexes for slow queries
- Load test after every deployment to catch performance regressions

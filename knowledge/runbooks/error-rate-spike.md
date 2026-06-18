---
runbook_id: RB-002
title: "High Error Rate (5xx Spike)"
anomaly_types: ["ERROR_RATE_SPIKE"]
services: ["api-gateway", "payment-service", "order-service", "user-service", "inventory-service"]
severity_levels: ["CRITICAL", "HIGH"]
tags: ["errors", "5xx", "http", "exception", "failure"]
version: "1.1"
---

# High Error Rate (5xx Spike)

## Overview

Covers investigation and remediation when service error rates exceed acceptable SLA thresholds. A 5xx error rate above 5% for 2+ minutes is a P1 incident.

## Detection Criteria

- `service_error_rate_percent > 5.0` sustained for >2 minutes
- HTTP 500, 502, 503, 504 status codes increasing
- Exception logs with stack traces appearing in log stream

## Investigation Steps

### Step 1: Triage Error Types

Distinguish between error categories:
- **Application errors (500)**: Bug in application code — look for exceptions in logs
- **Dependency errors (502/503)**: Downstream service unavailable — check dependency health
- **Timeout errors (504)**: Downstream too slow — check latency runbook concurrently
- **Overload errors (503)**: Service at capacity — check CPU/memory/thread pool

### Step 2: Examine Error Logs

Search for exception patterns:
- Look for the first occurrence: when did errors start vs. anomaly detection time?
- Identify the exception class and message — is it a known error or new?
- Check if errors are from one endpoint or all endpoints (targeted vs. widespread)
- Stack traces will reveal the root module causing failures

### Step 3: Correlate with Deployment

- Was any service deployed in the last 60 minutes?
- Did error rate start immediately after deployment or gradually?
- Immediate start after deploy → likely code bug in new version
- Gradual increase → may be resource leak, load accumulation, or dependency degradation

### Step 4: Check Downstream Dependencies

- Call each downstream service health endpoint
- High error rates in payment-service → check postgres and redis health
- High error rates in order-service → check payment-service and inventory-service
- High error rates in inventory-service → check postgres directly

### Step 5: Database Health Check

```sql
-- Check for long-running queries
SELECT pid, now() - query_start as duration, query
FROM pg_stat_activity
WHERE state = 'active' AND now() - query_start > interval '5 seconds'
ORDER BY duration DESC;

-- Check connection pool
SELECT count(*), state FROM pg_stat_activity GROUP BY state;
```

## Remediation Procedure

### Immediate Actions (< 5 minutes)

1. **Rollback deployment** if errors started within 10 minutes of a deploy:
   - Fastest path to recovery
   - Trigger rollback in CI/CD system
   Risk: LOW | Rollback: redeploy the feature branch

2. **Kill long-running DB queries** if database is the bottleneck:
   ```sql
   SELECT pg_terminate_backend(pid)
   FROM pg_stat_activity
   WHERE now() - query_start > interval '30 seconds' AND state = 'active';
   ```
   Risk: LOW | Rollback: N/A (queries will be re-run by application)

3. **Restart the affected service** if error rate is 100% (complete failure):
   - Rolling restart to avoid full downtime
   Risk: LOW | Rollback: N/A (service restarts to previous running state)

### Short-Term Actions (< 30 minutes)

4. **Add circuit breaker** to failing downstream dependency:
   - Prevents error propagation and cascading failures
   - Returns degraded/cached response instead of propagating 503

5. **Scale horizontally** if errors are due to thread pool exhaustion:
   - Add replicas, load balancer will distribute traffic

## Escalation Path

If error rate does not drop below 1% within 10 minutes:
- Page the service owner immediately
- Prepare stakeholder communication for >5 minute impact
- Enable status page incident

## Prevention

- Contract testing between services to catch breaking API changes before deployment
- Canary deployments to validate new versions on 5% of traffic first
- Chaos engineering: regularly test dependency failure scenarios

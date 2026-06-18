---
incident_id: INC-2025-001
service: payment-service
severity: CRITICAL
root_cause_category: DEPLOYMENT
occurred_at: "2025-01-15"
mttr_minutes: 75
---

# Postmortem: Payment Service Latency Spike — INC-2025-001

**Date:** 2025-01-15 | **Duration:** 75 minutes | **Severity:** CRITICAL | **MTTR:** 75 min

## Executive Summary

A deployment to payment-service introduced a missing database index, causing full table scans on the transactions table. Under production load, this exhausted the connection pool and caused a 75-minute P1 incident with significant revenue impact.

## Contributing Factors

1. **Missing DB index on hot query path** — The new query pattern was not present in any previous version and was not load tested before deployment.
2. **No query performance test in CI** — The CI pipeline had no automated check that new queries use indexes effectively.
3. **Connection pool undersized** — The existing connection pool (20 connections) was too small to absorb the additional load from slow queries.
4. **No canary deployment** — The change was deployed to 100% of instances simultaneously, maximising impact.

## Preventative Actions

- All new database queries must include EXPLAIN ANALYZE output in PR description
- Add automated query plan analysis to CI: detect sequential scans on large tables
- Increase payment-service DB pool from 20 to 50 connections
- Implement canary deployment for payment-service (5% traffic first, 30-minute soak)
- Add index coverage unit test for all new query patterns

---
incident_id: INC-2025-020
service: payment-service
severity: CRITICAL
root_cause_category: DEPLOYMENT
occurred_at: "2025-10-10"
mttr_minutes: 30
---

# Postmortem: Full Table Scan — New Feature Query — INC-2025-020

**Date:** 2025-10-10 | **Duration:** 30 minutes | **Severity:** CRITICAL | **MTTR:** 30 min

## Executive Summary

A new payment search feature introduced an `ILIKE` query on an unindexed column of a 50M-row table. The query was called on every payment status check — a high-frequency hot path. Under production load, each query took 8-12 seconds, causing widespread latency spikes and cascading timeouts.

## Contributing Factors

1. **ILIKE on unindexed 50M-row table** — The query was performant on the development database with 10K rows, but catastrophic at production scale.
2. **Query used on a hot path** — The search was embedded in the payment status check endpoint called 50+ times per second.
3. **No query analysis in CI** — The CI pipeline had no step that ran EXPLAIN ANALYZE on new database queries.
4. **No production-scale data in staging** — Staging database had 0.1% of production data volume.

## Preventative Actions

- Mandate EXPLAIN ANALYZE output in PR description for all new database queries
- Add staging database with realistic data volume (minimum 10% of production)
- Add pg_stat_statements baseline comparison in CI: alert if any query's mean execution time exceeds 100ms
- Code review requirement: identify the expected query execution plan before merging
- Add statement_timeout=5000ms to prevent any single query from taking >5 seconds

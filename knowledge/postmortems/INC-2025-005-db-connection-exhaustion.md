---
incident_id: INC-2025-005
service: payment-service
severity: CRITICAL
root_cause_category: APPLICATION
occurred_at: "2025-02-20"
mttr_minutes: 40
---

# Postmortem: DB Connection Pool Exhaustion — INC-2025-005

**Date:** 2025-02-20 | **Duration:** 40 minutes | **Severity:** CRITICAL | **MTTR:** 40 min | **Revenue Impact:** ~$2M

## Executive Summary

A batch reconciliation job deployed to run at 8 PM consumed 80 of 100 available database connections, leaving the payment service with only 20 connections to serve live traffic. During peak evening traffic, those 20 connections were exhausted, causing complete payment failure for 40 minutes.

## Contributing Factors

1. **Batch job connection limit not configured** — The batch job had no max_connections setting and defaulted to grabbing as many connections as it could parallelize.
2. **No connection quota by application role** — All applications shared the same PostgreSQL user with no per-role connection limits.
3. **Batch job scheduled at peak traffic time** — 8 PM was one of the highest traffic windows; the job should run at 3 AM.
4. **No monitoring of connection distribution** — We had total connection count monitoring but not per-application breakdown.

## Preventative Actions

- Deploy PgBouncer to multiplex application connections
- Implement PostgreSQL role-based connection limits: `ALTER ROLE batch_user CONNECTION LIMIT 5`
- Reschedule all batch jobs to 1-4 AM maintenance window
- Add connection distribution monitoring: alert when any single application holds >30% of connections
- Add pre-job check: if connection pool utilization >80%, delay batch job start

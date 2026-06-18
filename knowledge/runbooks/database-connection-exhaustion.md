---
runbook_id: RB-005
title: "Database Connection Pool Exhaustion"
anomaly_types: ["DB_CONNECTION_EXHAUSTION"]
services: ["payment-service", "order-service", "user-service", "inventory-service"]
severity_levels: ["CRITICAL", "HIGH"]
tags: ["database", "postgres", "connection-pool", "exhaustion", "pgbouncer"]
version: "1.3"
---

# Database Connection Pool Exhaustion

## Overview

When all available database connections are consumed, new queries queue indefinitely, causing latency spikes and eventual timeouts that cascade as errors.

## Detection Criteria

- `db_connections_active / db_connections_max >= 0.95`
- Latency spikes immediately follow connection saturation
- Error logs: "too many connections", "connection pool exhausted", "FATAL: remaining connection slots are reserved"

## Investigation Steps

### Step 1: Check Current Connection Count

```sql
SELECT count(*), state, wait_event_type, wait_event
FROM pg_stat_activity
GROUP BY state, wait_event_type, wait_event
ORDER BY count DESC;
```

- `idle in transaction` connections are especially dangerous — they hold locks and consume slots
- `idle` connections might indicate pool not being returned properly

### Step 2: Identify Long-Running Transactions

```sql
SELECT pid, usename, application_name,
       now() - xact_start AS xact_duration,
       now() - query_start AS query_duration,
       state, query
FROM pg_stat_activity
WHERE state != 'idle'
  AND now() - query_start > interval '10 seconds'
ORDER BY xact_duration DESC NULLS LAST;
```

Transactions open for >30 seconds are almost certainly stuck.

### Step 3: Check for Lock Contention

```sql
SELECT blocked.pid,
       blocked.query AS blocked_query,
       blocking.pid AS blocking_pid,
       blocking.query AS blocking_query
FROM pg_stat_activity blocked
JOIN pg_stat_activity blocking
  ON blocking.pid = ANY(pg_blocking_pids(blocked.pid))
WHERE cardinality(pg_blocking_pids(blocked.pid)) > 0;
```

### Step 4: Correlate with Application Traffic

- Did request rate spike? More requests → more concurrent connections needed
- Did a batch job start? Bulk operations consume many connections
- Was connection pool max size recently reduced?

## Remediation Procedure

### Immediate Actions (< 5 minutes)

1. **Terminate idle-in-transaction connections** older than 30 seconds:
   ```sql
   SELECT pg_terminate_backend(pid)
   FROM pg_stat_activity
   WHERE state = 'idle in transaction'
     AND now() - state_change > interval '30 seconds';
   ```
   Risk: LOW (idle transactions will retry) | Rollback: N/A

2. **Terminate long-running queries** blocking others:
   ```sql
   SELECT pg_terminate_backend(pid)
   FROM pg_stat_activity
   WHERE now() - query_start > interval '60 seconds'
     AND state = 'active'
     AND query NOT ILIKE '%pg_stat_activity%';
   ```
   Risk: MEDIUM (in-flight writes will rollback) | Rollback: N/A

3. **Restart application service** to reset its connection pool:
   - The service will reconnect with a fresh pool state
   Risk: LOW | Rollback: N/A

### Short-Term Actions (< 30 minutes)

4. **Increase `max_connections` in PostgreSQL** (requires reload, not restart):
   - Edit `postgresql.conf`: increase `max_connections` incrementally
   - `SELECT pg_reload_conf();`
   Risk: MEDIUM (memory impact) | Rollback: decrease and reload

5. **Deploy PgBouncer** as connection pooler:
   - PgBouncer multiplexes many application connections onto fewer DB connections
   - Session pooling or transaction pooling depending on transaction patterns
   Risk: MEDIUM | Rollback: route applications directly to DB

6. **Reduce application pool max size** to prevent starvation:
   - If multiple application instances each have pool max=50 and you have 10 instances → 500 connections
   - Set per-instance pool max to `total_db_max / num_instances - buffer`

## Escalation Path

If connections remain saturated after step 1-3:
- Escalate to database team immediately
- The Postgres max_connections limit may need an emergency increase
- Consider enabling read replicas to offload read traffic

## Prevention

- Set `statement_timeout` and `idle_in_transaction_session_timeout` in Postgres
- Monitor `db_connections_active/max` ratio, alert at 80%
- Use PgBouncer for high-concurrency services
- Set pool max based on actual DB connection limits, not defaults

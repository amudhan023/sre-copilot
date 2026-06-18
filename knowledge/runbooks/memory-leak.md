---
runbook_id: RB-004
title: "Memory Leak / OOM"
anomaly_types: ["MEMORY_LEAK"]
services: ["notification-service", "order-service", "payment-service"]
severity_levels: ["CRITICAL", "HIGH"]
tags: ["memory", "oom", "leak", "heap", "gc", "jvm"]
version: "1.1"
---

# Memory Leak / OOM

## Overview

Memory continuously growing without release indicates a leak. Left unresolved, services terminate with OOM errors, causing 100% error rates until restart.

## Detection Criteria

- `service_memory_percent > 85` and growing monotonically over 10+ minutes
- GC frequency increasing without memory reclamation
- RSS memory growing independently of heap

## Investigation Steps

### Step 1: Confirm the Leak Pattern

- Is memory growing steadily (linear → likely a true leak)?
- Or is it growing rapidly then plateauing (load-based accumulation)?
- True leak: memory does not decrease even when request rate drops

### Step 2: Identify the Memory Type

- **Heap leak**: objects allocated but never freed (unbounded caches, listeners not removed)
- **Connection leak**: database or HTTP connections not closed (pool exhaustion)
- **File descriptor leak**: file handles not closed (check `ulimit -n` and open fd count)
- **Cache without eviction**: in-memory caches growing without bound

### Step 3: Correlate with Recent Changes

- Was a cache added without TTL or max size?
- Was a subscription/listener added without cleanup on disconnect?
- Was a batch job introduced that loads large datasets without pagination?
- Were connection pool settings changed?

### Step 4: Check Connection Pools

```python
# Typical connection leak indicators
# Redis pool: check redis_connected_clients growing
# DB pool: check db_connections_active growing while requests are idle
```

## Remediation Procedure

### Immediate Actions (< 5 minutes)

1. **Restart the service** before OOM kill to control the restart timing:
   - Rolling restart: terminate instances one at a time to avoid full outage
   - Memory resets to baseline after restart
   Risk: LOW | Rollback: N/A (service is already failing)

2. **Scale horizontally** to share load during restarts:
   - Add replicas so traffic distributes while affected instances are restarting
   Risk: LOW | Rollback: scale back down

### Short-Term Actions (< 30 minutes)

3. **Add memory limit and circuit breaker**:
   - Set `MEMORY_LIMIT_MB` env var to auto-restart when threshold reached
   - This converts an uncontrolled OOM into a controlled restart

4. **Rollback deployment** if leak started after a recent deploy:
   - Rollback eliminates the leak source immediately
   Risk: LOW | Rollback: redeploy

5. **Add cache eviction policy** if heap grows correlate with cache size:
   - Set max size and TTL on in-memory caches
   - LRU eviction prevents unbounded growth

## Escalation Path

If memory continues growing after restart:
- The restart may be masking a fast leak
- Escalate to service team for heap dump analysis
- Consider disabling the feature that introduced the leak

## Prevention

- Set max size on all in-memory caches
- Always use connection pool libraries with max size
- Load test with extended run times (not just peak throughput)
- Add memory growth alerting at 75% to catch leaks before OOM

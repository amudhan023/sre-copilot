---
runbook_id: RB-008
title: "Upstream Dependency Outage"
anomaly_types: ["DEPENDENCY_OUTAGE", "ERROR_RATE_SPIKE"]
services: ["api-gateway", "order-service", "inventory-service"]
severity_levels: ["CRITICAL", "HIGH"]
tags: ["dependency", "outage", "circuit-breaker", "fallback", "cascade"]
version: "1.1"
---

# Upstream Dependency Outage

## Overview

When a service that yours depends on goes down or becomes severely degraded, your service will show error rate spikes, latency increases, or timeouts. Fast mitigation requires circuit breaking and fallback strategies.

## Detection Criteria

- Downstream service returns 503/504 errors
- Connection timeouts to downstream service
- Your service's error rate spikes when downstream degrades
- Cascade pattern: the original anomaly is in a different service than the reported one

## Investigation Steps

### Step 1: Identify the Dependency Chain

- Which service is the origin of the outage?
- Map the cascade: `api-gateway → order-service → payment-service → postgres`
- Focus remediation on the origin, not the downstream effects

### Step 2: Check Dependency Health

For each downstream service:
- Is the service returning errors or timing out?
- Is the service running (not crashed)?
- Is this a network partition between services?

### Step 3: Evaluate Fallback Options

- Can the feature served by the failed dependency be degraded gracefully?
- Can cached data substitute for live data temporarily?
- Can the operation be queued and retried when the dependency recovers?

### Step 4: Check Infrastructure

- Are services in the same network segment?
- Check DNS resolution between services
- Check connection limits at the load balancer/service mesh level

## Remediation Procedure

### Immediate Actions (< 5 minutes)

1. **Enable circuit breaker** for the failing dependency:
   - When the downstream is returning errors, fail fast instead of waiting for timeouts
   - Returns a cached/degraded response immediately
   - Prevents your service from being overwhelmed by client retries waiting for timeouts
   Risk: MEDIUM (some features degrade) | Rollback: disable circuit breaker

2. **Increase timeout for the affected dependency** if it's slow but not down:
   - Prevents your service from timing out before the dependency can respond
   Risk: LOW | Rollback: revert timeout config

3. **Route to read replica/cache** for read-heavy operations:
   - If a primary database is down, reads can often serve from a replica or cache
   Risk: LOW (stale reads possible) | Rollback: re-enable primary reads

### Short-Term Actions (< 30 minutes)

4. **Restart the failing downstream service** (if you own it):
   - OOM or deadlock may have caused the service to fail
   - Controlled restart recovers faster than waiting for health check restart

5. **Scale the failing service** if it's overwhelmed:
   - The dependency outage may actually be the dependency being overloaded
   - Scale horizontally to handle the inbound load

## Escalation Path

If the root dependency is a third-party service (external API):
- Check the third-party status page
- Notify stakeholders that resolution depends on external party
- Implement aggressive caching and fallbacks while waiting

## Prevention

- Implement circuit breakers for all external dependency calls
- Set aggressive connection and read timeouts (never leave at default infinite)
- Test dependency failure scenarios in staging monthly
- Define SLOs for each dependency and track them

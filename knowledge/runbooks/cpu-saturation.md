---
runbook_id: RB-003
title: "CPU Saturation"
anomaly_types: ["CPU_SATURATION"]
services: ["api-gateway", "payment-service", "order-service"]
severity_levels: ["CRITICAL", "HIGH"]
tags: ["cpu", "performance", "saturation", "profiling", "threads"]
version: "1.0"
---

# CPU Saturation

## Overview

CPU saturation (>85% sustained) causes request queuing, latency spikes, and ultimately service failure. Immediate action is required to prevent cascading impact.

## Detection Criteria

- `service_cpu_percent > 85` sustained for >2 minutes
- Usually accompanied by latency increase as threads wait for CPU
- May show as increased GC activity in JVM services

## Investigation Steps

### Step 1: Confirm and Scope

- Is this one service or multiple?
- Is it sustained or spiky? Spiky = GC pauses or burst traffic. Sustained = hot loop or overload.
- Check request rate: did traffic increase proportionally? If yes, auto-scale may resolve it.

### Step 2: Check for Traffic Spike

- Compare `http_requests_total` rate to baseline
- If traffic is 2× normal → likely legitimate load, scale horizontally
- If traffic is normal but CPU is high → application-level issue

### Step 3: Identify CPU-Intensive Code Path

- Look for recent deployments that added CPU-intensive operations (sorting, encryption, regex)
- Check if a new background job was introduced
- Look at goroutine/thread counts — are they growing unbounded?

### Step 4: Check for Infinite Loops or Hot Paths

- Exponential back-off missing in retry logic causes CPU spinning
- Missing break conditions in while loops
- Regex without timeouts on user-provided input

## Remediation Procedure

### Immediate Actions (< 5 minutes)

1. **Scale horizontally** to distribute CPU load:
   - Add 2-4 replicas immediately
   Risk: LOW | Rollback: scale down

2. **Enable rate limiting** if traffic spike is the cause:
   - Limit requests per second at the load balancer level
   Risk: MEDIUM | Rollback: remove rate limit config

3. **Rollback deployment** if CPU spiked after recent deploy:
   - New code likely introduced a hot path
   Risk: LOW | Rollback: redeploy

### Short-Term Actions

4. **Profile the application** to find the hot function:
   - CPU profiling for 30 seconds during incident
   - Identify top functions by CPU time
   Risk: LOW | Rollback: N/A (profiling is read-only)

5. **Restart if GC is the cause**:
   - Force full GC or restart to release fragmented heap
   Risk: LOW | Rollback: N/A

## Escalation Path

If CPU does not drop below 70% within 10 minutes of scaling:
- Escalate to the service team lead
- Consider traffic shedding if user impact is growing

## Prevention

- Load test new deployments before production
- Set CPU alerts at 70% to catch saturation before it becomes critical
- Review and limit background job concurrency

---
incident_id: INC-2025-003
service: api-gateway
severity: CRITICAL
root_cause_category: INFRASTRUCTURE
occurred_at: "2025-02-03"
mttr_minutes: 35
---

# Postmortem: Auto-Scaling Failure During Traffic Surge — INC-2025-003

**Date:** 2025-02-03 | **Duration:** 35 minutes | **Severity:** CRITICAL | **MTTR:** 35 min

## Executive Summary

A marketing campaign drove 2.3× normal traffic to api-gateway. The auto-scaling cooldown period of 5 minutes prevented the cluster from responding fast enough to the sudden traffic increase, causing CPU saturation and significant latency degradation for 35 minutes.

## Contributing Factors

1. **Auto-scaling cooldown too long** — The 5-minute scale-up cooldown was designed to prevent thrashing but was too long for sudden traffic spikes.
2. **Marketing campaign not communicated** — Engineering was not notified of the planned campaign and could not pre-scale.
3. **Maximum replica count too low** — api-gateway was capped at 2 replicas, which was insufficient for 2× traffic.
4. **No pre-scaling for known events** — There was no process to manually scale before anticipated traffic events.

## Preventative Actions

- Reduce HPA scale-up cooldown from 5 minutes to 30 seconds
- Establish marketing-engineering coordination SOP: all campaigns expected to drive >50% traffic increase require 24-hour advance notice
- Increase api-gateway maximum replica count from 2 to 20
- Create a "pre-scaling runbook" for expected high-traffic events
- Add CPU alert at 70% (before saturation) to provide earlier warning

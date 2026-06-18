---
incident_id: INC-2025-004
service: notification-service
severity: HIGH
root_cause_category: APPLICATION
occurred_at: "2025-02-10"
mttr_minutes: 80
---

# Postmortem: Memory Leak — Notification Service — INC-2025-004

**Date:** 2025-02-10 | **Duration:** 80 minutes | **Severity:** HIGH | **MTTR:** 80 min

## Executive Summary

An unbounded in-memory cache introduced in a deployment caused notification-service to gradually consume all available memory over 4 hours, resulting in 3 OOM-kill restarts, each of which caused Kafka consumer rebalances and notification delivery delays.

## Contributing Factors

1. **Cache without eviction policy** — The email template cache had no max_size or TTL. Memory grew indefinitely with new template variations.
2. **Memory alert threshold too high** — Alert was configured at 95%, leaving only a 5-minute window to respond before OOM kill.
3. **No extended soak test** — The staging CI pipeline only ran a 10-minute load test. The leak manifested over 4 hours.
4. **Each restart compounded the problem** — Each OOM restart caused a Kafka consumer rebalance, which temporarily increased consumer lag.

## Preventative Actions

- All in-memory caches must have `max_size` and `ttl` configured — code review checklist item
- Memory alert threshold lowered to 75% for all services
- Add 4-hour soak test to staging pipeline for any deployment touching data structures
- Add LRU cache with max_size=1000, ttl=300s as the organisation default implementation
- Configure memory auto-restart at 85% to control the restart timing

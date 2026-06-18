---
incident_id: INC-2025-006
service: order-service
severity: HIGH
root_cause_category: INFRASTRUCTURE
occurred_at: "2025-03-05"
mttr_minutes: 90
---

# Postmortem: Kafka Consumer Lag — SMTP Bottleneck — INC-2025-006

**Date:** 2025-03-05 | **Duration:** 90 minutes | **Severity:** HIGH | **MTTR:** 90 min

## Executive Summary

The notification-service SMTP provider experienced a degradation, causing email sending to slow from 200ms to 3-8 seconds per email. Since the Kafka consumer processing was synchronous — waiting for email delivery before committing the offset — consumer throughput dropped from 500 msg/s to 12 msg/s, causing lag to reach 85,000 messages.

## Contributing Factors

1. **Synchronous email sending in Kafka consumer** — The consumer blocked on SMTP delivery before processing the next message, tightly coupling Kafka throughput to email provider performance.
2. **No SMTP failover** — Only one SMTP provider was configured; no backup provider.
3. **No Kafka lag alert** — The lag alert was not configured; the issue was discovered by a user reporting delayed notifications.
4. **Single consumer instance** — One consumer could not keep up once throughput dropped.

## Preventative Actions

- Decouple email sending from Kafka consumption: consume → queue internally → send email async
- Configure SMTP provider failover: primary and backup provider with automatic switching
- Add Kafka consumer lag alert at 5,000 messages for all consumer groups
- Scale consumer instances to match partition count (6 consumers for 6 partitions)
- Add SMTP delivery time monitoring: alert if P95 delivery time exceeds 500ms

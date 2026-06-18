---
runbook_id: RB-006
title: "Kafka Consumer Lag"
anomaly_types: ["KAFKA_CONSUMER_LAG"]
services: ["order-service", "notification-service"]
severity_levels: ["HIGH", "MEDIUM"]
tags: ["kafka", "consumer", "lag", "throughput", "backpressure"]
version: "1.0"
---

# Kafka Consumer Lag

## Overview

Consumer lag means consumers are processing messages slower than producers are writing them. This causes message delays, SLA breaches, and eventual backpressure that can destabilize producers.

## Detection Criteria

- `kafka_consumer_lag > 10000` messages for a sustained period
- Lag growing monotonically (not recovering)
- Processing latency per message increasing

## Investigation Steps

### Step 1: Identify the Lagging Consumer Group

- Which consumer group is lagging?
- Which topic/partition has the most lag?
- Is lag on all partitions or just some? (Some = partition rebalance or dead consumer)

### Step 2: Check Consumer Throughput

- How many messages per second is the consumer processing?
- How many messages per second is the producer publishing?
- The gap tells you how long it will take to catch up

### Step 3: Identify Slow Message Processing

- Is each message taking longer to process than expected?
- Check if message processing involves a slow external call (DB write, HTTP call)
- Is the consumer doing single-threaded processing that can be parallelized?

### Step 4: Check for Consumer Failures

- Are consumers restarting frequently? Each restart loses partial progress.
- Are there poison pill messages causing repeated processing failures?
- Check DLQ (dead.letter.queue) for failed message count

### Step 5: Correlate with Downstream Systems

- If consumers write to Postgres, is DB slow? → DB bottleneck
- If consumers call an external API, is that API slow? → external dependency bottleneck

## Remediation Procedure

### Immediate Actions (< 5 minutes)

1. **Scale consumer replicas horizontally** (up to partition count):
   - Each Kafka partition can be consumed by at most 1 consumer in the same group
   - If you have 6 partitions and 1 consumer → scale to 6 consumers for 6× throughput
   Risk: LOW | Rollback: scale down

2. **Identify and skip poison pill messages** (if consumers are retrying the same message):
   - Use Kafka admin API to skip the problematic offset
   - Check DLQ to understand what caused the failure
   Risk: MEDIUM (message is not processed) | Rollback: manually reprocess from DLQ

### Short-Term Actions (< 30 minutes)

3. **Increase consumer fetch.max.bytes** for better batching:
   - Larger batches improve throughput for IO-bound consumers
   Risk: LOW | Rollback: revert config

4. **Reduce downstream dependency latency**:
   - If each message triggers a DB write: batch writes instead of one-per-message
   - If each message calls an external API: add connection pooling and concurrent requests

5. **Temporarily increase partition count** (if near-term solution needed):
   - Adding partitions allows more consumers to run in parallel
   - Note: cannot decrease partition count without full topic recreation
   Risk: MEDIUM | Rollback: not reversible

## Escalation Path

If lag is growing faster than recovery actions can address:
- Consider pausing producers temporarily (if technically feasible)
- Escalate to platform team for Kafka-level optimisation
- Evaluate whether messages older than X minutes can be skipped

## Prevention

- Alert at lag > 1000 (before it becomes a problem at 50,000)
- Set consumer timeout thresholds — fail fast rather than blocking indefinitely
- Test consumer throughput under expected peak load before deploying
- Ensure partition count matches expected peak consumer concurrency

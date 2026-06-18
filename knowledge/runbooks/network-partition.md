---
runbook_id: RB-009
title: "Network Partition / Connection Resets"
anomaly_types: ["NETWORK_PARTITION", "ERROR_RATE_SPIKE", "LATENCY_SPIKE"]
services: ["payment-service", "api-gateway"]
severity_levels: ["CRITICAL", "HIGH"]
tags: ["network", "partition", "connection-reset", "tcp", "retry-storm"]
version: "1.0"
---

# Network Partition / Connection Resets

## Overview

Network partitions cause connection resets between services, leading to retry storms that amplify the load on recovering services. This runbook covers identification and mitigation.

## Detection Criteria

- Connection reset errors in logs ("connection reset by peer", "broken pipe", "ECONNRESET")
- Intermittent errors that recover and recur (vs. sustained errors → outage)
- Error rate spikes correlating with specific source/destination service pairs
- Latency spikes followed by brief recovery followed by another spike

## Investigation Steps

### Step 1: Identify the Affected Network Path

- Which service pair is experiencing connection resets?
- Is it one-directional or both directions?
- Are all replicas of the service affected or only some? (Some = specific pod/host issue)

### Step 2: Check for Retry Storms

- When services retry on connection reset, they can amplify traffic 3-10×
- Check if request rate is abnormally high relative to baseline
- Retry storms make the partition worse by overwhelming the recovering service

### Step 3: Check Infrastructure Layer

- Network connectivity between hosts/pods
- Load balancer health and connection timeout settings
- Any infrastructure changes (network policy changes, security group changes, VPC changes)

### Step 4: Check Service Mesh / Load Balancer

- Are health checks passing for all backends?
- Is the load balancer routing to unhealthy instances?
- TCP keepalive settings: connections may be silently dropped by firewalls after idle time

## Remediation Procedure

### Immediate Actions (< 5 minutes)

1. **Enable exponential back-off with jitter** on all retries:
   - If retries are tight loops (no backoff), they create a retry storm
   - Jitter prevents thundering herd when all clients retry simultaneously
   Risk: LOW | Rollback: N/A

2. **Reduce connection pool max size temporarily**:
   - Fewer active connections reduce the blast radius of resets
   Risk: LOW | Rollback: restore pool size when stable

3. **Restart affected service instances** if they're stuck in a bad state:
   - Some TCP implementations get into broken states after partition recovery
   Risk: LOW | Rollback: N/A

### Short-Term Actions (< 30 minutes)

4. **Check and fix load balancer health check intervals**:
   - If health checks are too infrequent, dead instances stay in rotation
   - Reduce health check interval to 5s with 2 failure threshold

5. **Add TCP keepalive** to persistent connections:
   - Prevents silent connection drops by firewalls
   - Set `SO_KEEPALIVE` and appropriate keepalive interval

6. **Verify network policy / security groups**:
   - Recent infrastructure changes may have altered allowed traffic
   - Verify all service-to-service paths are permitted

## Escalation Path

If connection resets continue after remediation steps:
- Escalate to infrastructure/networking team
- Potential causes requiring infrastructure team: NIC failure, switch failure, network policy misconfiguration

## Prevention

- Always implement exponential backoff with jitter on retries
- Set TCP keepalive on all persistent connections
- Test network partition scenarios in staging
- Use circuit breakers to bound retry storms

---
runbook_id: RB-007
title: "Emergency Deployment Rollback"
anomaly_types: ["DEPLOYMENT_FAILURE", "ERROR_RATE_SPIKE", "LATENCY_SPIKE"]
services: ["api-gateway", "payment-service", "order-service", "user-service", "notification-service", "inventory-service"]
severity_levels: ["CRITICAL", "HIGH"]
tags: ["deployment", "rollback", "release", "regression", "hotfix"]
version: "2.0"
---

# Emergency Deployment Rollback

## Overview

When a deployment causes a production incident, rollback to the previous stable version is the fastest path to recovery. This runbook covers the rollback procedure for all services.

## When to Rollback

Roll back when ALL of the following are true:
1. An anomaly started within 30 minutes of a deployment completing
2. No configuration-only change can fix the issue quickly
3. The deployment correlation confidence is >60%

Do NOT roll back if:
- The issue is infrastructure-related (not deployment-related)
- The rollback itself would cause a more severe issue (e.g., database migration was included)
- The rollback version is known to have a different bug

## Pre-Rollback Check

### Step 1: Confirm Deployment Correlation

- What service was deployed and when?
- What was the git SHA and deployment description?
- Were there known risks flagged at deploy time?
- Did anomaly onset begin within 10 minutes of the deploy completing?

### Step 2: Check for Database Migrations

- Did this deployment include a database migration?
- If yes: rollback may cause the application to run against a migrated schema it doesn't understand
- In this case: consider a forward-fix (hotfix) instead of rollback
- If no migrations: proceed with confidence

### Step 3: Identify the Previous Stable Version

- Check deployment history for the last successful version before this deployment
- Verify that version had no prior incidents

## Rollback Procedure

### Immediate Actions (< 5 minutes)

1. **Trigger rollback deployment** via CI/CD system:
   - Navigate to the service's deployment pipeline
   - Select the previous successful deployment
   - Trigger rollback deployment with `--rollback` flag
   Risk: LOW | Expected: service returns to previous version within 2-3 minutes

2. **Monitor error rate and latency** as rollback deploys:
   - Error rate should begin dropping within 1-2 minutes of rollback starting
   - P99 latency should normalise within 3-5 minutes
   - If no improvement after 5 minutes → the issue may not be deployment-related

3. **Notify the development team** immediately:
   - Slack the service's channel with: service name, version rolled back from, version rolled back to, reason
   - Block the feature branch from redeployment until root cause is understood

### Post-Rollback Actions (< 30 minutes)

4. **Verify resolution**:
   - Confirm error rate has dropped below 1%
   - Confirm latency is within SLA
   - Close the incident if metrics are stable for 5 minutes

5. **Create post-deploy investigation ticket**:
   - The rolled-back change must be investigated before re-deploying
   - Root cause analysis: what code change caused the regression?
   - Fix the root cause in a new commit before redeploying

## Rollback Verification Checklist

- [ ] Error rate < 1%
- [ ] P99 latency < 2× baseline
- [ ] No new error patterns in logs
- [ ] All health endpoints returning healthy
- [ ] Downstream services no longer showing cascade errors

## Escalation Path

If rollback does not resolve the incident within 10 minutes:
- The root cause may be shared infrastructure, not the deployment
- Escalate to the platform team
- Check if the previous version also had these symptoms

## Prevention

- Canary deployments (5% traffic) before full rollout
- Automated smoke tests that run immediately after deployment
- Deployment change freeze windows during peak traffic periods
- Always document known risks in deployment notes

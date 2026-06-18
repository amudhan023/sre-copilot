---
incident_id: INC-2025-011
service: order-service
severity: CRITICAL
root_cause_category: DEPLOYMENT
occurred_at: "2025-05-01"
mttr_minutes: 30
---

# Postmortem: API Contract Break — Cascade Failure — INC-2025-011

**Date:** 2025-05-01 | **Duration:** 30 minutes | **Severity:** CRITICAL | **MTTR:** 30 min

## Executive Summary

A payment-service deployment introduced stricter input validation that broke the API contract with order-service. order-service had been sending a date field in ISO format for 18 months; the new validation required epoch milliseconds. The result was a 60% error rate for order creation while both services appeared internally healthy.

## Contributing Factors

1. **No API contract tests between services** — The API contract between order-service and payment-service existed only in documentation, not in automated tests.
2. **Breaking API change deployed without coordination** — The payment-service team did not identify this as a breaking change and did not coordinate with the order-service team.
3. **No versioning for the payments API** — All consumers were forced onto the same API version simultaneously.
4. **Cascade detection delayed** — Initial investigation focused on order-service (the service reporting errors) rather than payment-service (the root cause).

## Preventative Actions

- Implement Pact contract tests between all service pairs with API dependencies
- Create cross-service API change review process: any validation change requires sign-off from consumer teams
- Version all internal APIs (`/v1/payments`, `/v2/payments`) to allow gradual migration
- Adopt semantic versioning for internal APIs: validation changes are always breaking (major version bump)
- Add API contract validation in payment-service CI that runs against all known consumers

from datetime import datetime, timezone

from mcp_tools.metrics import query_metrics

# Manual smoke test - calls query_metrics directly against a real Prometheus
# instance and prints the result. Not a pytest test; see tests/test_metrics.py
# for the automated version that mocks the HTTP response.


result = query_metrics(
    service="payment-api",
    metric="payment_api_request_duration_seconds_sum",
    start_time=datetime(2026, 9, 4, 10, 0, tzinfo=timezone.utc),
    end_time=datetime(2026, 9, 4, 10, 10, tzinfo=timezone.utc),
)

print(result)
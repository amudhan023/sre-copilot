from datetime import datetime, timezone

from mcp_tools.metrics import query_metrics


result = query_metrics(
    service="payments-api",
    metric="cpu_usage",
    start_time=datetime(2026, 9, 4, 10, 0, tzinfo=timezone.utc),
    end_time=datetime(2026, 9, 4, 10, 10, tzinfo=timezone.utc),
)

print(result)
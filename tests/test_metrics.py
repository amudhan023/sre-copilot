from datetime import datetime, timezone

from mcp_tools.metrics import query_metrics


def test_query_metrics():
    start = datetime(2026, 9, 4, 10, 0, tzinfo=timezone.utc)
    end = datetime(2026, 9, 4, 10, 10, tzinfo=timezone.utc)

    result = query_metrics(
        service="payments-api",
        metric="cpu_usage",
        start_time=start,
        end_time=end,
    )

    assert result["service"] == "payments-api"
    assert result["metric"] == "cpu_usage"
    assert result["data"] == []
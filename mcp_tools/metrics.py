from datetime import datetime


def query_metrics(
    service: str,
    metric: str,
    start_time: datetime,
    end_time: datetime,
) -> dict:
    """
    Query metrics for a service within a time range.

    This is intentionally a simple implementation for now.
    Later, this function will query Prometheus through MCP.
    """

    return {
        "service": service,
        "metric": metric,
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
        "data": [],
    }
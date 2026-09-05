def search_logs(
    service: str,
    query: str,
    start_time: str,
    end_time: str,
) -> dict:
    """Search logs for a service within a time range."""

    return {
        "service": service,
        "query": query,
        "start_time": start_time,
        "end_time": end_time,
        "logs": [],
    }
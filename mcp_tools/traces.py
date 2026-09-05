def get_traces(
    service: str,
    operation: str,
    start_time: str,
    end_time: str,
) -> dict:
    """Find traces for a service and operation within a time range."""

    return {
        "service": service,
        "operation": operation,
        "start_time": start_time,
        "end_time": end_time,
        "traces": [],
    }
def recent_deploys(
    service: str,
    start_time: str,
    end_time: str,
) -> dict:
    """Find recent deployments for a service within a time range."""
    return {
        "service": service,
        "start_time": start_time,
        "end_time": end_time,
        "deployments": [],
    }
def find_similar_incidents(
    service: str,
    query: str,
) -> dict:
    """Find incidents similar to the current incident."""
    return {
        "service": service,
        "query": query,
        "incidents": [],
    }
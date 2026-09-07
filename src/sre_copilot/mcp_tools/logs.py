import json
from datetime import datetime
from pathlib import Path


LOG_FILE = Path(__file__).resolve().parents[3] / "data" / "logs.json"

# Searches the local logs.json fixture for log lines matching a service,
# time window, and free-text query (matched against level + message).


def search_logs(
    service: str,
    query: str,
    start_time: str,
    end_time: str,
) -> dict:
    """Search logs for a service within a time range."""

    start = datetime.fromisoformat(start_time)
    end = datetime.fromisoformat(end_time)

    with LOG_FILE.open() as file:
        logs = json.load(file)

    matching_logs = []

    for log in logs:
        timestamp = datetime.fromisoformat(log["timestamp"])

        if not (start <= timestamp <= end):
            continue

        if log["service"] != service:
            continue

        searchable_text = (
            f"{log['level']} {log['message']}"
        ).lower()

        if query.lower() not in searchable_text:
            continue

        matching_logs.append(log)

    return {
        "service": service,
        "query": query,
        "start_time": start_time,
        "end_time": end_time,
        "logs": matching_logs,
    }
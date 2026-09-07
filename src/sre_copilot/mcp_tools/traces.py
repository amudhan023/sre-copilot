import json
from datetime import datetime
from pathlib import Path


TRACE_FILE = Path(__file__).resolve().parents[3] / "data" / "traces.json"

# Looks up traces for a service/operation/time window from the local
# traces.json fixture. An empty operation means "match any operation" - that
# lets the agent discover real operation names instead of having to guess one.


def get_traces(
    service: str,
    operation: str,
    start_time: str,
    end_time: str,
) -> dict:
    """Find traces for a service and operation within a time range."""

    start = datetime.fromisoformat(start_time)
    end = datetime.fromisoformat(end_time)

    with TRACE_FILE.open() as file:
        traces = json.load(file)

    matching_traces = [
        trace
        for trace in traces
        if (
            trace["service"] == service
            and (
                not operation
                or operation.lower() in trace["operation"].lower()
            )
            and start <= datetime.fromisoformat(trace["timestamp"]) <= end
        )
    ]

    return {
        "service": service,
        "operation": operation,
        "start_time": start_time,
        "end_time": end_time,
        "traces": matching_traces,
    }
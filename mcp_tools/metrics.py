import os
from datetime import datetime

import httpx


PROMETHEUS_URL = os.environ.get(
    "PROMETHEUS_URL",
    "http://localhost:9090",
)


def query_metrics(
    service: str,
    metric: str,
    start_time: datetime,
    end_time: datetime,
) -> dict:
    query = f'{metric}{{service="{service}"}}'

    response = httpx.get(
        f"{PROMETHEUS_URL}/api/v1/query_range",
        params={
            "query": query,
            "start": start_time.timestamp(),
            "end": end_time.timestamp(),
            "step": 15,
        },
        timeout=10,
    )

    response.raise_for_status()

    payload = response.json()

    return {
        "service": service,
        "metric": metric,
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
        "data": payload["data"]["result"],
    }
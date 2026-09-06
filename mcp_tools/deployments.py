import json
from datetime import datetime
from pathlib import Path


DEPLOYMENT_FILE = Path(__file__).parent.parent / "data" / "deployments.json"

# Looks up recent deployments for a service from the local deployments.json
# fixture, filtered to a time window. Stands in for a real deployment/CD
# system in this project.


def recent_deploys(
    service: str,
    start_time: str,
    end_time: str,
) -> dict:
    """Find recent deployments for a service within a time range."""

    start = datetime.fromisoformat(start_time)
    end = datetime.fromisoformat(end_time)

    with DEPLOYMENT_FILE.open() as file:
        deployments = json.load(file)

    matching_deployments = [
        deployment
        for deployment in deployments
        if (
            deployment["service"] == service
            and start <= datetime.fromisoformat(deployment["timestamp"]) <= end
        )
    ]

    return {
        "service": service,
        "start_time": start_time,
        "end_time": end_time,
        "deployments": matching_deployments,
    }
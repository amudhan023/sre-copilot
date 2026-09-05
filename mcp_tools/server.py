from datetime import datetime
from mcp.server.mcpserver import MCPServer
from mcp_tools.metrics import query_metrics
from mcp_tools.logs import search_logs
from mcp_tools.traces import get_traces
from mcp_tools.deployments import recent_deploys
from mcp_tools.incidents import find_similar_incidents


mcp = MCPServer("sre-tools")



@mcp.tool()
def search_similar_incidents(
    service: str,
    query: str,
) -> dict:
    """Find incidents similar to the current incident."""
    return find_similar_incidents(
        service=service,
        query=query,
    )

@mcp.tool()
def search_recent_deploys(
    service: str,
    start_time: str,
    end_time: str,
) -> dict:
    """Find recent deployments for a service over a time range."""
    return recent_deploys(
        service=service,
        start_time=start_time,
        end_time=end_time,
    )

@mcp.tool()
def search_service_logs(
        service: str,
        query: str,
        start_time: str,
        end_time: str,
) -> dict:
    """Search logs for a service over a time range."""

    return search_logs(
        service=service,
        query=query,
        start_time=start_time,
        end_time=end_time,
    )


@mcp.tool()
def get_metrics(
        service: str,
        metric: str,
        start_time: str,
        end_time: str,
) -> dict:
    """Query a metric for a service over a time range."""

    return query_metrics(
        service=service,
        metric=metric,
        start_time=datetime.fromisoformat(start_time),
        end_time=datetime.fromisoformat(end_time),
    )


@mcp.tool()
def search_traces(
        service: str,
        operation: str,
        start_time: str,
        end_time: str,
) -> dict:
    """Find traces for a service and operation over a time range."""

    return get_traces(
        service=service,
        operation=operation,
        start_time=start_time,
        end_time=end_time,
    )


if __name__ == "__main__":
    mcp.run()

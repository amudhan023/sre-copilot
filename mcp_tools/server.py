from datetime import datetime

from mcp.server.mcpserver import MCPServer

from mcp_tools.metrics import query_metrics

mcp = MCPServer("sre-tools")


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


if __name__ == "__main__":
    mcp.run()

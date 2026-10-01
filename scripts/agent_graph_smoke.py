import asyncio
import sys
from datetime import datetime, timedelta, timezone

from mcp import ClientSession
from mcp.client.stdio import stdio_client

from sre_copilot.agent.graph import build_graph
from sre_copilot.agent.tools import ToolSession, mcp_tools_to_gemini, observe_server_params

# Manual end-to-end script (not a pytest test). It starts observe-mcp-server
# as a subprocess, builds the Gemini tool definitions from it, builds the
# LangGraph graph, and investigates one incident over the last 15 minutes of
# live data from the observability stack.
#
#     uv run python scripts/agent_graph_smoke.py [service] [alert_name]
#
# The incident carries a tenant, exactly like an alert coming from the
# receiver would. tool_node stamps that tenant into every tenant-scoped
# call, so the historical incident search stays inside the tenant.


# Local development/testing tenant. Ingestion writes the sample incidents
# under this tenant (see sre_copilot/rag/ingest.py).
DEFAULT_TENANT = "default"


async def main(service: str, alert_name: str):
    end = datetime.now(timezone.utc).replace(microsecond=0)
    start = end - timedelta(minutes=15)

    async with stdio_client(observe_server_params()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            mcp_result = await session.list_tools()
            gemini_tool = mcp_tools_to_gemini(mcp_result.tools)

            graph = build_graph(
                ToolSession(session),
                gemini_tool,
            )

            initial_state = {
                "incident_id": "INC-2101",
                "tenant": DEFAULT_TENANT,
                "service": service,
                "alert_name": alert_name,
                "start_time": start.isoformat(),
                "end_time": end.isoformat(),
                "messages": [
                    {
                        "role": "user",
                        "parts": [
                            {
                                "text": (
                                    f"Alert {alert_name} fired for {service}. "
                                    f"Investigate between {start.isoformat()} "
                                    f"and {end.isoformat()} and find the root cause."
                                )
                            }
                        ],
                    }
                ]
            }

            result = await graph.ainvoke(initial_state)

            print("\nFinal result:")
            print(result["messages"][-1])


if __name__ == "__main__":
    asyncio.run(main(
        sys.argv[1] if len(sys.argv) > 1 else "payment-api",
        sys.argv[2] if len(sys.argv) > 2 else "HighErrorRate",
    ))

import asyncio
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from sre_copilot.agent.graph import build_graph
from sre_copilot.agent.tools import mcp_tools_to_gemini

# Manual end-to-end script (not a pytest test). It spins up the real MCP
# server as a subprocess, builds the Gemini tool definitions from it, builds
# the LangGraph graph, and runs one hardcoded incident through the whole thing.
#
# The incident carries a tenant, exactly like an alert coming from the
# receiver would. tool_node stamps that tenant into every tenant-scoped MCP
# call, so the historical incident search stays inside the tenant.


# Local development/testing tenant. Ingestion writes the sample incidents
# under this tenant (see sre_copilot/rag/ingest.py).
DEFAULT_TENANT = "default"


async def main():
    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "sre_copilot.mcp_tools.server"],
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            mcp_result = await session.list_tools()
            gemini_tool = mcp_tools_to_gemini(mcp_result.tools)

            graph = build_graph(
                session,
                gemini_tool,
            )

            initial_state = {
                "incident_id": "INC-2101",
                "tenant": DEFAULT_TENANT,
                "service": "payment-api",
                "alert_name": "HighCPUUsage",
                "start_time": "2026-09-04T10:00:00+00:00",
                "end_time": "2026-09-04T10:10:00+00:00",
                "messages": [
                    {
                        "role": "user",
                        "parts": [
                            {
                                "text": """
                                The payment-api service is having
                                high CPU usage.

                                Investigate CPU usage between
                                2026-09-04T10:00:00+00:00 and
                                2026-09-04T10:10:00+00:00.
                                """
                            }
                        ],
                    }
                ]
            }

            result = await graph.ainvoke(initial_state)

            print("\nFinal result:")
            print(result["messages"][-1])


if __name__ == "__main__":
    asyncio.run(main())

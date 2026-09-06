import asyncio
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from agent.graph import build_graph
from agent.tools import mcp_tools_to_gemini

# Manual end-to-end script (not a pytest test, despite the filename). It
# spins up the real MCP server as a subprocess, builds the Gemini tool
# definitions from it, builds the LangGraph graph, and runs one hardcoded
# incident through the whole thing so you can eyeball the final RCA output.


async def main():
    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "mcp_tools.server"],
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # Discover tools from MCP
            mcp_result = await session.list_tools()

            gemini_tool = mcp_tools_to_gemini(
                mcp_result.tools
            )

            # Build LangGraph
            graph = build_graph(
                session,
                gemini_tool,
            )

            initial_state = {
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
import asyncio
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# A manual smoke test: starts the MCP server as a subprocess, lists its
# tools, and calls get_metrics once to check the wiring works end to end.
# Not part of the automated test suite - run it directly with
# `python -m sre_copilot.mcp_tools.client`.


async def main():
    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "sre_copilot.mcp_tools.server"],
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()

            result = await session.call_tool(
                "get_metrics",
                {
                    "service": "payment-api",
                    "metric": "payment_api_request_duration_seconds_sum",
                    "start_time": "2026-09-04T10:00:00+00:00",
                    "end_time": "2026-09-04T10:10:00+00:00",
                },
            )

            print("\nTool result:")
            print(result.content)

            print("Available tools:")

            for tool in tools.tools:
                print(f"- {tool.name}: {tool.description}")


if __name__ == "__main__":
    asyncio.run(main())

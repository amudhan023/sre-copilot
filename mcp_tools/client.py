import asyncio
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "mcp_tools.server"],
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()

            result = await session.call_tool(
                "get_metrics",
                {
                    "service": "payments-api",
                    "metric": "cpu_usage",
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

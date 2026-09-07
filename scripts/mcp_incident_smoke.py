"""MCP integration smoke test for the historical incident search tool.

Starts the real MCP server as a subprocess, checks that it exposes
``search_similar_incidents``, and calls it once so the full RAG pipeline
(BGE-M3 -> pgvector + FTS -> RRF -> BGE reranker) actually runs.

Requires PostgreSQL with ingested chunks. Run it directly:
    uv run python scripts/mcp_incident_smoke.py
"""

import asyncio
import json
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# Local development/testing tenant. Ingestion writes the sample incidents
# under this tenant (see sre_copilot/rag/ingest.py).
DEFAULT_TENANT = "default"

TOOL_NAME = "search_similar_incidents"
QUERY = "payment failures caused by database connection pool exhaustion"


async def main():
    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "sre_copilot.mcp_tools.server"],
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            tool_names = [tool.name for tool in tools.tools]

            print("Available tools:")
            for name in tool_names:
                print(f"- {name}")

            if TOOL_NAME not in tool_names:
                raise AssertionError(f"MCP server does not expose {TOOL_NAME}")

            tool = next(tool for tool in tools.tools if tool.name == TOOL_NAME)
            parameters = tool.input_schema["properties"]

            if "tenant" not in parameters:
                raise AssertionError(f"{TOOL_NAME} does not accept a tenant")

            print(f"\nCalling {TOOL_NAME} (tenant={DEFAULT_TENANT})...")

            result = await session.call_tool(
                TOOL_NAME,
                {
                    "tenant": DEFAULT_TENANT,
                    "service": "payment-api",
                    "query": QUERY,
                },
            )

            # The server returns the tool's dict as JSON text content.
            payload = json.loads(result.content[0].text)
            incidents = payload["incidents"]

            print(f"\nTenant: {payload['tenant']}")
            print(f"Service: {payload['service']}")
            print(f"Query: {payload['query']}")

            for index, incident in enumerate(incidents, start=1):
                print(f"\n--- Historical incident {index} ---")
                print("ID:", incident["incident_id"])
                print("Score:", incident["score"])
                print("Title:", incident["title"])
                print("Content:", incident["content"])

            if not incidents:
                raise AssertionError("MCP incident search returned no incidents")
            if len(incidents) > 5:
                raise AssertionError("MCP incident search returned more than 5 incidents")

            incident_ids = [incident["incident_id"] for incident in incidents]
            if len(set(incident_ids)) != len(incident_ids):
                raise AssertionError("MCP incident search returned duplicate incidents")

            print("\nMCP incident search test passed.")


if __name__ == "__main__":
    asyncio.run(main())

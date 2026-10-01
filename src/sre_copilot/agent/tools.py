import asyncio
import json
import os
import sys
from types import SimpleNamespace

from google.genai import types
from mcp import StdioServerParameters

# The agent's tools come from two places:
#
# - observe-mcp-server (a separate repo and package): logs, traces, metrics,
#   dashboards and code. It runs as a subprocess and is reached over MCP.
# - search_similar_incidents: runs in this process, because it searches this
#   project's own pgvector knowledge base with models loaded here.
#
# ToolSession hides that split from the graph: tool_node calls call_tool()
# for every tool and does not care where the tool runs.


SIMILAR_INCIDENTS = types.FunctionDeclaration(
    name="search_similar_incidents",
    description=(
        "Search past incidents for ones similar to the current incident. "
        "Results are supporting evidence for a hypothesis, never proof of "
        "what happened in the current incident. The search is scoped to the "
        "incident's tenant automatically."
    ),
    # No tenant parameter: tool_node stamps it from the incident state, so
    # the LLM is never offered the choice.
    parameters={
        "type": "object",
        "properties": {
            "service": {"type": "string"},
            "query": {"type": "string", "description": "What went wrong, in words."},
        },
        "required": ["service", "query"],
    },
)


def observe_server_params() -> StdioServerParameters:
    """How to start observe-mcp-server as a subprocess.

    The MCP client gives the subprocess only a minimal environment by
    default, so pass ours through: the OPENSEARCH_/PROMETHEUS_/GRAFANA_/
    GITHUB_ settings live in this project's .env.
    """
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "observe_mcp_server"],
        env=dict(os.environ),
    )


def _find_similar_incidents(**arguments) -> dict:
    # Imported on first use so building the graph does not load the RAG stack.
    from sre_copilot.rag.incidents import find_similar_incidents

    return find_similar_incidents(**arguments)


LOCAL_TOOLS = {"search_similar_incidents": _find_similar_incidents}


class ToolSession:
    """Routes each tool call to a local function or to the MCP session."""

    def __init__(self, mcp_session):
        self.mcp_session = mcp_session

    async def call_tool(self, name, arguments):
        local = LOCAL_TOOLS.get(name)
        if local is None:
            return await self.mcp_session.call_tool(name, arguments)

        # Same contract as an MCP result: a failure is reported to the LLM
        # instead of ending the whole investigation.
        try:
            result = await asyncio.to_thread(local, **arguments)
        except Exception as error:
            return SimpleNamespace(content=f"Error executing tool {name}: {error}", is_error=True)
        return SimpleNamespace(content=json.dumps(result), is_error=False)


def mcp_tools_to_gemini(mcp_tools):
    """One Gemini Tool with the MCP server's tools plus the local ones."""
    declarations = [
        types.FunctionDeclaration(
            name=tool.name,
            description=tool.description,
            parameters=tool.input_schema,
        )
        for tool in mcp_tools
    ]
    declarations.append(SIMILAR_INCIDENTS)

    return types.Tool(
        function_declarations=declarations
    )

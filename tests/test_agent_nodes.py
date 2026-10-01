import asyncio
import json
from types import SimpleNamespace

import pytest

from sre_copilot.agent import nodes, tools

# Tests the tenant scoping that tool_node applies to MCP tool calls. The
# tenant comes from the incident in AgentState, never from the LLM, so these
# tests use function calls that carry a wrong tenant or none at all.


def call(name, args):
    return SimpleNamespace(name=name, args=args)


def test_tenant_from_state_overrides_the_tenant_the_llm_chose():
    arguments = nodes.tool_arguments(
        call("search_similar_incidents", {"tenant": "other-tenant", "service": "payment-api", "query": "timeouts"}),
        {"tenant": "acme"},
    )

    assert arguments == {
        "tenant": "acme",
        "service": "payment-api",
        "query": "timeouts",
    }


def test_tenant_is_added_when_the_llm_leaves_it_out():
    arguments = nodes.tool_arguments(
        call("search_similar_incidents", {"service": "payment-api", "query": "timeouts"}),
        {"tenant": "acme"},
    )

    assert arguments["tenant"] == "acme"


def test_state_without_tenant_fails_instead_of_searching_unscoped():
    with pytest.raises(ValueError):
        nodes.tool_arguments(
            call("search_similar_incidents", {"service": "payment-api", "query": "timeouts"}),
            {"service": "payment-api"},
        )


def test_other_tools_are_left_alone():
    arguments = nodes.tool_arguments(
        call("list_metrics", {"service": "payment-api"}),
        {"tenant": "acme"},
    )

    assert arguments == {"service": "payment-api"}


# --- ToolSession: local incident search vs observe-mcp-server --------------


class FakeMcpSession:
    def __init__(self):
        self.calls = []

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return SimpleNamespace(content="from mcp", is_error=False)


def test_incident_search_runs_locally(monkeypatch):
    mcp_session = FakeMcpSession()
    monkeypatch.setitem(tools.LOCAL_TOOLS, "search_similar_incidents", lambda **kwargs: {"incidents": [kwargs]})

    result = asyncio.run(tools.ToolSession(mcp_session).call_tool(
        "search_similar_incidents", {"tenant": "acme", "service": "payment-api", "query": "timeouts"}
    ))

    assert mcp_session.calls == []
    assert json.loads(result.content)["incidents"][0]["tenant"] == "acme"


def test_local_tool_failure_is_reported_not_raised(monkeypatch):
    def broken(**kwargs):
        raise RuntimeError("database is down")

    monkeypatch.setitem(tools.LOCAL_TOOLS, "search_similar_incidents", broken)

    result = asyncio.run(tools.ToolSession(FakeMcpSession()).call_tool("search_similar_incidents", {}))

    assert result.is_error
    assert "database is down" in result.content


def test_other_tools_go_to_the_mcp_server():
    mcp_session = FakeMcpSession()

    result = asyncio.run(tools.ToolSession(mcp_session).call_tool("search_service_logs", {"service": "payment-api"}))

    assert mcp_session.calls == [("search_service_logs", {"service": "payment-api"})]
    assert result.content == "from mcp"


def test_gemini_tool_offers_incident_search_without_a_tenant_argument():
    mcp_tool = SimpleNamespace(name="list_metrics", description="List metrics",
                               input_schema={"type": "object", "properties": {"service": {"type": "string"}}})

    declarations = {d.name: d for d in tools.mcp_tools_to_gemini([mcp_tool]).function_declarations}

    assert set(declarations) == {"list_metrics", "search_similar_incidents"}
    assert "tenant" not in declarations["search_similar_incidents"].parameters.properties

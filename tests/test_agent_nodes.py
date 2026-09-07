from types import SimpleNamespace

import pytest

from sre_copilot.agent import nodes

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

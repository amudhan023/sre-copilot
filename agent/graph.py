from functools import partial

from langgraph.graph import StateGraph, END

from agent.state import AgentState
from agent.nodes import llm_node, tool_node

# This wires up the LangGraph state machine that drives the investigation.
# It's a simple two-node loop: "llm" decides what to do next (answer, or call
# a tool), and "tool" runs whatever tool the LLM asked for. After a tool call
# we always go back to "llm" so it can look at the result and decide again.
# The loop only ends when the LLM's latest message has no tool calls left in
# it, which is what route_after_llm checks below.


def route_after_llm(state: AgentState):
    last_message = state["messages"][-1]

    for part in last_message.parts:
        if part.function_call:
            return "tool"

    return END

def build_graph(session, gemini_tool):
    graph = StateGraph(AgentState)

    async def llm(state):
        return llm_node(
            state,
            gemini_tool,
        )

    async def tool(state):
        return await tool_node(
            state,
            session,
        )

    graph.add_node("llm", llm)
    graph.add_node("tool", tool)

    graph.set_entry_point("llm")

    graph.add_conditional_edges(
        "llm",
        route_after_llm,
        {
            "tool": "tool",
            END: END,
        },
    )

    graph.add_edge("tool", "llm")

    return graph.compile()
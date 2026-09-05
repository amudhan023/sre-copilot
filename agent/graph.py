from functools import partial

from langgraph.graph import StateGraph, END

from agent.state import AgentState
from agent.nodes import llm_node, tool_node


def route_after_llm(state: AgentState):
    last_message = state["messages"][-1]

    for part in last_message.parts:
        if part.function_call:
            return "tool"

    return END

def build_graph(session, gemini_tool):
    graph = StateGraph(AgentState)

    def llm(state):
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
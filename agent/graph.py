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

def build_graph(gemini_tool, session):
    graph = StateGraph(AgentState)

    # LangGraph calls nodes with the state alone, so bind the rest.
    graph.add_node("llm", partial(llm_node, gemini_tool=gemini_tool))
    graph.add_node("tool", partial(tool_node, session=session))

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
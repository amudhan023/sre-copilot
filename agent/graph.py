from langgraph.graph import StateGraph, END

from agent.state import AgentState
from agent.nodes import llm_node, tool_node


def route_after_llm(state: AgentState):
    last_message = state["messages"][-1]

    for part in last_message.parts:
        if part.function_call:
            return "tool"

    return END
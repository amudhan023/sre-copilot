import asyncio

from sre_copilot.agent.llm import continue_gemini
from sre_copilot.agent.state import AgentState

# The two LangGraph nodes that make up the investigation loop. llm_node asks
# the LLM what to do next and hands back its response as-is. tool_node looks
# at the LLM's last message, runs every tool call it contains in parallel
# (via asyncio.gather), and turns each result into a plain text message the
# LLM can read on its next turn.


def llm_node(state: AgentState, gemini_tool) -> AgentState:
    response = continue_gemini(
        state["messages"],
        gemini_tool,
    )

    return {
        "messages": [
            response.candidates[0].content
        ]
    }


async def tool_node(state: AgentState, session) -> AgentState:
    last_message = state["messages"][-1]

    tool_calls = [
        part.function_call
        for part in last_message.parts
        if part.function_call
    ]

    if not tool_calls:
        return {"messages": []}

    results = await asyncio.gather(
        *[
            session.call_tool(
                call.name,
                dict(call.args),
            )
            for call in tool_calls
        ]
    )

    tool_results = [
        {
            "text": (
                f"Result from {call.name}:\n"
                f"{result.content}"
            )
        }
        for call, result in zip(tool_calls, results)
    ]

    return {
        "messages": [
            {
                "role": "user",
                "parts": tool_results,
            }
        ]
    }

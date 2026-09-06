import asyncio

from agent.llm import continue_gemini
from agent.state import AgentState


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
        return state

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
        "messages": state["messages"] + [
            {
                "role": "user",
                "parts": tool_results,
            }
        ]
    }

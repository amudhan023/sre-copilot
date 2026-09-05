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

    for part in last_message.parts:
        if not part.function_call:
            continue

        function_call = part.function_call

        result = await session.call_tool(
            function_call.name,
            dict(function_call.args),
        )

        return {
            "messages": [
                {
                    "role": "user",
                    "parts": [
                        {
                            "text": (
                                f"Result from {function_call.name}:\n"
                                f"{result.content}"
                            )
                        }
                    ],
                }
            ]
        }

    # Nothing to add; returning state would re-append the whole history.
    return {"messages": []}
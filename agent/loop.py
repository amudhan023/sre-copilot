from agent.llm import ask_gemini, continue_gemini


async def run_agent(session, prompt: str, gemini_tool):
    messages = [
        {
            "role": "user",
            "parts": [
                {
                    "text": prompt
                }
            ],
        }
    ]

    response = ask_gemini(
        prompt,
        gemini_tool,
    )

    while True:
        function_call = None

        for part in response.candidates[0].content.parts:
            if part.function_call:
                function_call = part.function_call
                break

        # Gemini has finished reasoning and doesn't need another tool.
        if function_call is None:
            return response

        print(f"Calling tool: {function_call.name}")
        print(f"Arguments: {function_call.args}")

        # Application executes the MCP tool.
        result = await session.call_tool(
            function_call.name,
            dict(function_call.args),
        )

        print(f"Tool result: {result.content}")

        # Remember Gemini's previous response.
        messages.append(
            response.candidates[0].content
        )

        # Remember the tool result.
        messages.append(
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
        )

        # Ask Gemini what to do next.
        response = continue_gemini(
            messages,
            gemini_tool,
        )
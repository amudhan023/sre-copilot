from agent.llm import ask_gemini, continue_gemini


async def run_agent(session, prompt: str, gemini_tool):
    messages = [
        {
            "role": "user",
            "parts": [{"text": prompt}],
        }
    ]

    response = ask_gemini(prompt, gemini_tool)

    while True:
        function_call = None

        for part in response.candidates[0].content.parts:
            if part.function_call:
                function_call = part.function_call
                break

        # Gemini is finished.
        if function_call is None:
            return response

        # Keep Gemini's response in the history.
        messages.append(response.candidates[0].content)

        # Execute the requested MCP tool.
        result = await session.call_tool(
            function_call.name,
            dict(function_call.args),
        )

        print(f"Calling tool: {function_call.name}")

        # Keep the tool result in the history.
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

        # Ask Gemini again using the accumulated history.
        response = continue_gemini(messages)
from agent.llm import ask_gemini, continue_gemini


async def run_agent(session, prompt: str, gemini_tool):
    response = ask_gemini(prompt, gemini_tool)

    while True:
        function_call = None

        for part in response.candidates[0].content.parts:
            if part.function_call:
                function_call = part.function_call
                break

        # Gemini has finished reasoning.
        if function_call is None:
            return response

        # Execute the requested MCP tool.
        result = await session.call_tool(
            function_call.name,
            dict(function_call.args),
        )

        # Add the tool result to the conversation.
        contents = [
            {
                "role": "user",
                "parts": [{"text": prompt}],
            },
            response.candidates[0].content,
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
            },
        ]

        # Ask Gemini what to do next.
        response = continue_gemini(contents)
from agent.llm import ask_gemini, continue_gemini


async def run_agent(session, prompt: str, gemini_tool):
    response = ask_gemini(prompt, gemini_tool)

    for part in response.candidates[0].content.parts:
        if not part.function_call:
            continue

        function_call = part.function_call

        result = await session.call_tool(
            function_call.name,
            dict(function_call.args),
        )

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
                            f"Here is the result from the "
                            f"{function_call.name} tool:\n"
                            f"{result.content}"
                        )
                    }
                ],
            },
        ]

        return continue_gemini(contents)

    return response
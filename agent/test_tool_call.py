from agent.llm import ask_gemini


response = ask_gemini(
    """
    The payments-api service is having an incident.

    Investigate its CPU usage between
    2026-09-04T10:00:00+00:00 and
    2026-09-04T10:10:00+00:00.
    """
)

for part in response.candidates[0].content.parts:
    if part.function_call:
        print("Tool requested:")
        print("Name:", part.function_call.name)
        print("Arguments:", part.function_call.args)

    if part.text:
        print("Text:", part.text)
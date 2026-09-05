import asyncio

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from agent.llm import ask_gemini, continue_gemini

async def main():
    # Start our MCP server as a subprocess.
    server_params = StdioServerParameters(
        command="python",
        args=["-m", "mcp_tools.server"],
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # Ask Gemini to decide whether it needs the tool.
            response = ask_gemini(
                """
                The payments-api service is having an incident.

                Investigate its CPU usage between
                2026-09-04T10:00:00+00:00 and
                2026-09-04T10:10:00+00:00.
                """
            )

            for part in response.candidates[0].content.parts:
                if not part.function_call:
                    continue

                function_call = part.function_call

                print("Gemini requested:")
                print("Tool:", function_call.name)
                print("Arguments:", function_call.args)

                # Execute the requested tool through MCP.
                result = await session.call_tool(
                    function_call.name,
                    dict(function_call.args),
                )

                print("\nMCP tool result:")
                print(result.content)

                # Send the tool result back to Gemini.
                contents = [
                    {
                        "role": "user",
                        "parts": [
                            {
                                "text": """
                                The payments-api service is having an incident.

                                Investigate its CPU usage between
                                2026-09-04T10:00:00+00:00 and
                                2026-09-04T10:10:00+00:00.
                                """
                            }
                        ],
                    },
                    response.candidates[0].content,
                    {
                        "role": "user",
                        "parts": [
                            {
                                "text": f"Here is the result from the get_metrics tool:\n{result.content}"
                            }
                        ],
                    },
                ]

                final_response = continue_gemini(contents)

                print("\nFinal Gemini response:")
                print(final_response.text)


if __name__ == "__main__":
    asyncio.run(main())
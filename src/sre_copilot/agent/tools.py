from google.genai import types

# Converts the tool list we get from the MCP server into the shape Gemini's
# function-calling API expects: a single Tool made up of FunctionDeclarations.


def mcp_tools_to_gemini(mcp_tools):
    declarations = []

    for tool in mcp_tools:
        declarations.append(
            types.FunctionDeclaration(
                name=tool.name,
                description=tool.description,
                parameters=tool.input_schema,
            )
        )

    return types.Tool(
        function_declarations=declarations
    )
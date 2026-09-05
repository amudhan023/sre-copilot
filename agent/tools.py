from google.genai import types


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
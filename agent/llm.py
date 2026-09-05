import os

from google import genai
from google.genai import types


client = genai.Client(
    api_key=os.environ["GEMINI_API_KEY"]
)


get_metrics_declaration = {
    "name": "get_metrics",
    "description": "Query a metric for a service over a time range.",
    "parameters": {
        "type": "object",
        "properties": {
            "service": {
                "type": "string",
                "description": "Name of the service",
            },
            "metric": {
                "type": "string",
                "description": "Metric to query",
            },
            "start_time": {
                "type": "string",
                "description": "Start time in ISO format",
            },
            "end_time": {
                "type": "string",
                "description": "End time in ISO format",
            },
        },
        "required": [
            "service",
            "metric",
            "start_time",
            "end_time",
        ],
    },
}

def continue_gemini(contents):
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=contents,
    )

    return response

def ask_gemini(prompt: str):
    tool = types.Tool(
        function_declarations=[get_metrics_declaration]
    )

    config = types.GenerateContentConfig(
        tools=[tool]
    )

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
        config=config,
    )

    return response
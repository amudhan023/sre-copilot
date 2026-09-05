import os

from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

client = genai.Client(
    api_key=os.environ["GEMINI_API_KEY"]
)

# Free-tier rate limits are per model. Override to switch budgets.
MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-lite")


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


def ask_gemini(prompt: str, tool=None):
    config = types.GenerateContentConfig(
        tools=[tool] if tool else None
    )

    return client.models.generate_content(
        model=MODEL,
        contents=prompt,
        config=config,
    )


def continue_gemini(contents, tool):
    config = types.GenerateContentConfig(
        tools=[tool]
    )

    return client.models.generate_content(
        model=MODEL,
        contents=contents,
        config=config,
    )
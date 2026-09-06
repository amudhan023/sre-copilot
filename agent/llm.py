import os

from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

# Free-tier rate limits are per model. Override to switch budgets.
MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")

client = genai.Client(
    api_key=os.environ["GEMINI_API_KEY"]
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

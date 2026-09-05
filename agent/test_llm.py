from agent.llm import ask_gemini


response = ask_gemini(
    "Explain what an SRE incident is in one sentence."
)

print(response)
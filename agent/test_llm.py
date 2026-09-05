from langchain_core.messages import HumanMessage

from agent.llm import ask_gemini


messages = [
    HumanMessage(
        content="Explain what LangGraph is in one sentence."
    )
]

response = ask_gemini(messages)

print(response.content)
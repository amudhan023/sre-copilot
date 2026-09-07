import asyncio
from types import SimpleNamespace

import pytest

from sre_copilot.agent import claude_code_llm


def test_messages_to_prompt_preserves_roles_and_order():
    contents = [
        {"role": "user", "parts": [{"text": "first"}]},
        {"role": "model", "parts": [{"text": "second"}]},
        {"role": "user", "parts": [{"text": "third"}]},
    ]
    prompt = claude_code_llm._messages_to_prompt(contents)
    assert prompt == "User:\nfirst\n\nAssistant:\nsecond\n\nUser:\nthird"


def test_message_history_preserves_tool_call_text():
    contents = [{"role": "model", "parts": [{"function_call": {"name": "list_metrics", "args": {"service": "payment-api"}}}]}]
    prompt = claude_code_llm._messages_to_prompt(contents)
    assert "list_metrics" in prompt
    assert "payment-api" in prompt


@pytest.mark.asyncio
async def test_invoke_returns_text_response():
    class FakeTextBlock:
        def __init__(self, text):
            self.text = text

    class FakeAssistantMessage:
        def __init__(self, content):
            self.content = content

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(claude_code_llm, "AssistantMessage", FakeAssistantMessage)
    monkeypatch.setattr(claude_code_llm, "TextBlock", FakeTextBlock)
    monkeypatch.setattr(claude_code_llm, "ClaudeAgentOptions", lambda **kwargs: kwargs)

    async def fake_client(prompt, options):
        yield FakeAssistantMessage([FakeTextBlock("Claude answer")])

    try:
        response = await claude_code_llm.invoke(
            [{"role": "user", "parts": [{"text": "Investigate payment-api"}]}],
            client=fake_client,
        )
    finally:
        monkeypatch.undo()

    assert response.candidates[0].content.parts[0].text == "Claude answer"


@pytest.mark.asyncio
async def test_invoke_converts_claude_tool_use_to_gemini_function_call(monkeypatch):
    class FakeAssistantMessage:
        def __init__(self, content):
            self.content = content

    class FakeToolUseBlock:
        def __init__(self):
            self.id = "toolu_123"
            self.name = "list_metrics"
            self.input = {"service": "payment-api"}

    monkeypatch.setattr(claude_code_llm, "AssistantMessage", FakeAssistantMessage)
    monkeypatch.setattr(claude_code_llm, "ToolUseBlock", FakeToolUseBlock)
    monkeypatch.setattr(claude_code_llm, "TextBlock", type("UnusedText", (), {}))
    monkeypatch.setattr(claude_code_llm, "ClaudeAgentOptions", lambda **kwargs: kwargs)

    async def fake_client(prompt, options):
        yield FakeAssistantMessage([FakeToolUseBlock()])

    response = await claude_code_llm.invoke(
        [{"role": "user", "parts": [{"text": "Find metrics for payment-api"}]}],
        client=fake_client,
    )

    function_call = response.candidates[0].content.parts[0].function_call
    assert function_call.id == "toolu_123"
    assert function_call.name == "list_metrics"
    assert function_call.args == {"service": "payment-api"}


@pytest.mark.asyncio
async def test_timeout_is_mapped(monkeypatch):
    monkeypatch.setattr(claude_code_llm, "ClaudeAgentOptions", lambda **kwargs: kwargs)

    async def slow_client(prompt, options):
        await asyncio.sleep(1)
        yield SimpleNamespace(content=[])

    with pytest.raises(claude_code_llm.ClaudeCodeTimeoutError):
        await claude_code_llm.invoke(
            [{"role": "user", "parts": [{"text": "hello"}]}],
            timeout_seconds=0.01,
            client=slow_client,
        )


@pytest.mark.asyncio
async def test_authentication_error_does_not_expose_secret(monkeypatch):
    monkeypatch.setattr(claude_code_llm, "ClaudeAgentOptions", lambda **kwargs: kwargs)

    async def failing_client(prompt, options):
        raise RuntimeError("credential token=super-secret-123 is invalid")
        yield

    with pytest.raises(claude_code_llm.ClaudeCodeRequestError) as exc_info:
        await claude_code_llm.invoke(
            [{"role": "user", "parts": [{"text": "hello"}]}],
            client=failing_client,
        )
    assert "super-secret-123" not in str(exc_info.value)
    assert "authentication failed" in str(exc_info.value).lower()


def test_options_disable_claude_code_tool_execution(monkeypatch):
    captured = {}

    class FakeOptions:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(claude_code_llm, "ClaudeAgentOptions", FakeOptions)
    claude_code_llm._options()

    assert captured["allowed_tools"] == []
    assert "Bash" in captured["disallowed_tools"]
    assert "Write" in captured["disallowed_tools"]
    assert "Edit" in captured["disallowed_tools"]
    assert captured["max_turns"] == 1

import asyncio
from types import SimpleNamespace

import pytest

from sre_copilot.agent import claude_code_llm


def _tool():
    return SimpleNamespace(
        function_declarations=[
            SimpleNamespace(name="list_metrics", description="List available metrics for a service."),
            SimpleNamespace(name="query_metrics", description="Query a metric for a service."),
        ]
    )


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


def test_options_disable_claude_code_tool_execution(monkeypatch):
    captured = {}

    class FakeOptions:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(claude_code_llm, "ClaudeAgentOptions", FakeOptions)
    claude_code_llm._options(_tool())

    assert captured["allowed_tools"] == []
    assert "Bash" in captured["disallowed_tools"]
    assert "Write" in captured["disallowed_tools"]
    assert "Edit" in captured["disallowed_tools"]
    assert captured["max_turns"] == 1
    assert captured["output_format"]["type"] == "json_schema"
    assert "list_metrics" in captured["system_prompt"]


@pytest.mark.asyncio
async def test_invoke_returns_structured_final_response(monkeypatch):
    class FakeResultMessage:
        def __init__(self, structured_output):
            self.structured_output = structured_output
            self.is_error = False

    monkeypatch.setattr(claude_code_llm, "ResultMessage", FakeResultMessage)
    monkeypatch.setattr(claude_code_llm, "ClaudeAgentOptions", lambda **kwargs: kwargs)

    async def fake_client(prompt, options):
        yield FakeResultMessage({
            "action": "final",
            "tool_name": None,
            "arguments": {},
            "answer": "Claude answer",
        })

    response = await claude_code_llm.invoke_async(
        [{"role": "user", "parts": [{"text": "Investigate payment-api"}]}],
        tool=_tool(),
        client=fake_client,
    )

    assert response.candidates[0].content.parts[0].text == "Claude answer"


@pytest.mark.asyncio
async def test_invoke_converts_structured_tool_decision_to_gemini_function_call(monkeypatch):
    class FakeResultMessage:
        def __init__(self, structured_output):
            self.structured_output = structured_output
            self.is_error = False

    monkeypatch.setattr(claude_code_llm, "ResultMessage", FakeResultMessage)
    monkeypatch.setattr(claude_code_llm, "ClaudeAgentOptions", lambda **kwargs: kwargs)

    async def fake_client(prompt, options):
        yield FakeResultMessage({
            "action": "tool",
            "tool_name": "list_metrics",
            "arguments": {"service": "payment-api"},
            "answer": None,
        })

    response = await claude_code_llm.invoke_async(
        [{"role": "user", "parts": [{"text": "Find metrics for payment-api"}]}],
        tool=_tool(),
        client=fake_client,
    )

    function_call = response.candidates[0].content.parts[0].function_call
    assert function_call.id == "claude_list_metrics"
    assert function_call.name == "list_metrics"
    assert function_call.args == {"service": "payment-api"}


@pytest.mark.asyncio
async def test_unknown_tool_is_rejected(monkeypatch):
    class FakeResultMessage:
        structured_output = {
            "action": "tool",
            "tool_name": "not_a_real_tool",
            "arguments": {},
            "answer": None,
        }
        is_error = False

    monkeypatch.setattr(claude_code_llm, "ResultMessage", FakeResultMessage)
    monkeypatch.setattr(claude_code_llm, "ClaudeAgentOptions", lambda **kwargs: kwargs)

    async def fake_client(prompt, options):
        yield FakeResultMessage()

    with pytest.raises(claude_code_llm.ClaudeCodeRequestError, match="unknown tool"):
        await claude_code_llm.invoke_async(
            [{"role": "user", "parts": [{"text": "hello"}]}],
            tool=_tool(),
            client=fake_client,
        )


@pytest.mark.asyncio
async def test_timeout_is_mapped(monkeypatch):
    monkeypatch.setattr(claude_code_llm, "ClaudeAgentOptions", lambda **kwargs: kwargs)

    async def slow_client(prompt, options):
        await asyncio.sleep(1)
        yield SimpleNamespace(content=[])

    with pytest.raises(claude_code_llm.ClaudeCodeTimeoutError):
        await claude_code_llm.invoke_async(
            [{"role": "user", "parts": [{"text": "hello"}]}],
            tool=_tool(),
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
        await claude_code_llm.invoke_async(
            [{"role": "user", "parts": [{"text": "hello"}]}],
            tool=_tool(),
            client=failing_client,
        )
    assert "super-secret-123" not in str(exc_info.value)
    assert "authentication failed" in str(exc_info.value).lower()

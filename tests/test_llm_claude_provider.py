from types import SimpleNamespace

import pytest

from sre_copilot.agent import llm
from sre_copilot.agent.claude_code_llm import ClaudeCodeRequestError


CONTENTS = [{"role": "user", "parts": [{"text": "Investigate payment-api"}]}]
TOOL = SimpleNamespace(function_declarations=[])


def test_claude_code_provider_can_be_selected(monkeypatch):
    expected = object()
    received = {}
    monkeypatch.setenv("LLM_PROVIDERS", "claude_code")
    monkeypatch.setenv("LLM_STRATEGY", "fallback")

    # Mirrors claude_code_llm.invoke, where ``tool`` is keyword-only and required.
    def fake_invoke(contents, *, tool):
        received["contents"] = contents
        received["tool"] = tool
        return expected

    monkeypatch.setattr(llm, "invoke_claude_code", fake_invoke)

    assert llm.continue_gemini(CONTENTS, TOOL) is expected
    assert received["contents"] is CONTENTS
    assert received["tool"] is TOOL


def test_gemini_provider_still_works(monkeypatch):
    expected = object()
    monkeypatch.setenv("LLM_PROVIDERS", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    client = SimpleNamespace(models=SimpleNamespace(
        generate_content=lambda **kwargs: expected
    ))
    monkeypatch.setattr(llm.genai, "Client", lambda api_key: client)

    assert llm.continue_gemini(CONTENTS, TOOL) is expected


def test_unsupported_provider_fails_clearly(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDERS", "not-a-provider")

    with pytest.raises(llm.ProviderConfigurationError, match="Unsupported LLM provider"):
        llm.continue_gemini(CONTENTS, TOOL)


def test_claude_code_failure_is_not_silently_fallen_back_to_gemini(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDERS", "claude_code,gemini")

    def fake_invoke(contents, *, tool):
        raise RuntimeError("Claude failed")

    monkeypatch.setattr(llm, "invoke_claude_code", fake_invoke)
    monkeypatch.setattr(llm.genai, "Client", lambda **kwargs: pytest.fail("Gemini fallback must be explicit"))

    with pytest.raises(RuntimeError, match="Claude failed"):
        llm.continue_gemini(CONTENTS, TOOL)


# --- transient errors that arrive wrapped ----------------------------------
#
# claude_code_llm re-raises every SDK failure as ClaudeCodeRequestError, which
# carries no status of its own. _status_code therefore follows __cause__, or
# an upstream overload would look permanent and the chain would stop instead
# of reaching the next provider.


def _wrapped(status: int) -> Exception:
    upstream = RuntimeError(f"HTTP {status}")
    upstream.status_code = status
    error = ClaudeCodeRequestError("Claude Code request failed")
    error.__cause__ = upstream
    return error


def test_a_wrapped_overload_falls_through_to_the_file_backup(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDERS", "claude_code,file")
    monkeypatch.delenv("LLM_TRANSPORT", raising=False)

    def fake_invoke(contents, *, tool):
        raise _wrapped(503)

    monkeypatch.setattr(llm, "invoke_claude_code", fake_invoke)
    monkeypatch.setattr(llm, "exchange", lambda issue, prompt: "Answered from a file.")

    response = llm.continue_gemini(CONTENTS, TOOL)

    assert response.candidates[0].content.parts[0].text == "Answered from a file."


def test_a_wrapped_rejection_still_stops_the_chain(monkeypatch):
    """Only 429/503 and timeouts are transient. A 401 must not be retried."""
    monkeypatch.setenv("LLM_PROVIDERS", "claude_code,file")

    def fake_invoke(contents, *, tool):
        raise _wrapped(401)

    monkeypatch.setattr(llm, "invoke_claude_code", fake_invoke)
    monkeypatch.setattr(llm, "exchange", lambda issue, prompt: pytest.fail("must not fall back"))

    with pytest.raises(ClaudeCodeRequestError):
        llm.continue_gemini(CONTENTS, TOOL)


def test_a_looping_cause_chain_does_not_hang():
    first = RuntimeError("first")
    second = RuntimeError("second")
    first.__cause__ = second
    second.__cause__ = first

    assert llm._status_code(first) is None

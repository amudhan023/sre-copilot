from types import SimpleNamespace

import pytest

from sre_copilot.agent import llm


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

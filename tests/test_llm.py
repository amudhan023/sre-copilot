import json
from types import SimpleNamespace

import httpx
import pytest
from google.genai import types

from agent import llm

# Tests the Gemini/Groq provider layer in agent/llm.py: picking a provider
# from env config, falling back from Gemini to Groq on transient failures,
# translating between Gemini's native message/tool-call shapes and Groq's
# OpenAI-compatible ones, and the payload-capping logic that keeps oversized
# tool results from blowing up Groq requests. All HTTP calls are faked so no
# real API calls happen.


CONTENTS = [{"role": "user", "parts": [{"text": "Investigate payment-api"}]}]
TOOL = SimpleNamespace(function_declarations=[SimpleNamespace(
    name="list_metrics",
    description="List metrics",
    parameters={"type": "object", "properties": {}},
)])


def groq_response(content="Groq response"):
    return {"choices": [{"message": {"content": content}}]}


class FakeHttpResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


def transient_error(status_code):
    request = httpx.Request("POST", "https://example.test")
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError("temporary failure", request=request, response=response)


@pytest.fixture(autouse=True)
def provider_environment(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDERS", "gemini,groq")
    monkeypatch.setenv("LLM_STRATEGY", "fallback")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-test-key")
    monkeypatch.setenv("GROQ_API_KEY", "groq-test-key")


def test_gemini_success(monkeypatch):
    expected = object()
    calls = []
    client = SimpleNamespace(models=SimpleNamespace(
        generate_content=lambda **kwargs: calls.append(kwargs) or expected
    ))
    monkeypatch.setattr(llm.genai, "Client", lambda api_key: client)

    assert llm.continue_gemini(CONTENTS, TOOL) is expected
    assert calls[0]["config"].tools == [TOOL]


@pytest.mark.parametrize("status_code", [503, 429])
def test_gemini_transient_status_falls_back_to_groq(monkeypatch, status_code):
    client = SimpleNamespace(models=SimpleNamespace(
        generate_content=lambda **kwargs: (_ for _ in ()).throw(transient_error(status_code))
    ))
    monkeypatch.setattr(llm.genai, "Client", lambda api_key: client)
    monkeypatch.setattr(llm.httpx, "post", lambda *args, **kwargs: FakeHttpResponse(groq_response()))

    response = llm.continue_gemini(CONTENTS, TOOL)

    assert response.candidates[0].content.parts[0].text == "Groq response"


def test_gemini_timeout_falls_back_to_groq(monkeypatch):
    client = SimpleNamespace(models=SimpleNamespace(
        generate_content=lambda **kwargs: (_ for _ in ()).throw(httpx.TimeoutException("timeout"))
    ))
    monkeypatch.setattr(llm.genai, "Client", lambda api_key: client)
    monkeypatch.setattr(llm.httpx, "post", lambda *args, **kwargs: FakeHttpResponse(groq_response()))

    assert llm.continue_gemini(CONTENTS, TOOL).candidates[0].content.parts[0].text == "Groq response"


def test_both_providers_failing_raises_availability_error(monkeypatch):
    client = SimpleNamespace(models=SimpleNamespace(
        generate_content=lambda **kwargs: (_ for _ in ()).throw(transient_error(503))
    ))
    monkeypatch.setattr(llm.genai, "Client", lambda api_key: client)
    monkeypatch.setattr(llm.httpx, "post", lambda *args, **kwargs: (_ for _ in ()).throw(httpx.TimeoutException("timeout")))

    with pytest.raises(llm.ProvidersUnavailableError):
        llm.continue_gemini(CONTENTS, TOOL)


def test_random_strategy_selects_one_provider(monkeypatch):
    monkeypatch.setenv("LLM_STRATEGY", "random")
    monkeypatch.setattr(llm.random, "choice", lambda providers: "groq")
    monkeypatch.setattr(llm.httpx, "post", lambda *args, **kwargs: FakeHttpResponse(groq_response()))
    gemini_client = SimpleNamespace(models=SimpleNamespace(
        generate_content=lambda **kwargs: pytest.fail("Gemini must not be called")
    ))
    monkeypatch.setattr(llm.genai, "Client", lambda api_key: gemini_client)

    assert llm.continue_gemini(CONTENTS, TOOL).candidates[0].content.parts[0].text == "Groq response"


def test_gemini_tool_schema_converts_to_groq_json_schema():
    tool = types.Tool(function_declarations=[types.FunctionDeclaration(
        name="list_metrics",
        parameters={"type": "OBJECT", "properties": {"service": {"type": "STRING"}}},
    )])

    result = llm._groq_tools(tool)

    assert result[0]["function"]["parameters"]["type"] == "object"
    assert result[0]["function"]["parameters"]["properties"]["service"]["type"] == "string"


def test_groq_receives_openai_compatible_tools(monkeypatch):
    request = {}
    tool = types.Tool(function_declarations=[types.FunctionDeclaration(
        name="list_metrics",
        description="List metrics",
        parameters={"type": "OBJECT", "properties": {}},
    )])

    def post(url, **kwargs):
        request["url"] = url
        request.update(kwargs)
        return FakeHttpResponse(groq_response())

    monkeypatch.setattr(llm.httpx, "post", post)
    llm._call_groq(CONTENTS, tool)

    assert request["json"]["tools"] == [{
        "type": "function",
        "function": {
            "name": "list_metrics",
            "description": "List metrics",
            "parameters": {"type": "object", "properties": {}},
        },
    }]


def test_groq_tool_call_is_normalized_for_langgraph():
    response = llm._groq_response({"choices": [{"message": {
        "content": None,
        "tool_calls": [{"function": {
            "name": "list_metrics",
            "arguments": '{"service": "payment-api"}',
        }}],
    }}]})

    function_call = response.candidates[0].content.parts[0].function_call
    assert function_call.name == "list_metrics"
    assert function_call.args == {"service": "payment-api"}


def test_missing_selected_provider_key_is_configuration_error(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDERS", "gemini")
    monkeypatch.delenv("GEMINI_API_KEY")

    with pytest.raises(llm.ProviderConfigurationError, match="GEMINI_API_KEY"):
        llm.continue_gemini(CONTENTS, TOOL)


class FakeHttp413Response:
    def __init__(self):
        request = httpx.Request("POST", llm.GROQ_CHAT_COMPLETIONS_URL)
        self._response = httpx.Response(413, request=request)

    def raise_for_status(self):
        raise httpx.HTTPStatusError("payload too large", request=self._response.request, response=self._response)

    def json(self):
        pytest.fail("json() must not be read on a 413 response")


def test_groq_413_is_a_request_error_and_does_not_fall_back(monkeypatch):
    gemini_calls = []
    groq_calls = []
    client = SimpleNamespace(models=SimpleNamespace(
        generate_content=lambda **kwargs: gemini_calls.append(1) or (_ for _ in ()).throw(transient_error(503))
    ))
    monkeypatch.setattr(llm.genai, "Client", lambda api_key: client)

    def post(*args, **kwargs):
        groq_calls.append(1)
        return FakeHttp413Response()

    monkeypatch.setattr(llm.httpx, "post", post)

    with pytest.raises(llm.ProviderRequestError, match="413"):
        llm.continue_gemini(CONTENTS, TOOL)

    assert len(gemini_calls) == 1
    assert len(groq_calls) == 1


def test_groq_message_history_round_trips_tool_calls():
    contents = [
        {"role": "user", "parts": [{"text": "Investigate payment-api CPU usage"}]},
        {"role": "model", "parts": [{"function_call": {"id": "call_1", "name": "list_metrics", "args": {"service": "payment-api"}}}]},
        {"role": "user", "parts": [{"text": "Result from list_metrics:\ncpu_usage, memory_usage"}]},
        {"role": "model", "parts": [{"text": "Findings: high CPU usage observed."}]},
    ]

    messages = llm._groq_messages(contents)

    assert messages[0] == {"role": "system", "content": llm.SYSTEM_INSTRUCTION}
    assert messages[1] == {"role": "user", "content": "Investigate payment-api CPU usage"}

    assistant_message = messages[2]
    assert assistant_message["role"] == "assistant"
    assert assistant_message["tool_calls"] == [{
        "id": "call_1",
        "type": "function",
        "function": {"name": "list_metrics", "arguments": '{"service": "payment-api"}'},
    }]

    tool_message = messages[3]
    assert tool_message == {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": "cpu_usage, memory_usage",
    }

    assert messages[4] == {"role": "assistant", "content": "Findings: high CPU usage observed."}

    # No duplicate representation of the same tool result anywhere else in the history.
    serialized = json.dumps(messages)
    assert serialized.count("cpu_usage, memory_usage") == 1


def test_cap_groq_tool_history_bounds_total_size_and_favors_recent_results():
    messages = [{"role": "system", "content": llm.SYSTEM_INSTRUCTION}]
    for index in range(6):
        messages.append({
            "role": "tool",
            "tool_call_id": f"call_{index}",
            "content": "X" * 20_000,
        })

    capped = llm._cap_groq_tool_history(messages)
    tool_messages = [message for message in capped if message["role"] == "tool"]

    total_tool_chars = sum(len(message["content"]) for message in tool_messages)
    assert total_tool_chars <= llm.GROQ_MAX_HISTORY_TOOL_CHARS + len(tool_messages)

    # The most recent tool result (last in the list) must not be reduced to near-nothing
    # while older history absorbs the cut, since it is still part of the live investigation.
    assert len(tool_messages[-1]["content"]) == llm.GROQ_MAX_TOOL_RESULT_CHARS
    assert len(tool_messages[0]["content"]) < len(tool_messages[-1]["content"])


def test_logs_do_not_contain_api_keys(monkeypatch, caplog):
    caplog.set_level("INFO", logger=llm.__name__)
    client = SimpleNamespace(models=SimpleNamespace(
        generate_content=lambda **kwargs: (_ for _ in ()).throw(transient_error(503))
    ))
    monkeypatch.setattr(llm.genai, "Client", lambda api_key: client)
    monkeypatch.setattr(llm.httpx, "post", lambda *args, **kwargs: FakeHttpResponse(groq_response()))

    llm.continue_gemini(CONTENTS, TOOL)

    assert "gemini-test-key" not in caplog.text
    assert "groq-test-key" not in caplog.text
    assert "LLM provider: gemini" in caplog.text
    assert "falling back to groq" in caplog.text


def test_system_instruction_forbids_treating_the_alert_claim_as_observed():
    # Regression for a real RCA where the model stated "payment-api experienced
    # high CPU usage" as a Finding even though no CPU metric was ever returned
    # by a tool. The instruction must force the model to (a) separate the
    # alert's own claim from what telemetry actually showed, and (b) call out
    # a named symptom as unconfirmed when no tool returned a matching metric.
    instruction = llm.SYSTEM_INSTRUCTION

    assert "Alert claim" in instruction
    assert "Observed telemetry" in instruction
    assert "Inference" in instruction

    assert "is a claim, not evidence" in instruction
    assert "unconfirmed by telemetry" in instruction

    # Temporal correlation (e.g. "deployment happened first") must not be
    # allowed to stand in for proof of causation or for High confidence.
    assert "not proof" in instruction or "not proof of causation" in instruction
    assert "never sufficient for \"High\"" in instruction or "never sufficient for" in instruction

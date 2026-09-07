import json
import threading
import time
from types import SimpleNamespace

import pytest

from sre_copilot import llm_files
from sre_copilot.agent import llm

# Tests the file-backed LLM transport in sre_copilot/llm_files.py: how a
# caller label becomes a file name, what a request file says, how the poll
# loop decides a response is complete, and what happens when nobody answers.
# The last few tests cover the "file" provider that plugs the transport into
# sre_copilot/agent/llm.py. Nothing here touches the network.


@pytest.fixture
def calls_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_CALLS_DIR", str(tmp_path))
    monkeypatch.setenv("LLM_FILE_POLL_INTERVAL", "0.01")
    monkeypatch.setenv("LLM_FILE_TIMEOUT", "5")
    return tmp_path


def answer_when_asked(directory, text, *, delay=0.0):
    """Watch the request directory and write the reply the request asks for."""

    def responder():
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            for request in sorted((directory / "request").glob("*.request.*.txt")):
                reply = directory / "response" / request.name.replace(
                    ".request.", ".response."
                )
                if reply.exists():
                    continue
                time.sleep(delay)
                reply.write_text(text, encoding="utf-8")
                return
            time.sleep(0.01)

    thread = threading.Thread(target=responder, daemon=True)
    thread.start()
    return thread


@pytest.mark.parametrize(
    "issue, expected",
    [
        ("INC-1042", "INC-1042"),
        ("payment-root-cause-001 factual_correctness", "payment-root-cause-001-factual_correctness"),
        ("INC-1042/ctx precision", "INC-1042-ctx-precision"),
        ("a.b.c", "a-b-c"),
        ("", "llm"),
        ("///", "llm"),
        ("x" * 200, "x" * 60),
    ],
)
def test_issue_slug_produces_safe_file_names(issue, expected):
    assert llm_files.issue_slug(issue) == expected


def test_transport_is_off_unless_explicitly_enabled(monkeypatch):
    monkeypatch.delenv("LLM_TRANSPORT", raising=False)
    assert llm_files.transport_is_enabled() is False
    monkeypatch.setenv("LLM_TRANSPORT", "api")
    assert llm_files.transport_is_enabled() is False
    monkeypatch.setenv("LLM_TRANSPORT", " File ")
    assert llm_files.transport_is_enabled() is True


def test_exchange_writes_the_prompt_and_reads_the_reply(calls_dir):
    answer_when_asked(calls_dir, "  the pool was exhausted  ")

    reply = llm_files.exchange("INC-1042", "Why did payment-api fail?")

    assert reply == "the pool was exhausted"
    written = sorted((calls_dir / "request").glob("INC-1042.request.*.txt"))
    assert len(written) == 1
    body = written[0].read_text(encoding="utf-8")
    assert "Why did payment-api fail?" in body
    # The request has to name the exact file to create, otherwise whoever
    # answers has to reverse-engineer the naming convention.
    stem = written[0].name.replace(".request.", ".response.")
    assert str(calls_dir / "response" / stem) in body


def test_request_file_carries_the_schema_when_one_is_required(calls_dir):
    answer_when_asked(calls_dir, '{"verdict": 1}')
    schema = {"type": "object", "properties": {"verdict": {"type": "integer"}}}

    llm_files.exchange("ragas-Verdict", "Judge this claim", schema=schema)

    body = sorted((calls_dir / "request").glob("*.txt"))[0].read_text(encoding="utf-8")
    assert "REQUIRED JSON RESPONSE SCHEMA" in body
    assert '"verdict"' in body


def test_two_calls_never_share_a_response_file(calls_dir):
    answer_when_asked(calls_dir, "first")
    llm_files.exchange("INC-1042", "one")
    answer_when_asked(calls_dir, "second")
    llm_files.exchange("INC-1042", "two")

    requests = sorted((calls_dir / "request").glob("*.txt"))
    assert len(requests) == 2
    assert requests[0].name != requests[1].name


def test_an_empty_response_file_is_not_treated_as_an_answer(calls_dir):
    # Someone creating the file before typing into it must not be read as an
    # empty model reply; the poll loop has to keep waiting.
    def responder():
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            requests = sorted((calls_dir / "request").glob("*.request.*.txt"))
            if requests:
                target = calls_dir / "response" / requests[0].name.replace(
                    ".request.", ".response."
                )
                target.write_text("", encoding="utf-8")
                time.sleep(0.2)
                target.write_text("the real answer", encoding="utf-8")
                return
            time.sleep(0.01)

    threading.Thread(target=responder, daemon=True).start()

    assert llm_files.exchange("INC-1042", "prompt") == "the real answer"


def test_a_response_with_any_extension_is_accepted(calls_dir):
    def responder():
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            requests = sorted((calls_dir / "request").glob("*.request.*.txt"))
            if requests:
                name = requests[0].name.replace(".request.", ".response.")
                (calls_dir / "response" / name.replace(".txt", ".md")).write_text(
                    "answered in markdown", encoding="utf-8"
                )
                return
            time.sleep(0.01)

    threading.Thread(target=responder, daemon=True).start()

    assert llm_files.exchange("INC-1042", "prompt") == "answered in markdown"


def test_exchange_times_out_when_nobody_answers(calls_dir, monkeypatch):
    monkeypatch.setenv("LLM_FILE_TIMEOUT", "0.05")

    with pytest.raises(llm_files.FileTransportTimeout) as excinfo:
        llm_files.exchange("INC-1042", "prompt")

    # The message has to say which file to create, or the run is unrecoverable.
    assert "INC-1042.response." in str(excinfo.value)


@pytest.mark.parametrize("value", ["not-a-number", "0", "-3"])
def test_bad_timeout_configuration_fails_loudly(calls_dir, monkeypatch, value):
    monkeypatch.setenv("LLM_FILE_TIMEOUT", value)
    with pytest.raises(ValueError):
        llm_files.exchange("INC-1042", "prompt")


@pytest.mark.parametrize(
    "text",
    [
        '{"score": 1}',
        '```json\n{"score": 1}\n```',
        'Sure, here you go:\n{"score": 1}\nHope that helps.',
    ],
)
def test_extract_json_tolerates_how_people_actually_write_replies(text):
    assert llm_files.extract_json(text) == {"score": 1}


def test_extract_json_rejects_text_with_no_json():
    with pytest.raises(ValueError):
        llm_files.extract_json("the pool was exhausted")


# --- the "file" provider in agent/llm.py -----------------------------------

CONTENTS = [{"role": "user", "parts": [{"text": "Investigate payment-api"}]}]
TOOL = SimpleNamespace(function_declarations=[SimpleNamespace(
    name="list_metrics",
    description="List metrics",
    parameters={"type": "object", "properties": {}},
)])


def test_file_transport_overrides_the_configured_providers(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDERS", "gemini,groq")
    monkeypatch.setenv("LLM_TRANSPORT", "file")
    assert llm._provider_names() == ["file"]


def test_file_provider_can_also_be_configured_as_a_fallback(monkeypatch):
    monkeypatch.delenv("LLM_TRANSPORT", raising=False)
    monkeypatch.setenv("LLM_PROVIDERS", "gemini,groq,file")
    assert llm._provider_names() == ["gemini", "groq", "file"]


def test_prose_reply_becomes_the_assistant_text(monkeypatch):
    captured = {}

    def fake_exchange(issue, prompt):
        captured["issue"] = issue
        captured["prompt"] = prompt
        return "The connection pool was exhausted."

    monkeypatch.setattr(llm, "exchange", fake_exchange)

    response = llm._call_file(CONTENTS, TOOL, "INC-1042")

    assert response.candidates[0].content.parts[0].text == "The connection pool was exhausted."
    assert captured["issue"] == "INC-1042"
    # The file must show the tools, or whoever answers cannot call one.
    assert "list_metrics" in captured["prompt"]
    assert "Investigate payment-api" in captured["prompt"]


def test_json_reply_becomes_a_tool_call(monkeypatch):
    reply = json.dumps({
        "content": None,
        "tool_calls": [{
            "id": "call_1",
            "type": "function",
            "function": {"name": "list_metrics", "arguments": {"service": "payment-api"}},
        }],
    })
    monkeypatch.setattr(llm, "exchange", lambda issue, prompt: reply)

    response = llm._call_file(CONTENTS, TOOL, "INC-1042")

    call = response.candidates[0].content.parts[0].function_call
    assert call.name == "list_metrics"
    assert call.args == {"service": "payment-api"}


def test_json_reply_with_encoded_arguments_is_accepted(monkeypatch):
    reply = json.dumps({
        "tool_calls": [{
            "id": "call_1",
            "type": "function",
            "function": {"name": "list_metrics", "arguments": '{"service": "payment-api"}'},
        }],
    })
    monkeypatch.setattr(llm, "exchange", lambda issue, prompt: reply)

    response = llm._call_file(CONTENTS, TOOL, "INC-1042")

    assert response.candidates[0].content.parts[0].function_call.args == {
        "service": "payment-api"
    }

import asyncio
import json

import pytest
from pydantic import BaseModel

# Importing file_llm first applies the Ragas/Vertex AI import shim; importing
# anything from ragas before it raises ModuleNotFoundError.
from sre_copilot.rag.evaluation import file_llm
from ragas.llms.base import InstructorBaseRagasLLM

# Tests the Ragas evaluator LLM that reads its answers from files. Ragas asks
# for structured output, so the interesting behaviour is schema validation and
# what happens when the answer on disk does not match: the wrapper has to ask
# again with the error attached rather than crashing the whole run.


class Verdict(BaseModel):
    verdict: int
    reason: str


@pytest.fixture(autouse=True)
def reset_label():
    token = file_llm.current_label.set("ragas")
    yield
    file_llm.current_label.reset(token)


def test_ragas_accepts_it_as_an_evaluator_llm():
    # ragas/metrics/collections/base.py does an isinstance check, so duck
    # typing this class would fail at metric construction time.
    assert isinstance(file_llm.FileInstructorLLM(), InstructorBaseRagasLLM)


def test_agenerate_validates_the_reply_against_the_model(monkeypatch):
    async def fake(issue, prompt, schema=None):
        assert schema["title"] == "Verdict"
        return '```json\n{"verdict": 1, "reason": "supported"}\n```'

    monkeypatch.setattr(file_llm, "aexchange", fake)

    result = asyncio.run(file_llm.FileInstructorLLM().agenerate("judge this", Verdict))

    assert result == Verdict(verdict=1, reason="supported")


def test_request_files_are_labelled_with_the_case_and_metric(monkeypatch):
    seen = {}

    async def fake(issue, prompt, schema=None):
        seen["issue"] = issue
        return '{"verdict": 0, "reason": "no"}'

    monkeypatch.setattr(file_llm, "aexchange", fake)
    file_llm.current_label.set("payment-root-cause-001-factual_correctness")

    asyncio.run(file_llm.FileInstructorLLM().agenerate("judge this", Verdict))

    assert seen["issue"] == "payment-root-cause-001-factual_correctness-Verdict"


def test_a_reply_that_misses_the_schema_is_asked_again(monkeypatch):
    prompts = []
    replies = iter(['{"verdict": "yes"}', '{"verdict": 1, "reason": "supported"}'])

    async def fake(issue, prompt, schema=None):
        prompts.append(prompt)
        return next(replies)

    monkeypatch.setattr(file_llm, "aexchange", fake)

    result = asyncio.run(file_llm.FileInstructorLLM().agenerate("judge this", Verdict))

    assert result.verdict == 1
    assert len(prompts) == 2
    # The second request has to explain what was wrong, otherwise whoever is
    # answering just makes the same mistake again.
    assert "YOUR PREVIOUS ANSWER WAS REJECTED" in prompts[1]
    assert '{"verdict": "yes"}' in prompts[1]


def test_giving_up_after_the_configured_number_of_attempts(monkeypatch):
    monkeypatch.setenv("LLM_FILE_VALIDATION_ATTEMPTS", "2")
    calls = []

    async def fake(issue, prompt, schema=None):
        calls.append(prompt)
        return "not json at all"

    monkeypatch.setattr(file_llm, "aexchange", fake)

    with pytest.raises(ValueError, match="never matched Verdict"):
        asyncio.run(file_llm.FileInstructorLLM().agenerate("judge this", Verdict))

    assert len(calls) == 2


def test_generate_works_synchronously_too(monkeypatch):
    monkeypatch.setattr(
        file_llm,
        "exchange",
        lambda issue, prompt, schema=None: json.dumps({"verdict": 1, "reason": "ok"}),
    )

    assert file_llm.FileInstructorLLM().generate("judge this", Verdict).verdict == 1


@pytest.mark.parametrize("value", ["0", "-1", "many"])
def test_bad_attempt_configuration_fails_loudly(monkeypatch, value):
    monkeypatch.setenv("LLM_FILE_VALIDATION_ATTEMPTS", value)
    with pytest.raises(ValueError):
        file_llm._attempts()

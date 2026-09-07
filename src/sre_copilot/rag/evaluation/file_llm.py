"""A Ragas evaluator LLM that is answered from files instead of an API.

Ragas metrics ask their LLM for *structured* output: each call passes a
Pydantic model and expects an instance back. This wrapper writes the metric's
prompt plus that model's JSON schema into ``llm_calls/request/`` and validates
whatever comes back in ``llm_calls/response/`` against the same model.

Ragas type-checks the evaluator (``ragas/metrics/collections/base.py``
rejects anything that is not an ``InstructorBaseRagasLLM``), so this subclasses
that base rather than duck-typing it.
"""

from __future__ import annotations

import contextvars
import json
import os
from typing import Type, TypeVar

from .ragas_compat import patch_ragas_vertexai_imports

# Ragas pulls Vertex AI classes from legacy langchain-community paths, so the
# shim has to be in place before anything from Ragas is imported.
patch_ragas_vertexai_imports()

from pydantic import BaseModel, ValidationError
from ragas.llms.base import InstructorBaseRagasLLM

from sre_copilot.llm_files import aexchange, exchange, extract_json

ModelT = TypeVar("ModelT", bound=BaseModel)

# Ragas gives its LLM a bare prompt with no idea which case or metric it
# belongs to. The runner sets this around each metric call so the request file
# names are readable instead of a wall of identical timestamps.
current_label: contextvars.ContextVar[str] = contextvars.ContextVar(
    "ragas_file_llm_label", default="ragas"
)

DEFAULT_VALIDATION_ATTEMPTS = 3


def _attempts() -> int:
    raw = os.getenv("LLM_FILE_VALIDATION_ATTEMPTS", str(DEFAULT_VALIDATION_ATTEMPTS))
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(
            f"LLM_FILE_VALIDATION_ATTEMPTS must be an integer, got {raw!r}"
        ) from None
    if value < 1:
        raise ValueError("LLM_FILE_VALIDATION_ATTEMPTS must be at least 1")
    return value


class FileInstructorLLM(InstructorBaseRagasLLM):
    """Answer Ragas metric prompts from ``llm_calls/`` instead of a provider."""

    def __init__(self, model: str = "file-transport", provider: str = "file") -> None:
        self.model = model
        self.provider = provider
        # Ragas inspects this to decide whether agenerate() is usable.
        self.is_async = True

    def _issue(self, response_model: Type[BaseModel]) -> str:
        return f"{current_label.get()}-{response_model.__name__}"

    @staticmethod
    def _retry_note(error: Exception, previous: str) -> str:
        return (
            "\n\n===== YOUR PREVIOUS ANSWER WAS REJECTED =====\n"
            f"{previous}\n\n"
            "It did not match the required schema. The error was:\n"
            f"{error}\n\n"
            "Answer again with a JSON object that satisfies the schema."
        )

    def _validate(self, raw: str, response_model: Type[ModelT]) -> ModelT:
        payload = extract_json(raw)
        return response_model.model_validate(payload)

    def generate(self, prompt: str, response_model: Type[ModelT]) -> ModelT:
        schema = response_model.model_json_schema()
        issue = self._issue(response_model)
        note = ""
        last_error: Exception | None = None

        for _ in range(_attempts()):
            raw = exchange(issue, f"{prompt}{note}", schema=schema)
            try:
                return self._validate(raw, response_model)
            except (ValidationError, ValueError, json.JSONDecodeError) as error:
                last_error = error
                note = self._retry_note(error, raw)

        raise ValueError(
            f"File response for {issue} never matched {response_model.__name__}: {last_error}"
        ) from last_error

    async def agenerate(self, prompt: str, response_model: Type[ModelT]) -> ModelT:
        schema = response_model.model_json_schema()
        issue = self._issue(response_model)
        note = ""
        last_error: Exception | None = None

        for _ in range(_attempts()):
            raw = await aexchange(
                issue, f"{prompt}{note}", schema=schema
            )
            try:
                return self._validate(raw, response_model)
            except (ValidationError, ValueError, json.JSONDecodeError) as error:
                last_error = error
                note = self._retry_note(error, raw)

        raise ValueError(
            f"File response for {issue} never matched {response_model.__name__}: {last_error}"
        ) from last_error

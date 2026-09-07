"""Load and validate the curated RAG evaluation dataset."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .ragas_compat import patch_ragas_vertexai_imports

patch_ragas_vertexai_imports()

from ragas import EvaluationDataset, SingleTurnSample


REQUIRED_FIELDS = {
    "id",
    "question",
    "reference_answer",
    "relevant_document_ids",
}


@dataclass(frozen=True)
class EvaluationCase:
    """One curated question and its human-authored reference answer."""

    case_id: str
    question: str
    reference_answer: str
    relevant_document_ids: tuple[str, ...]


def load_cases(path: Path) -> list[EvaluationCase]:
    """Load evaluation cases from JSON and reject malformed cases early."""
    with path.open(encoding="utf-8") as file:
        raw_cases = json.load(file)

    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("Evaluation dataset must be a non-empty JSON array")

    cases: list[EvaluationCase] = []
    seen_ids: set[str] = set()

    for raw in raw_cases:
        if not isinstance(raw, dict):
            raise ValueError("Each evaluation case must be a JSON object")

        missing = REQUIRED_FIELDS - raw.keys()
        if missing:
            raise ValueError(
                f"Evaluation case is missing fields: {', '.join(sorted(missing))}"
            )

        case_id = str(raw["id"]).strip()
        question = str(raw["question"]).strip()
        reference_answer = str(raw["reference_answer"]).strip()
        relevant_ids = tuple(str(value).strip() for value in raw["relevant_document_ids"])

        if not case_id or not question or not reference_answer or not relevant_ids:
            raise ValueError(f"Evaluation case {case_id!r} contains an empty field")
        if case_id in seen_ids:
            raise ValueError(f"Duplicate evaluation case id: {case_id}")
        if any(not value for value in relevant_ids):
            raise ValueError(f"Evaluation case {case_id} has an empty document id")

        seen_ids.add(case_id)
        cases.append(
            EvaluationCase(
                case_id=case_id,
                question=question,
                reference_answer=reference_answer,
                relevant_document_ids=relevant_ids,
            )
        )

    return cases


def build_ragas_dataset(cases: list[EvaluationCase], results: list[dict]) -> EvaluationDataset:
    """Build the Ragas v0.4 EvaluationDataset from executed RAG results."""
    if len(cases) != len(results):
        raise ValueError("cases and results must have the same length")

    samples = []
    for case, result in zip(cases, results):
        samples.append(
            SingleTurnSample(
                user_input=case.question,
                retrieved_contexts=result["contexts"],
                response=result["response"],
                reference=case.reference_answer,
            )
        )

    return EvaluationDataset(samples=samples)

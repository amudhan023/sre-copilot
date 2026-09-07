"""Guard the invariants that make the curated RAG dataset scorable by Ragas."""

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DATASET = REPO_ROOT / "data" / "rag_eval.json"
INCIDENTS = REPO_ROOT / "data" / "incidents.json"


@pytest.fixture(scope="module")
def cases():
    with DATASET.open(encoding="utf-8") as file:
        return json.load(file)


@pytest.fixture(scope="module")
def incidents():
    with INCIDENTS.open(encoding="utf-8") as file:
        return {incident["incident_id"]: incident for incident in json.load(file)}


def test_relevant_documents_exist_in_the_corpus(cases, incidents):
    for case in cases:
        for document_id in case["relevant_document_ids"]:
            assert document_id in incidents, (
                f"{case['id']} points at unknown document {document_id}"
            )


def test_reference_answer_names_every_relevant_incident(cases):
    """Ragas FactualCorrectness verifies response claims against the reference only.

    The generated answers identify the incident they describe, so a reference
    that never names that incident turns a correct claim into a false positive
    and drives the score to zero. Keep the identifier in the reference.
    """
    for case in cases:
        for document_id in case["relevant_document_ids"]:
            assert document_id in case["reference_answer"], (
                f"{case['id']} reference answer does not name {document_id}"
            )


def test_reference_answer_does_not_pull_in_unrelated_incidents(cases, incidents):
    """A single-incident question must not carry facts from the other incident.

    Extra incidents in the reference become unmatched reference claims, which
    Ragas counts as false negatives against a correctly scoped answer.
    """
    for case in cases:
        relevant = set(case["relevant_document_ids"])
        unrelated = set(incidents) - relevant
        for document_id in unrelated:
            assert document_id not in case["reference_answer"], (
                f"{case['id']} reference answer mentions unrelated {document_id}"
            )

"""End-to-end RAGAS evaluation runner for the SRE Copilot RAG pipeline.

The runner deliberately evaluates the real application path:

    evaluation question
        -> BGE-M3 + PostgreSQL dense/FTS retrieval
        -> RRF
        -> BGE cross-encoder reranking
        -> top-K contexts
        -> answer generation
        -> Ragas evaluation

Ragas is used for answer/context quality. Retrieval ranking metrics are
calculated separately because Ragas does not replace Recall@K, Precision@K,
MRR, or NDCG.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from google import genai
from google.genai import types

from sre_copilot.rag.evaluation.ragas_compat import patch_ragas_vertexai_imports

# Ragas 0.4.3 imports Vertex AI classes from legacy langchain-community paths.
# Installations that import this runner directly must receive the compatibility
# patch before importing anything from Ragas.
patch_ragas_vertexai_imports()

from ragas import EvaluationDataset
from ragas.llms import llm_factory
from ragas.metrics.collections import (
    AnswerRelevancy,
    ContextPrecision,
    ContextRecall,
    Faithfulness,
    FactualCorrectness,
)

from sre_copilot.rag.evaluation.dataset import (
    EvaluationCase,
    build_ragas_dataset,
    load_cases,
)
from sre_copilot.rag.evaluation.metrics import retrieval_metrics
from sre_copilot.rag.retriever import HybridRetriever

load_dotenv()

REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_DATASET = REPO_ROOT / "data" / "rag_eval.json"
DEFAULT_TENANT = os.getenv("RAG_EVAL_TENANT", "default")
DEFAULT_TOP_K = int(os.getenv("RAG_EVAL_TOP_K", "5"))

ANSWER_SYSTEM_PROMPT = """You are answering questions for an SRE knowledge-base evaluation.

Use only the supplied retrieved context. Do not add facts from general knowledge.
If the context does not contain enough evidence, say that the available context
is insufficient rather than guessing.

Give a concise, technically precise answer. Distinguish historical incident
facts from inference when the question asks for a cause or resolution.
"""


def _gemini_client() -> genai.Client:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is required for RAG evaluation")
    return genai.Client(api_key=api_key)


def generate_answer(
    client: genai.Client,
    question: str,
    contexts: list[str],
) -> str:
    """Generate an answer using the same Gemini configuration as the project."""
    context_text = "\n\n--- Retrieved context ---\n\n".join(contexts)
    prompt = (
        f"Question:\n{question}\n\n"
        f"Retrieved context:\n{context_text}\n\n"
        "Answer the question using only this retrieved context."
    )

    response = client.models.generate_content(
        model=os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite"),
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=ANSWER_SYSTEM_PROMPT,
            temperature=0.0,
        ),
    )
    answer = (response.text or "").strip()
    if not answer:
        raise RuntimeError("Gemini returned an empty evaluation answer")
    return answer


def run_retrieval(
    retriever: HybridRetriever,
    cases: list[EvaluationCase],
    tenant: str,
    top_k: int,
) -> list[dict[str, Any]]:
    """Execute the production retrieval pipeline for every evaluation case."""
    results = []
    for case in cases:
        retrieval = retriever.retrieve(
            case.question,
            tenant=tenant,
            dense_limit=20,
            sparse_limit=20,
            rrf_limit=20,
            top_k=top_k,
        )
        final_results = retrieval["results"]
        contexts = [result["content"] for result in final_results]
        document_ids = [result["document_id"] for result in final_results]

        results.append(
            {
                "case_id": case.case_id,
                "question": case.question,
                "reference_answer": case.reference_answer,
                "contexts": contexts,
                "document_ids": document_ids,
                "retrieval_metrics": retrieval_metrics(
                    document_ids,
                    case.relevant_document_ids,
                    k=top_k,
                ),
                "retrieval": retrieval,
            }
        )
    return results


def _metric_score(result: Any) -> float:
    """Normalize a Ragas MetricResult to a JSON-friendly float."""
    value = getattr(result, "value", result)
    return float(value)


async def evaluate_with_ragas(
    cases: list[EvaluationCase],
    results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Run the current Ragas v0.4 collections metrics over every case."""
    if not results:
        return []

    client = _gemini_client()
    model = os.getenv("RAGAS_MODEL", os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite"))
    evaluator_llm = llm_factory(
        model,
        provider="google",
        client=client,
        temperature=0.0,
    )

    metrics = {
        "context_precision": ContextPrecision(llm=evaluator_llm),
        "context_recall": ContextRecall(llm=evaluator_llm),
        "faithfulness": Faithfulness(llm=evaluator_llm),
        "answer_relevancy": AnswerRelevancy(llm=evaluator_llm),
        "factual_correctness": FactualCorrectness(llm=evaluator_llm),
    }

    # Construct the official Ragas dataset schema before scoring. This also
    # catches malformed sample data before any evaluator LLM calls are made.
    ragas_dataset: EvaluationDataset = build_ragas_dataset(cases, results)
    if len(ragas_dataset.samples) != len(cases):
        raise RuntimeError("Ragas dataset size does not match evaluation cases")

    scored = []
    for case, result in zip(cases, results):
        kwargs = {
            "user_input": case.question,
            "response": result["response"],
            "retrieved_contexts": result["contexts"],
            "reference": case.reference_answer,
        }

        case_scores = {}
        reasons = {}

        for name, metric in metrics.items():
            metric_result = await metric.ascore(**kwargs)
            case_scores[name] = _metric_score(metric_result)
            reason = getattr(metric_result, "reason", None)
            if reason:
                reasons[name] = str(reason)

        scored.append(
            {
                "case_id": case.case_id,
                "question": case.question,
                "response": result["response"],
                "scores": case_scores,
                "reasons": reasons,
            }
        )

    return scored


def _average(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def summarize(
    results: list[dict[str, Any]],
    ragas_results: list[dict[str, Any]],
) -> dict[str, Any]:
    retrieval_keys = list(results[0]["retrieval_metrics"].keys()) if results else []
    retrieval_summary = {
        key: _average([result["retrieval_metrics"][key] for result in results])
        for key in retrieval_keys
    }

    ragas_keys = list(ragas_results[0]["scores"].keys()) if ragas_results else []
    ragas_summary = {
        key: _average([result["scores"][key] for result in ragas_results])
        for key in ragas_keys
    }

    return {
        "num_cases": len(results),
        "retrieval": retrieval_summary,
        "ragas": ragas_summary,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate the SRE Copilot RAG pipeline with Ragas")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--tenant", default=DEFAULT_TENANT)
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    return parser.parse_args()


async def async_main(args: argparse.Namespace) -> None:
    if args.top_k <= 0:
        raise ValueError("--top-k must be greater than zero")

    cases = load_cases(args.dataset)
    print(f"Loaded {len(cases)} evaluation cases from {args.dataset}")
    print(f"Tenant: {args.tenant}")
    print(f"Top-K: {args.top_k}")

    retriever = HybridRetriever()
    results = run_retrieval(retriever, cases, args.tenant, args.top_k)

    client = _gemini_client()
    for result in results:
        result["response"] = generate_answer(
            client,
            result["question"],
            result["contexts"],
        )

    ragas_results = await evaluate_with_ragas(cases, results)
    summary = summarize(results, ragas_results)

    print("\n=== Per-case evaluation ===")
    for result, ragas_result in zip(results, ragas_results):
        print(f"\n[{result['case_id']}] {result['question']}")
        print(f"Answer: {result['response']}")
        print("Retrieval:")
        for name, score in result["retrieval_metrics"].items():
            print(f"  {name}: {score:.4f}")
        print("Ragas:")
        for name, score in ragas_result["scores"].items():
            print(f"  {name}: {score:.4f}")

    print("\n=== Evaluation Summary ===")
    print(f"Cases: {summary['num_cases']}")
    print("Retrieval metrics:")
    for name, score in summary["retrieval"].items():
        print(f"  {name}: {score:.4f}")
    print("Ragas metrics:")
    for name, score in summary["ragas"].items():
        print(f"  {name}: {score:.4f}")

    print("\nRAGAS evaluation completed successfully.")


if __name__ == "__main__":
    asyncio.run(async_main(parse_args()))

# RAG Evaluation

The SRE Copilot RAG evaluation harness executes the real retrieval and generation path before scoring it with Ragas.

## Pipeline

```text
Question
  -> BGE-M3 dense retrieval
  -> PostgreSQL full-text retrieval
  -> RRF
  -> BGE Reranker v2-M3
  -> Top-K contexts
  -> Gemini answer generation
  -> Ragas evaluation
```

## Metrics

### Retrieval metrics

These are calculated directly from curated relevant document IDs:

- Recall@K
- Precision@K
- MRR
- NDCG@K

### Ragas metrics

- Context Precision
- Context Recall
- Faithfulness
- Answer Relevancy
- Factual Correctness

Ragas v0.4 uses the collections metrics API and `MetricResult.value` for scores.

## Dataset

`data/rag_eval.json` contains curated questions, reference answers, and relevant incident IDs. The dataset should grow with the incident corpus; the current six cases are a smoke-test baseline, not a statistically meaningful benchmark.

### Writing reference answers

Factual Correctness never sees the retrieved context. It decomposes the response into claims and checks each one against the reference text alone, then does the same in reverse. The reference is therefore the only ground truth the metric has, and two rules follow from that:

- **Name every incident the case is about.** Answers identify their incident (`INC-1042`), so a reference that omits the ID turns a correct claim into a false positive and pushes the score toward zero.
- **Cover the question, and nothing beyond it.** A cause question gets a cause-only reference. Resolution facts in a cause reference become unmatched reference claims, which the metric counts as false negatives.

`tests/evaluation/test_dataset.py` enforces both rules against `data/incidents.json`.

## Run

After pulling the repository, synchronize the environment so `uv.lock` is regenerated for the new Ragas dependency:

```bash
uv sync
```

Then run:

```bash
uv run python scripts/rag_eval.py
```

Optional settings:

```bash
uv run python scripts/rag_eval.py --top-k 5
uv run python scripts/rag_eval.py --tenant default
uv run python scripts/rag_eval.py --dataset data/rag_eval.json
```

Environment variables:

- `GEMINI_API_KEY`: Gemini API key used for answer generation and Ragas evaluation.
- `GEMINI_MODEL`: application answer-generation model.
- `RAGAS_MODEL`: optional separate model for Ragas judges; defaults to `GEMINI_MODEL`.
- `RAG_EVAL_TENANT`: default evaluation tenant, `default`.
- `RAG_EVAL_TOP_K`: default retrieval top-K, `5`.

## Interpretation

Do not collapse all scores into one number.

- Low Recall@K / Context Recall -> retrieval is missing relevant evidence.
- Low Precision@K / Context Precision -> retrieval is returning too much irrelevant evidence or ranking it poorly.
- Low MRR / NDCG -> relevant evidence is appearing too low in the ranking.
- Low Faithfulness -> the generator is making claims that are not supported by retrieved context.
- Low Answer Relevancy -> the generated response does not directly address the question.
- Low Factual Correctness -> the answer differs materially from the curated reference answer. Check the reference before the retriever: high Faithfulness with near-zero Factual Correctness means the answer matches the retrieved evidence but not the reference, which usually points at the reference or at an answer that ranged outside the question.

Ragas scores are evaluator-model judgments and should be tracked across versions rather than treated as absolute truth. Keep the curated dataset fixed when comparing retrieval or prompting changes.

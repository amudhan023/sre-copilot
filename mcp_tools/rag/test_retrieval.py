"""
RAG retrieval integration test and inspection script.

Purpose:
    Exercise the complete PostgreSQL-backed retrieval pipeline against the
    incidents currently loaded by mcp_tools.rag.ingest.

Behavior:
    - Uses the default tenant used by the ingestion script.
    - Runs dense BGE-M3 retrieval through pgvector.
    - Runs PostgreSQL full-text sparse retrieval.
    - Combines both ranked lists with Reciprocal Rank Fusion (RRF).
    - Reranks the RRF candidates with BGE Reranker v2-M3.
    - Prints the final Top-K results and their reranker scores.

Important details:
    - The cross-encoder only reranks the RRF candidate set; it does not
      perform database retrieval.
    - Keep the test query aligned with the sample incidents so the ranking
      behavior is easy to inspect.
    - Run this module with `uv run python -m mcp_tools.rag.test_retrieval`.
"""

from mcp_tools.rag.retriever import HybridRetriever


DEFAULT_TENANT = "default"
TEST_QUERY = "payment database connection pool exhaustion"


def print_results(title, results):
    print(f"\n=== {title} ({len(results)}) ===")

    for rank, result in enumerate(results, start=1):
        print(
            f"{rank}. {result['chunk_id']} | "
            f"service={result['service']} | "
            f"title={result['title']}"
        )

        if "dense_score" in result:
            print(f"   dense_score={result['dense_score']:.6f}")
        if "sparse_score" in result:
            print(f"   sparse_score={result['sparse_score']:.6f}")
        if "rrf_score" in result:
            print(f"   rrf_score={result['rrf_score']:.6f}")
        if "rerank_score" in result:
            print(f"   rerank_score={result['rerank_score']:.6f}")
        if "dense_rank" in result:
            print(f"   dense_rank={result['dense_rank']}")
        if "sparse_rank" in result:
            print(f"   sparse_rank={result['sparse_rank']}")


def main():
    print(f"Query: {TEST_QUERY}")
    print(f"Tenant: {DEFAULT_TENANT}")

    retriever = HybridRetriever()
    retrieval = retriever.retrieve(
        TEST_QUERY,
        tenant=DEFAULT_TENANT,
        dense_limit=20,
        sparse_limit=20,
        rrf_limit=20,
        top_k=5,
    )

    print_results("Dense Retrieval", retrieval["dense"])
    print_results("Sparse Retrieval", retrieval["sparse"])
    print_results("RRF Retrieval", retrieval["rrf"])
    print_results("Final Reranked Top-K", retrieval["results"])

    if not retrieval["rrf"]:
        raise AssertionError("RRF returned no results")

    if not retrieval["results"]:
        raise AssertionError("Reranker returned no results")

    if len(retrieval["results"]) > 5:
        raise AssertionError("Final results exceeded top_k=5")

    if any("rerank_score" not in result for result in retrieval["results"]):
        raise AssertionError("Final result is missing rerank_score")

    scores = [result["rerank_score"] for result in retrieval["results"]]
    if scores != sorted(scores, reverse=True):
        raise AssertionError("Final results are not sorted by rerank_score")

    print(
        "\nRetrieval test passed: dense, sparse, RRF, "
        "and cross-encoder reranking returned valid results."
    )


if __name__ == "__main__":
    main()

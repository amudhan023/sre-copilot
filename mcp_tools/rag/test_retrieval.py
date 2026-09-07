"""
RAG retrieval integration test and inspection script.

Purpose:
    Exercise the PostgreSQL-backed hybrid retrieval pipeline against the
    incidents currently loaded by mcp_tools.rag.ingest.

Behavior:
    - Uses the default tenant used by the ingestion script.
    - Runs dense BGE-M3 retrieval through pgvector.
    - Runs PostgreSQL full-text sparse retrieval.
    - Combines both ranked lists with Reciprocal Rank Fusion (RRF).
    - Prints enough metadata and scores to verify each retrieval stage.

Important details:
    - This script intentionally does not run the cross-encoder yet. The
      existing BGE reranker is wired in the next implementation step.
    - Keep the test query aligned with the sample incidents so the retrieval
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
        if "dense_rank" in result:
            print(f"   dense_rank={result['dense_rank']}")
        if "sparse_rank" in result:
            print(f"   sparse_rank={result['sparse_rank']}")


def main():
    print(f"Query: {TEST_QUERY}")
    print(f"Tenant: {DEFAULT_TENANT}")

    retriever = HybridRetriever()

    dense = retriever.dense_search(
        TEST_QUERY,
        tenant=DEFAULT_TENANT,
        limit=20,
    )

    sparse = retriever.sparse_search(
        TEST_QUERY,
        tenant=DEFAULT_TENANT,
        limit=20,
    )

    rrf = retriever.hybrid_search(
        TEST_QUERY,
        tenant=DEFAULT_TENANT,
        limit=20,
        candidate_limit=20,
    )

    print_results("Dense Retrieval", dense)
    print_results("Sparse Retrieval", sparse)
    print_results("RRF Retrieval", rrf)

    if not rrf:
        raise AssertionError("RRF returned no results")

    print("\nRetrieval test passed: dense, sparse, and RRF returned results.")


if __name__ == "__main__":
    main()

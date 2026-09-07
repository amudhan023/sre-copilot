from sre_copilot.rag.retriever import HybridRetriever

# Manual smoke test for the hybrid retriever. Runs one real query against
# Postgres and prints each result's dense/sparse ranks and RRF score.


def main():
    retriever = HybridRetriever()

    results = retriever.hybrid_search(
        query="database connection pool exhaustion",
        tenant="default",
        limit=5,
    )

    for index, result in enumerate(results, start=1):
        print(f"\n--- Result {index} ---")
        print("Document:", result["document_id"])
        print("Chunk:", result["chunk_id"])
        print("Dense rank:", result["dense_rank"])
        print("Sparse rank:", result["sparse_rank"])
        print("RRF score:", result["rrf_score"])
        print("Content:", result["content"])


if __name__ == "__main__":
    main()

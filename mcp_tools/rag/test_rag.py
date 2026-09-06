from mcp_tools.incidents import find_similar_incidents


def main():
    result = find_similar_incidents(
        service="payment-api",
        query=(
            "payment failures caused by "
            "database connection pool exhaustion"
        ),
    )

    print("Service:", result["service"])
    print("Query:", result["query"])

    for index, incident in enumerate(
        result["incidents"],
        start=1,
    ):
        print(f"\n--- Incident {index} ---")
        print("ID:", incident["incident_id"])
        print("Score:", incident["score"])
        print("Title:", incident["title"])
        print("Content:", incident["content"])


if __name__ == "__main__":
    main()
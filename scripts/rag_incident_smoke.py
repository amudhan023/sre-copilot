from sre_copilot.mcp_tools.incidents import find_similar_incidents

# Local development/testing tenant. Ingestion writes the sample incidents
# under this tenant (see sre_copilot/rag/ingest.py).
DEFAULT_TENANT = "default"


def main():
    result = find_similar_incidents(
        tenant=DEFAULT_TENANT,
        service="payment-api",
        query=(
            "payment failures caused by "
            "database connection pool exhaustion"
        ),
    )

    print("Tenant:", result["tenant"])
    print("Service:", result["service"])
    print("Query:", result["query"])

    for index, incident in enumerate(result["incidents"], start=1):
        print(f"\n--- Incident {index} ---")
        print("ID:", incident["incident_id"])
        print("Score:", incident["score"])
        print("Title:", incident["title"])
        print("Content:", incident["content"])


if __name__ == "__main__":
    main()

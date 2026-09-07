from sre_copilot.mcp_tools.logs import search_logs

# Manual smoke test - calls search_logs directly against the local
# logs.json fixture and prints the result. Not part of the pytest suite.


def main():
    result = search_logs(
        service="payment-api",
        query="error",
        start_time="2026-09-04T10:00:00+00:00",
        end_time="2026-09-04T10:10:00+00:00",
    )

    print(result)


if __name__ == "__main__":
    main()

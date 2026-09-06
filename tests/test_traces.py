from mcp_tools.traces import get_traces

# Tests get_traces' operation filtering: an empty operation should match
# every operation for the service (so the agent can discover what
# operations exist), while a real but non-matching operation should filter
# everything out.


START = "2026-09-04T10:00:00+00:00"
END = "2026-09-04T10:10:00+00:00"


def test_empty_operation_matches_all_operations_for_the_service():
    # The agent should be able to discover what operations exist by searching
    # with operation="" instead of guessing a name that may not match the
    # dataset (e.g. "process_payment" vs the real "POST /payments").
    result = get_traces(
        service="payment-api",
        operation="",
        start_time=START,
        end_time=END,
    )

    assert len(result["traces"]) == 3
    assert all(trace["operation"] == "POST /payments" for trace in result["traces"])


def test_non_matching_operation_still_filters_out_traces():
    result = get_traces(
        service="payment-api",
        operation="checkout",
        start_time=START,
        end_time=END,
    )

    assert result["traces"] == []

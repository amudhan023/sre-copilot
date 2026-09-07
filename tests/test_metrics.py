from datetime import datetime, timezone

from sre_copilot.mcp_tools import metrics

# Tests query_metrics against a fake Prometheus response (via monkeypatched
# httpx.get) so the test doesn't need a real Prometheus instance running.


class FakeResponse:
    def raise_for_status(self):
        pass

    def json(self):
        return {"data": {"result": []}}


def test_query_metrics(monkeypatch):
    monkeypatch.setattr(metrics.httpx, "get", lambda *args, **kwargs: FakeResponse())
    start = datetime(2026, 9, 4, 10, 0, tzinfo=timezone.utc)
    end = datetime(2026, 9, 4, 10, 10, tzinfo=timezone.utc)

    result = metrics.query_metrics(
        service="payment-api",
        metric="payment_api_request_duration_seconds_sum",
        start_time=start,
        end_time=end,
    )

    assert result["service"] == "payment-api"
    assert result["metric"] == "payment_api_request_duration_seconds_sum"
    assert result["data"] == []

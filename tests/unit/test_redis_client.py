"""Unit tests for Redis client wrapper — dedup, metric baseline, KR pub/sub."""
import json
import pytest
from unittest.mock import MagicMock, patch, call
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))


@pytest.fixture(autouse=True)
def reset_redis_client():
    """Reset the module-level Redis client singleton between tests."""
    import shared.redis_client as rc
    original = rc._client
    rc._client = None
    yield
    rc._client = original


@pytest.fixture
def mock_redis_client():
    r = MagicMock()
    r.ping.return_value = True
    with patch("shared.redis_client.redis.Redis", return_value=r):
        yield r


class TestSetDedup:
    def test_new_key_returns_true(self, mock_redis_client):
        mock_redis_client.set.return_value = True
        from shared.redis_client import set_dedup
        result = set_dedup("dedup:svc:LATENCY_SPIKE:metric", 300)
        assert result is True
        mock_redis_client.set.assert_called_once_with("dedup:svc:LATENCY_SPIKE:metric", "1", nx=True, ex=300)

    def test_duplicate_key_returns_false(self, mock_redis_client):
        mock_redis_client.set.return_value = None  # Redis returns None when key exists (NX mode)
        from shared.redis_client import set_dedup
        result = set_dedup("dedup:svc:LATENCY_SPIKE:metric", 300)
        assert result is False

    def test_ttl_passed_correctly(self, mock_redis_client):
        mock_redis_client.set.return_value = True
        from shared.redis_client import set_dedup
        set_dedup("key", 600)
        call_kwargs = mock_redis_client.set.call_args[1]
        assert call_kwargs["ex"] == 600


class TestPushMetric:
    def test_pushes_to_correct_key(self, mock_redis_client):
        from shared.redis_client import push_metric
        push_metric("payment-service", "service_latency_p99_ms", 8500.0)
        mock_redis_client.lpush.assert_called_with("metric:payment-service:service_latency_p99_ms", "8500.0")

    def test_trims_to_max_len(self, mock_redis_client):
        from shared.redis_client import push_metric
        push_metric("svc", "metric", 1.0, max_len=50)
        mock_redis_client.ltrim.assert_called_with("metric:svc:metric", 0, 49)

    def test_sets_expiry(self, mock_redis_client):
        from shared.redis_client import push_metric
        push_metric("svc", "metric", 1.0)
        mock_redis_client.expire.assert_called()


class TestGetMetricHistory:
    def test_returns_floats(self, mock_redis_client):
        mock_redis_client.lrange.return_value = ["100.0", "200.0", "300.0"]
        from shared.redis_client import get_metric_history
        result = get_metric_history("svc", "metric", count=3)
        assert result == [100.0, 200.0, 300.0]
        assert all(isinstance(v, float) for v in result)

    def test_empty_history_returns_empty_list(self, mock_redis_client):
        mock_redis_client.lrange.return_value = []
        from shared.redis_client import get_metric_history
        result = get_metric_history("svc", "metric")
        assert result == []

    def test_queries_correct_key(self, mock_redis_client):
        mock_redis_client.lrange.return_value = []
        from shared.redis_client import get_metric_history
        get_metric_history("api-gateway", "service_cpu_percent", count=25)
        mock_redis_client.lrange.assert_called_with("metric:api-gateway:service_cpu_percent", 0, 24)


class TestActiveIncident:
    def test_set_and_get(self, mock_redis_client):
        mock_redis_client.get.return_value = "inc-001"
        mock_redis_client.set.return_value = True
        from shared.redis_client import set_active_incident, get_active_incident
        set_active_incident("payment-service", "inc-001", ttl=1800)
        mock_redis_client.set.assert_called_with("active_incident:payment-service", "inc-001", ex=1800)

        result = get_active_incident("payment-service")
        assert result == "inc-001"

    def test_no_active_incident_returns_none(self, mock_redis_client):
        mock_redis_client.get.return_value = None
        from shared.redis_client import get_active_incident
        assert get_active_incident("unknown-service") is None

    def test_delete_active_incident(self, mock_redis_client):
        from shared.redis_client import delete_active_incident
        delete_active_incident("payment-service")
        mock_redis_client.delete.assert_called_with("active_incident:payment-service")


class TestKRPubSub:
    def test_push_kr_request(self, mock_redis_client):
        from shared.redis_client import push_kr_request
        payload = {"incident_id": "inc-001", "query_type": "INCIDENT_SIMILARITY"}
        push_kr_request("req-001", payload)
        mock_redis_client.rpush.assert_called_once()
        call_args = mock_redis_client.rpush.call_args[0]
        assert call_args[0] == "kr:req:req-001"
        assert "inc-001" in call_args[1]

    def test_push_kr_response(self, mock_redis_client):
        from shared.redis_client import push_kr_response
        payload = {"chunks": [], "total_tokens": 0}
        push_kr_response("req-001", payload)
        mock_redis_client.rpush.assert_called_once()
        call_args = mock_redis_client.rpush.call_args[0]
        assert call_args[0] == "kr:res:req-001"

    def test_wait_kr_response_returns_payload(self, mock_redis_client):
        payload = {"chunks": [{"content": "test"}], "total_tokens": 100}
        mock_redis_client.blpop.return_value = ("kr:res:req-001", json.dumps(payload))
        from shared.redis_client import wait_kr_response
        result = wait_kr_response("req-001", timeout_seconds=5)
        assert result is not None
        assert result["total_tokens"] == 100

    def test_wait_kr_response_timeout(self, mock_redis_client):
        mock_redis_client.blpop.return_value = None
        from shared.redis_client import wait_kr_response
        result = wait_kr_response("req-001", timeout_seconds=1)
        assert result is None


class TestFailureState:
    def test_set_failure_state(self, mock_redis_client):
        from shared.redis_client import set_failure_state
        state = {"latency_p99": 8500.0, "error_rate": 45.0}
        set_failure_state("payment-service", state, ttl=300)
        mock_redis_client.set.assert_called()
        stored_key = mock_redis_client.set.call_args[0][0]
        assert "failure:state:payment-service" in stored_key

    def test_clear_failure_state(self, mock_redis_client):
        from shared.redis_client import clear_failure_state
        clear_failure_state("payment-service")
        mock_redis_client.delete.assert_called_with("failure:state:payment-service")

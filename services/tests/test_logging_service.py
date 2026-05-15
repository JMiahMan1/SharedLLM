# services/tests/test_logging_service.py
import json
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

# Test the logging service logic without requiring a real Redis instance

class TestLogSanitization:
    def test_truncates_long_messages(self):
        from services.logging.main import sanitize_log_payload
        long_msg = "A" * 5000
        result = sanitize_log_payload(long_msg)
        assert len(result) <= 4014  # 4000 + "...[TRUNCATED]"
        assert result.endswith("...[TRUNCATED]")

    def test_redacts_bearer_tokens(self):
        from services.logging.main import sanitize_log_payload
        text = "Authorization: Bearer abc123.xyz.789"
        result = sanitize_log_payload(text)
        assert "Bearer" not in result
        assert "[REDACTED]" in result

    def test_redacts_github_pat(self):
        from services.logging.main import sanitize_log_payload
        text = "Token: github_pat_1234567890abcdef"
        result = sanitize_log_payload(text)
        assert "github_pat" not in result

    def test_redacts_secret_fields_in_dict(self):
        from services.logging.main import sanitize_log_payload
        data = {"api_key": "secret123", "message": "hello", "password": "pass"}
        result = sanitize_log_payload(data)
        assert result["api_key"] == "[REDACTED]"
        assert result["password"] == "[REDACTED]"
        assert result["message"] == "hello"

    def test_redacts_nested_secrets(self):
        from services.logging.main import sanitize_log_payload
        data = {"context": {"token": "abc", "depth": {"secret": "xyz"}}}
        result = sanitize_log_payload(data)
        assert result["context"]["token"] == "[REDACTED]"
        assert result["context"]["depth"]["secret"] == "[REDACTED]"

    def test_preserves_non_secret_data(self):
        from services.logging.main import sanitize_log_payload
        data = {"service": "gateway", "level": "INFO", "message": "Test log"}
        result = sanitize_log_payload(data)
        assert result == data


class TestLimitResolution:
    def test_resolve_limit_with_limit_param(self):
        from services.logging.main import _resolve_limit
        assert _resolve_limit(50, None) == 50

    def test_resolve_limit_with_lines_param(self):
        from services.logging.main import _resolve_limit
        assert _resolve_limit(None, 25) == 25

    def test_resolve_limit_prefers_lines(self):
        from services.logging.main import _resolve_limit
        assert _resolve_limit(100, 50) == 50

    def test_resolve_limit_default(self):
        from services.logging.main import _resolve_limit
        assert _resolve_limit(None, None) == 100

    def test_resolve_limit_caps_at_5000(self):
        from services.logging.main import _resolve_limit
        assert _resolve_limit(10000, None) == 5000

    def test_resolve_limit_minimum_1(self):
        from services.logging.main import _resolve_limit
        assert _resolve_limit(0, None) == 1
        assert _resolve_limit(-5, None) == 1


class TestAuth:
    def test_require_internal_secret_passes(self):
        from services.logging.main import _require_internal_secret, INTERNAL_SECRET
        _require_internal_secret(INTERNAL_SECRET)

    def test_require_internal_secret_fails(self):
        from services.logging.main import _require_internal_secret
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            _require_internal_secret("wrong-secret")
        assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_fetch_logs_filters_by_service():
    from services.logging.main import _fetch_logs
    
    mock_redis = AsyncMock()
    mock_redis.zrevrangebyscore.return_value = [
        json.dumps({"service": "gateway", "message": "msg1", "_ts": time.time()}),
        json.dumps({"service": "execution", "message": "msg2", "_ts": time.time()}),
        json.dumps({"service": "gateway", "message": "msg3", "_ts": time.time()}),
    ]
    
    with patch("services.logging.main.get_redis", return_value=mock_redis):
        results = await _fetch_logs(service="gateway", limit=10)
    
    assert len(results) == 2
    assert all(r["service"] == "gateway" for r in results)


@pytest.mark.asyncio
async def test_fetch_logs_filters_by_user_id():
    from services.logging.main import _fetch_logs
    
    mock_redis = AsyncMock()
    mock_redis.zrevrangebyscore.return_value = [
        json.dumps({"user_id": "alice", "message": "msg1", "_ts": time.time()}),
        json.dumps({"user_id": "bob", "message": "msg2", "_ts": time.time()}),
    ]
    
    with patch("services.logging.main.get_redis", return_value=mock_redis):
        results = await _fetch_logs(user_id="alice", limit=10)
    
    assert len(results) == 1
    assert results[0]["user_id"] == "alice"


@pytest.mark.asyncio
async def test_fetch_logs_admin_sees_all():
    from services.logging.main import _fetch_logs
    
    mock_redis = AsyncMock()
    mock_redis.zrevrangebyscore.return_value = [
        json.dumps({"user_id": "alice", "message": "msg1", "_ts": time.time()}),
        json.dumps({"user_id": "bob", "message": "msg2", "_ts": time.time()}),
    ]
    
    with patch("services.logging.main.get_redis", return_value=mock_redis):
        results = await _fetch_logs(user_id="admin", limit=10)
    
    assert len(results) == 2


@pytest.mark.asyncio
async def test_log_event_stores_in_redis_and_publishes():
    from services.logging.main import log_event, LogEntry
    
    mock_redis = AsyncMock()
    mock_redis.pubsub.return_value = AsyncMock()
    
    entry = LogEntry(user_id="test", service="gateway", level="INFO", message="test log")
    
    with patch("services.logging.main.get_redis", return_value=mock_redis), \
         patch("services.logging.main._require_internal_secret"):
        response = await log_event(entry, x_internal_secret="test-secret")
    
    assert response["status"] == "success"
    mock_redis.zadd.assert_called_once()
    mock_redis.publish.assert_called_once()
    
    # Verify published data contains expected fields
    call_args = mock_redis.publish.call_args
    published_data = json.loads(call_args[0][1])
    assert published_data["service"] == "gateway"
    assert published_data["message"] == "test log"
    assert "user_id" not in published_data or published_data.get("user_id") == "test"

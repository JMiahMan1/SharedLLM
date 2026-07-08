import pytest
import httpx
import os
import json
import time
from datetime import datetime, timedelta

AUTOMATION_URL = os.getenv("EXECUTION_SVC_URL", "http://localhost:8003")
EXECUTION_URL = os.getenv("EXECUTION_SVC_URL", "http://localhost:8003")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
INTERNAL_SECRET = os.getenv("INTERNAL_SECRET", "test-secret")


@pytest.fixture
def internal_headers():
    return {"X-Internal-Secret": INTERNAL_SECRET}


@pytest.fixture
def http_client(internal_headers):
    return httpx.Client(headers=internal_headers, timeout=10.0)


@pytest.fixture
def redis_client():
    import redis
    client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    client.flushall()
    yield client
    client.flushall()
    client.close()


@pytest.mark.local_only
@pytest.mark.integration
class TestRedisQueueFlow:
    def test_timer_creation_in_redis(self, redis_client):
        timer_id = "test_timer_001"
        timer_key = f"timer:{timer_id}"
        expires_at = (datetime.now() + timedelta(hours=1)).isoformat()

        timer_data = {
            "id": timer_id,
            "title": "Test Timer",
            "expires_at": expires_at,
            "active": True,
            "user_id": "test_user",
        }

        redis_client.set(timer_key, json.dumps(timer_data))

        stored = redis_client.get(timer_key)
        assert stored is not None

        parsed = json.loads(stored)
        assert parsed["id"] == timer_id
        assert parsed["active"] is True

        redis_client.delete(timer_key)

    def test_timer_key_pattern_scanning(self, redis_client):
        for i in range(3):
            timer_key = f"timer:test_scan_{i}"
            timer_data = {
                "id": f"test_scan_{i}",
                "title": f"Scan Timer {i}",
                "expires_at": (datetime.now() + timedelta(hours=1)).isoformat(),
                "active": True,
            }
            redis_client.set(timer_key, json.dumps(timer_data))

        keys = redis_client.keys("timer:*")
        assert len(keys) >= 3

        for i in range(3):
            redis_client.delete(f"timer:test_scan_{i}")

    def test_expired_timer_detection(self, redis_client):
        timer_id = "test_expired_timer"
        timer_key = f"timer:{timer_id}"
        expires_at = (datetime.now() - timedelta(minutes=5)).isoformat()

        timer_data = {
            "id": timer_id,
            "title": "Expired Timer",
            "expires_at": expires_at,
            "active": True,
        }

        redis_client.set(timer_key, json.dumps(timer_data))

        stored = json.loads(redis_client.get(timer_key))
        stored_expires = datetime.fromisoformat(stored["expires_at"])
        if stored_expires.tzinfo:
            stored_expires = stored_expires.replace(tzinfo=None)

        assert datetime.now() >= stored_expires

        redis_client.delete(timer_key)

    def test_inactive_timer_skipped(self, redis_client):
        timer_id = "test_inactive_timer"
        timer_key = f"timer:{timer_id}"
        expires_at = (datetime.now() - timedelta(minutes=5)).isoformat()

        timer_data = {
            "id": timer_id,
            "title": "Inactive Timer",
            "expires_at": expires_at,
            "active": False,
        }

        redis_client.set(timer_key, json.dumps(timer_data))

        stored = json.loads(redis_client.get(timer_key))
        assert stored["active"] is False

        redis_client.delete(timer_key)


@pytest.mark.local_only
@pytest.mark.integration
class TestLoggingServiceRedis:
    def test_log_entry_stored_in_redis(self, redis_client):
        LOGGING_URL = os.getenv("LOGGING_SVC_URL", "http://localhost:8006")

        log_entry = {
            "user_id": "test_user",
            "service": "test_service",
            "level": "INFO",
            "message": "Test log message",
        }

        resp = httpx.post(
            f"{LOGGING_URL}/log",
            json=log_entry,
            headers={"X-Internal-Secret": INTERNAL_SECRET},
            timeout=10.0,
        )
        assert resp.status_code == 200

        entries = redis_client.zrange("logs:entries", 0, -1)
        assert len(entries) >= 1

        found = False
        for entry_json in entries:
            entry = json.loads(entry_json)
            if entry.get("message") == "Test log message":
                found = True
                assert entry["service"] == "test_service"
                assert entry["level"] == "INFO"
                break

        assert found, "Log entry not found in Redis"

        redis_client.delete("logs:entries")

    def test_log_sanitization(self, redis_client):
        LOGGING_URL = os.getenv("LOGGING_SVC_URL", "http://localhost:8006")

        log_entry = {
            "user_id": "test_user",
            "service": "test_service",
            "level": "INFO",
            "message": "Token: Bearer abc123xyz",
            "context": {"api_key": "secret-key-value"},
        }

        resp = httpx.post(
            f"{LOGGING_URL}/log",
            json=log_entry,
            headers={"X-Internal-Secret": INTERNAL_SECRET},
            timeout=10.0,
        )
        assert resp.status_code == 200

        entries = redis_client.zrange("logs:entries", 0, -1)
        assert len(entries) >= 1

        entry = json.loads(entries[-1])
        assert "[REDACTED]" in entry["message"]
        assert entry["context"]["api_key"] == "[REDACTED]"

        redis_client.delete("logs:entries")

    def test_log_retrieval(self, redis_client):
        LOGGING_URL = os.getenv("LOGGING_SVC_URL", "http://localhost:8006")

        redis_client.delete("logs:entries")

        for i in range(5):
            log_entry = {
                "user_id": "test_user",
                "service": "test_service",
                "level": "INFO",
                "message": f"Log message {i}",
            }
            httpx.post(
                f"{LOGGING_URL}/log",
                json=log_entry,
                headers={"X-Internal-Secret": INTERNAL_SECRET},
                timeout=10.0,
            )

        resp = httpx.get(
            f"{LOGGING_URL}/logs",
            params={"limit": 10},
            timeout=10.0,
        )
        assert resp.status_code == 200

        logs = resp.json()
        assert isinstance(logs, list)
        assert len(logs) >= 5

        redis_client.delete("logs:entries")

    def test_log_clear_requires_secret(self):
        LOGGING_URL = os.getenv("LOGGING_SVC_URL", "http://localhost:8006")

        resp = httpx.delete(
            f"{LOGGING_URL}/api/logs",
            timeout=10.0,
        )
        assert resp.status_code == 403

    def test_log_clear_with_secret(self, redis_client):
        LOGGING_URL = os.getenv("LOGGING_SVC_URL", "http://localhost:8006")

        redis_client.zadd("logs:entries", {"test": time.time()})

        resp = httpx.delete(
            f"{LOGGING_URL}/api/logs",
            headers={"X-Internal-Secret": INTERNAL_SECRET},
            timeout=10.0,
        )
        assert resp.status_code == 200

        count = redis_client.zcard("logs:entries")
        assert count == 0

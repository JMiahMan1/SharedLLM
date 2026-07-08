import pytest
import httpx
import os

EXECUTION_URL = os.getenv("EXECUTION_SVC_URL", "http://localhost:8003")
IDENTITY_URL = os.getenv("IDENTITY_SVC_URL", "http://localhost:8001")
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
class TestExecutionService:
    def test_health_check(self, http_client):
        resp = http_client.get(f"{EXECUTION_URL}/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"

    def test_light_control_endpoint_exists(self, http_client):
        payload = {
            "user_context": {"user": "test_user"},
            "entity_id": "light.test_light",
            "action": "turn_on",
        }
        resp = http_client.post(
            f"{EXECUTION_URL}/execute/light",
            json=payload,
        )
        assert resp.status_code in (200, 500)

    def test_media_play_endpoint_exists(self, http_client):
        payload = {
            "user_context": {"user": "test_user"},
            "entity_id": "media_player.test_player",
            "media_content_id": "test_content",
            "media_content_type": "music",
        }
        resp = http_client.post(
            f"{EXECUTION_URL}/execute/media/play",
            json=payload,
        )
        assert resp.status_code in (200, 500)

    def test_calendar_endpoint_exists(self, http_client):
        payload = {
            "user_context": {"user": "test_user"},
            "action": "list",
        }
        resp = http_client.post(
            f"{EXECUTION_URL}/execute/calendar",
            json=payload,
        )
        assert resp.status_code in (200, 500)

    def test_note_endpoint_exists(self, http_client):
        payload = {
            "user_context": {"user": "test_user"},
            "action": "create",
            "title": "Test Note",
            "content": "Test content",
        }
        resp = http_client.post(
            f"{EXECUTION_URL}/execute/note",
            json=payload,
        )
        assert resp.status_code in (200, 500)

    def test_timer_endpoint_exists(self, http_client):
        payload = {
            "user_context": {"user": "test_user"},
            "action": "add",
            "duration_minutes": 5,
            "title": "Test Timer",
        }
        resp = http_client.post(
            f"{EXECUTION_URL}/execute/timer",
            json=payload,
        )
        assert resp.status_code in (200, 500)

    def test_tts_endpoint_exists(self, http_client):
        payload = {
            "user_context": {"user": "test_user"},
            "text": "Hello, this is a test.",
        }
        resp = http_client.post(
            f"{EXECUTION_URL}/execute/tts",
            json=payload,
        )
        assert resp.status_code in (200, 500)

    def test_rejects_missing_internal_secret(self):
        resp = httpx.post(
            f"{EXECUTION_URL}/execute/light",
            json={
                "user_context": {"user": "test_user"},
                "entity_id": "light.test",
                "action": "turn_on",
            },
            timeout=10.0,
        )
        assert resp.status_code == 403


@pytest.mark.local_only
@pytest.mark.integration
class TestIntentEngineRouting:
    def test_intent_engine_imports(self):
        from services.gateway.intent_engine import IntentEngine
        engine = IntentEngine()
        assert engine is not None

    def test_light_intent_classification(self):
        from services.gateway.intent_engine import IntentEngine
        engine = IntentEngine()
        intent = engine.classify("Turn on the piano lamp")
        assert intent is not None
        assert "light" in str(intent).lower() or "fast_path" in str(intent).lower()

    def test_media_intent_classification(self):
        from services.gateway.intent_engine import IntentEngine
        engine = IntentEngine()
        intent = engine.classify("Play some music")
        assert intent is not None

    def test_general_conversation_intent(self):
        from services.gateway.intent_engine import IntentEngine
        engine = IntentEngine()
        intent = engine.classify("What is the weather today?")
        assert intent is not None


@pytest.mark.local_only
@pytest.mark.local_only
@pytest.mark.integration
class TestDeviceStateCaching:
    def test_device_state_redis_persistence(self, redis_client):
        device_key = "device_state:test:living_room_tv"
        redis_client.hset(device_key, mapping={
            "power": "on",
            "source": "hdmi1",
            "volume": "50",
        })

        power = redis_client.hget(device_key, "power")
        assert power == "on"

        source = redis_client.hget(device_key, "source")
        assert source == "hdmi1"

        redis_client.delete(device_key)

    def test_device_state_isolation(self, redis_client):
        device_key_1 = "device_state:test:device_1"
        device_key_2 = "device_state:test:device_2"

        redis_client.hset(device_key_1, "power", "on")
        redis_client.hset(device_key_2, "power", "off")

        assert redis_client.hget(device_key_1, "power") == "on"
        assert redis_client.hget(device_key_2, "power") == "off"

        redis_client.delete(device_key_1, device_key_2)

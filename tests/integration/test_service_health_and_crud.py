import pytest
import httpx
import os

GATEWAY_URL = os.getenv("GATEWAY_URL", "http://localhost:8016")
IDENTITY_URL = os.getenv("IDENTITY_URL", "http://localhost:8011")
STORAGE_URL = os.getenv("STORAGE_URL", "http://localhost:8014")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6399/0")
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


@pytest.mark.integration
class TestIdentityService:
    def test_health_check(self, http_client):
        resp = http_client.get(f"{IDENTITY_URL}/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["service"] == "identity"

    def test_create_user_and_verify_in_db(self, http_client, identity_db_session):
        from services.identity.models import User
        from sqlmodel import select

        payload = {
            "username": "test_user_integration",
            "display_name": "Test User",
            "password": "secure_password_123",
            "is_admin": False,
        }

        resp = http_client.post(f"{IDENTITY_URL}/users", json=payload)
        assert resp.status_code == 200

        data = resp.json()
        assert data["username"] == "test_user_integration"
        assert "password" not in data
        assert "password_hash" not in data

        user = identity_db_session.exec(
            select(User).where(User.username == "test_user_integration")
        ).first()
        assert user is not None
        assert user.display_name == "Test User"
        assert user.password_hash is not None

    def test_user_login_returns_token(self, http_client):
        create_payload = {
            "username": "login_test_user",
            "display_name": "Login Test",
            "password": "login_pass_123",
        }
        http_client.post(f"{IDENTITY_URL}/users", json=create_payload)

        login_payload = {
            "username": "login_test_user",
            "password": "login_pass_123",
        }
        resp = http_client.post(f"{IDENTITY_URL}/login", json=login_payload)
        assert resp.status_code == 200

        data = resp.json()
        assert "api_key" in data
        assert data["username"] == "login_test_user"

    def test_credential_resolution(self, http_client):
        create_payload = {
            "username": "resolve_test_user",
            "display_name": "Resolve Test",
            "password": "resolve_pass",
            "ha_url": "http://ha.test.local:8123",
            "ha_token": "test-ha-token-value",
        }
        http_client.post(f"{IDENTITY_URL}/users", json=create_payload)

        resolve_payload = {
            "user_id": "resolve_test_user",
            "resolve_secrets": True,
        }
        resp = http_client.post(
            f"{IDENTITY_URL}/resolve",
            json=resolve_payload,
        )
        assert resp.status_code == 200

        data = resp.json()
        assert data["username"] == "resolve_test_user"
        assert data["ha_url"] == "http://ha.test.local:8123"

    def test_global_settings_crud(self, http_client):
        resp = http_client.get(f"{IDENTITY_URL}/settings")
        assert resp.status_code == 200

        settings = resp.json()
        assert isinstance(settings, list)
        assert len(settings) > 0

        setting = settings[0]
        key = setting["key"]

        update_payload = {"value": "updated_test_value"}
        resp = http_client.patch(
            f"{IDENTITY_URL}/settings/{key}",
            json=update_payload,
        )
        assert resp.status_code == 200
        assert resp.json()["value"] == "updated_test_value"

    def test_device_assignment_lifecycle(self, http_client, identity_db_session):
        from services.identity.models import DeviceAssignment
        from sqlmodel import select

        user_payload = {
            "username": "device_test_user",
            "display_name": "Device Test",
            "password": "device_pass",
        }
        user_resp = http_client.post(f"{IDENTITY_URL}/users", json=user_payload)
        user_id = user_resp.json()["id"]

        device_payload = {
            "device_id": "media_player.test_speaker",
            "user_id": user_id,
        }
        resp = http_client.post(
            f"{IDENTITY_URL}/devices",
            json=device_payload,
        )
        assert resp.status_code == 200

        assignment = identity_db_session.exec(
            select(DeviceAssignment).where(
                DeviceAssignment.device_id == "media_player.test_speaker"
            )
        ).first()
        assert assignment is not None
        assert assignment.user_id == user_id


@pytest.mark.integration
class TestStorageService:
    def test_health_check(self, http_client):
        resp = http_client.get(f"{STORAGE_URL}/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["service"] == "storage"

    def test_indexing_pause_and_resume(self, http_client):
        resp = http_client.post(f"{STORAGE_URL}/index/pause")
        assert resp.status_code == 200
        assert resp.json()["status"] == "PAUSED"

        resp = http_client.post(f"{STORAGE_URL}/index/resume")
        assert resp.status_code == 200
        assert resp.json()["status"] == "RESUMED"

    def test_status_endpoint_returns_structure(self, http_client):
        resp = http_client.get(f"{STORAGE_URL}/status")
        assert resp.status_code == 200

        data = resp.json()
        assert "status" in data
        assert "indexer" in data
        assert "checkpointed_files" in data


@pytest.mark.integration
class TestGatewayService:
    def test_health_check(self, http_client):
        resp = http_client.get(f"{GATEWAY_URL}/health")
        assert resp.status_code == 200

    def test_readiness_checks_all_services(self, http_client):
        resp = http_client.get(f"{GATEWAY_URL}/health/ready")
        assert resp.status_code in (200, 503)

        data = resp.json()
        assert "services" in data

    def test_chat_endpoint_rejects_unauthenticated(self):
        resp = httpx.post(
            f"{GATEWAY_URL}/api/chat",
            json={"messages": [{"role": "user", "content": "test"}]},
            timeout=10.0,
        )
        assert resp.status_code in (401, 403, 422)

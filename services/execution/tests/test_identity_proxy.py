from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from services.execution.main import INTERNAL_SECRET, app

client = TestClient(app)


def test_execute_identity_import():
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(return_value={"status": "SUCCESS", "message": "Imported 5 users"})
    mock_resp.text = AsyncMock(return_value="")

    mock_session = AsyncMock()
    mock_session.post = AsyncMock(return_value=mock_resp)

    @asynccontextmanager
    async def mock_get_client():
        yield mock_session

    with patch("services.execution.main.get_client", new=mock_get_client):
        payload = {
            "user_context": {
                "user": "admin",
                "is_admin": True,
                "api_key": "test-key",
            },
            "action": "import_nextcloud",
        }

        response = client.post(
            "/execute/identity",
            json=payload,
            headers={"X-Internal-Secret": INTERNAL_SECRET},
        )

        assert response.status_code == 200
        assert response.json()["status"] == "SUCCESS"
        detail = response.json().get("detail", {})
        data = detail.get("data", {})
        assert "Imported 5 users" in data.get("message", "")


def test_execute_identity_create():
    mock_resp = MagicMock()
    mock_resp.status = 201
    mock_resp.json = AsyncMock(return_value={"username": "newuser", "display_name": "New User"})
    mock_resp.text = AsyncMock(return_value="")

    mock_session = AsyncMock()
    mock_session.post = AsyncMock(return_value=mock_resp)

    @asynccontextmanager
    async def mock_get_client():
        yield mock_session

    with patch("services.execution.main.get_client", new=mock_get_client):
        payload = {
            "user_context": {
                "user": "admin",
                "is_admin": True,
                "api_key": "test-key",
            },
            "action": "create",
            "username": "newuser",
            "display_name": "New User",
            "is_admin": False,
        }

        response = client.post(
            "/execute/identity",
            json=payload,
            headers={"X-Internal-Secret": INTERNAL_SECRET},
        )

        assert response.status_code == 200
        assert response.json()["status"] == "SUCCESS"
        detail = response.json().get("detail", {})
        data = detail.get("data", {})
        assert data.get("username") == "newuser"


def test_execute_identity_error():
    mock_resp = MagicMock()
    mock_resp.status = 500
    mock_resp.text = AsyncMock(return_value="Internal Server Error")

    mock_session = AsyncMock()
    mock_session.get = AsyncMock(return_value=mock_resp)

    @asynccontextmanager
    async def mock_get_client():
        yield mock_session

    with patch("services.execution.main.get_client", new=mock_get_client):
        payload = {
            "user_context": {
                "user": "admin",
                "is_admin": True,
                "api_key": "test-key",
            },
            "action": "list",
        }

        response = client.post(
            "/execute/identity",
            json=payload,
            headers={"X-Internal-Secret": INTERNAL_SECRET},
        )

        assert response.status_code == 200
        assert response.json()["status"] == "FAILURE"
        assert "Identity service returned 500" in response.json()["message"]

import pytest
import respx
from httpx import Response
from fastapi.testclient import TestClient
try:
    from main import app, INTERNAL_SECRET, IDENTITY_SVC_URL
except ImportError:
    from execution.main import app, INTERNAL_SECRET, IDENTITY_SVC_URL

client = TestClient(app)

@respx.mock
def test_execute_identity_import():
    # Mock Identity Service response
    respx.post(f"{IDENTITY_SVC_URL}/api/auth/import/nextcloud").mock(
        return_value=Response(200, json={"status": "SUCCESS", "message": "Imported 5 users"})
    )

    payload = {
        "user_context": {
            "user": "admin",
            "is_admin": True,
            "api_key": "test-key"
        },
        "action": "import_nextcloud"
    }

    response = client.post(
        "/execute/identity",
        json=payload,
        headers={"X-Internal-Secret": INTERNAL_SECRET}
    )

    assert response.status_code == 200
    assert response.json()["status"] == "SUCCESS"
    # Handler wraps identity response in detail.data
    detail = response.json().get("detail", {})
    data = detail.get("data", {})
    assert "Imported 5 users" in data.get("message", "")

@respx.mock
def test_execute_identity_create():
    # Mock Identity Service response
    respx.post(f"{IDENTITY_SVC_URL}/api/users").mock(
        return_value=Response(201, json={"username": "newuser", "display_name": "New User"})
    )

    payload = {
        "user_context": {
            "user": "admin",
            "is_admin": True,
            "api_key": "test-key"
        },
        "action": "create",
        "username": "newuser",
        "display_name": "New User",
        "is_admin": False
    }

    response = client.post(
        "/execute/identity",
        json=payload,
        headers={"X-Internal-Secret": INTERNAL_SECRET}
    )

    assert response.status_code == 200
    assert response.json()["status"] == "SUCCESS"
    # Handler wraps identity response in detail.data
    detail = response.json().get("detail", {})
    data = detail.get("data", {})
    assert data.get("username") == "newuser"

@respx.mock
def test_execute_identity_error():
    # Mock Identity Service error
    respx.get(f"{IDENTITY_SVC_URL}/api/users").mock(
        return_value=Response(500, text="Internal Server Error")
    )

    payload = {
        "user_context": {
            "user": "admin",
            "is_admin": True,
            "api_key": "test-key"
        },
        "action": "list"
    }

    response = client.post(
        "/execute/identity",
        json=payload,
        headers={"X-Internal-Secret": INTERNAL_SECRET}
    )

    assert response.status_code == 200
    assert response.json()["status"] == "FAILURE"
    assert "Identity service returned 500" in response.json()["message"]

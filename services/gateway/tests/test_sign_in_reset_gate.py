"""A key revoked by identity's sign-in reset is answered with the reason, not a bare 401.

The web UI and the Android app read ``code: sign_in_reset`` to clear the stored key and
show "Your sign-in was reset, please sign in again" instead of failing silently.
"""
import pytest
from fastapi.testclient import TestClient

from services.gateway import main as gateway_main
from services.gateway.main import app


class _Resp:
    def __init__(self, status: int, body: dict):
        self.status = status
        self._body = body

    async def json(self, content_type=None):
        return self._body


class _Client:
    def __init__(self, status: int, body: dict):
        self.status, self.body = status, body
        self.calls = 0

    async def get(self, *_a, **_k):
        self.calls += 1
        return _Resp(self.status, self.body)


@pytest.fixture
def client_for(monkeypatch):
    def make(status: int, body: dict) -> TestClient:
        fake = _Client(status, body)
        monkeypatch.setattr(gateway_main, "get_http_client", lambda: fake)
        return TestClient(app, raise_server_exceptions=False)

    return make


def test_a_reset_key_gets_the_sign_in_reset_code(client_for):
    client = client_for(401, {"detail": "Your sign-in was reset, please sign in again.", "code": "sign_in_reset"})
    resp = client.get("/api/workspaces", headers={"Authorization": "Bearer fake-old-key"})
    assert resp.status_code == 401
    assert resp.json() == {"detail": "Your sign-in was reset, please sign in again.", "code": "sign_in_reset"}


def test_an_unknown_key_is_refused_with_a_stable_code(client_for):
    client = client_for(401, {"detail": "Invalid API key"})
    resp = client.get("/api/workspaces", headers={"Authorization": "Bearer fake-junk-key"})
    assert resp.status_code == 401
    assert resp.json() == {"detail": "Invalid API Key", "code": "api_key_invalid"}


def test_no_key_at_all_is_a_plain_401_the_apps_ignore(client_for):
    client = client_for(401, {"detail": "unused"})
    resp = client.get("/api/workspaces")
    assert resp.status_code == 401
    assert "code" not in resp.json()


def test_admin_routes_say_the_key_was_refused(client_for):
    client = client_for(401, {"detail": "Invalid API key"})
    resp = client.post("/api/workspaces", headers={"Authorization": "Bearer fake-junk-key"}, json={"id": "x"})
    assert resp.status_code == 401
    assert resp.json()["code"] == "api_key_invalid"


def test_the_reset_flag_does_not_leak_into_the_next_request(client_for, monkeypatch):
    client = client_for(401, {"detail": "x", "code": "sign_in_reset"})
    assert client.get("/api/workspaces", headers={"Authorization": "Bearer fake-old-key"}).json()["code"] == "sign_in_reset"
    plain = _Client(401, {"detail": "Invalid API key"})
    monkeypatch.setattr(gateway_main, "get_http_client", lambda: plain)
    assert client.get("/api/workspaces", headers={"Authorization": "Bearer fake-junk"}).json()["code"] == "api_key_invalid"

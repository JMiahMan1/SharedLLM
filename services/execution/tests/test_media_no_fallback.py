"""BUG-02: execution media endpoints must not fall back to the first user.

With the first-user fallback removed, a request whose user_context lacks HA
credentials must error — never silently execute with user 1's credentials.
"""
import os

from fastapi.testclient import TestClient

from services.execution.main import app

client = TestClient(app)


def _secret() -> str:
    return os.environ["INTERNAL_SECRET"]


def test_media_status_missing_credentials_errors(monkeypatch):
    import services.execution.main as exec_main

    called = []

    async def mock_first_user():
        called.append(True)
        return {"user": "default", "ha_url": "http://ha.local", "ha_token": "user1-token"}

    monkeypatch.setattr(exec_main, "resolve_first_user", mock_first_user)

    resp = client.post(
        "/execute/media/status",
        headers={"X-Internal-Secret": _secret()},
        json={"user_context": {"user": "testuser"}},
    )

    assert resp.status_code >= 400
    assert not called


def test_media_play_missing_credentials_errors(monkeypatch):
    import services.execution.main as exec_main

    called = []

    async def mock_first_user():
        called.append(True)
        return {"user": "default", "ha_url": "http://ha.local", "ha_token": "user1-token"}

    monkeypatch.setattr(exec_main, "resolve_first_user", mock_first_user)

    resp = client.post(
        "/execute/media/play",
        headers={"X-Internal-Secret": _secret()},
        json={
            "user_context": {"user": "testuser"},
            "entity_id": "media_player.office",
            "query": "test",
        },
    )

    assert resp.status_code >= 400
    assert not called

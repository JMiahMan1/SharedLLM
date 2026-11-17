"""BUG-02: execution media endpoints must not fall back to the first user.

A request whose user_context lacks a hub login is sent with the hub's own
login (hardware_router.hub_login), never with user 1's credentials; with no
hub configured at all it errors.
"""
import os

from fastapi.testclient import TestClient

from services.execution.main import app

client = TestClient(app)


def _secret() -> str:
    return os.environ["INTERNAL_SECRET"]


def test_media_status_missing_credentials_errors(monkeypatch):
    import services.config as config
    import services.execution.main as exec_main

    monkeypatch.setattr(config, "HA_URL", None)

    monkeypatch.setattr(config, "HOMEHUB_URL", "")
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
    import services.config as config
    import services.execution.main as exec_main

    monkeypatch.setattr(config, "HA_URL", None)

    monkeypatch.setattr(config, "HOMEHUB_URL", "")
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

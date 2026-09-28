"""Per-user credentials in execution: never act as another user.

Identity now hands each user their own HA / Music Assistant / Audiobookshelf
credentials and leaves unconfigured services empty (shared credentials require
an explicit grant). Execution must therefore fail loudly instead of filling the
gap from user 1 -- a user with no HA credentials must not drive the admin's
house, and a user with no Music Assistant credentials must not see the admin's
library presented as their own empty one.
"""
import os

import pytest
from fastapi.testclient import TestClient

from services.execution.main import app

client = TestClient(app)


def _secret() -> str:
    return os.environ["INTERNAL_SECRET"]


def _forbid_first_user(monkeypatch) -> list:
    """Make any use of the first-user resolver a test failure."""
    import services.execution.main as exec_main

    called: list = []

    async def mock_first_user():
        called.append(True)
        return {"user": "default", "ha_url": "http://ha.admin", "ha_token": "admin-token"}

    monkeypatch.setattr(exec_main, "resolve_first_user", mock_first_user)
    return called


# ─── Home Assistant: announce / entity search ─────────────────────────────────


def test_announce_without_ha_credentials_does_not_borrow_admin(monkeypatch):
    called = _forbid_first_user(monkeypatch)

    resp = client.post(
        "/execute/announce",
        headers={"X-Internal-Secret": _secret()},
        json={"user_context": {"user": "casey"}, "message": "play something"},
    )

    assert not called, "announce must not fall back to the first user"
    body = resp.json()
    assert body.get("status") != "SUCCESS"
    assert "casey" in json_text(body)


def test_entity_search_without_ha_credentials_does_not_borrow_admin(monkeypatch):
    called = _forbid_first_user(monkeypatch)

    resp = client.post(
        "/execute/entity/search",
        headers={"X-Internal-Secret": _secret()},
        json={"user_context": {"user": "casey"}, "query": "lamp"},
    )

    assert not called, "entity search must not fall back to the first user"
    body = resp.json()
    assert body.get("status") != "SUCCESS"
    assert "casey" in json_text(body)


def json_text(body) -> str:
    import json

    return json.dumps(body)


# ─── Music Assistant ──────────────────────────────────────────────────────────


def _stub_resolve(monkeypatch, creds: dict | None) -> None:
    import services.execution.main as exec_main

    async def fake(user_id=None, rag_user=None):
        return creds

    monkeypatch.setattr(exec_main, "resolve_internal_user", fake)


def test_ma_playlists_without_credentials_reports_the_missing_setup(monkeypatch):
    called = _forbid_first_user(monkeypatch)
    _stub_resolve(monkeypatch, {"user": "casey", "ha_url": None, "ha_token": None,
                                "mass_url": None, "mass_token": None})

    resp = client.get(
        "/execute/media/music-assistant/playlists",
        headers={"X-Internal-Secret": _secret()},
        params={"user_id": "casey"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "FAILURE", body
    assert "casey" in json_text(body)
    assert "Music Assistant" in json_text(body)
    assert not called


def test_ma_browse_without_credentials_reports_the_missing_setup(monkeypatch):
    _forbid_first_user(monkeypatch)
    _stub_resolve(monkeypatch, {"user": "casey", "ha_url": None, "ha_token": None})

    resp = client.get(
        "/execute/media/music-assistant/browse",
        headers={"X-Internal-Secret": _secret()},
        params={"user_id": "casey"},
    )

    body = resp.json()
    assert body["status"] == "FAILURE", body
    assert "Music Assistant" in json_text(body)


def test_ma_playlists_use_a_direct_token_without_home_assistant(monkeypatch):
    """A user may configure Music Assistant without Home Assistant."""
    _stub_resolve(
        monkeypatch,
        {
            "user": "bob",
            "ha_url": None,
            "ha_token": None,
            "mass_url": "http://ma.bob:8095",
            "mass_token": "bob-ma-token",
        },
    )

    seen: dict = {}

    async def fake_playlists(mass_url, mass_token):
        seen["url"] = mass_url
        seen["token"] = mass_token
        return [{"id": "pl-1", "name": "Bob's Mix"}]

    import services.execution.handlers.mass_client as mass_client

    monkeypatch.setattr(mass_client, "get_playlists", fake_playlists)

    resp = client.get(
        "/execute/media/music-assistant/playlists",
        headers={"X-Internal-Secret": _secret()},
        params={"user_id": "bob"},
    )

    body = resp.json()
    assert body["status"] == "SUCCESS", body
    assert body["playlists"] == [{"id": "pl-1", "name": "Bob's Mix"}]
    assert seen == {"url": "http://ma.bob:8095", "token": "bob-ma-token"}


def test_ma_playlists_pass_the_resolved_users_home_assistant(monkeypatch):
    _stub_resolve(
        monkeypatch,
        {"user": "bob", "ha_url": "http://ha.bob", "ha_token": "bob-ha-token",
         "mass_url": None, "mass_token": None},
    )

    seen: dict = {}

    async def fake_via_ha(ha_url, ha_token, mass_entry_id="", limit=50):
        seen["ha_url"] = ha_url
        seen["ha_token"] = ha_token
        return [{"id": "pl-2", "name": "Via HA"}]

    import services.execution.main as exec_main

    monkeypatch.setattr(exec_main, "_get_ma_playlists_via_ha", fake_via_ha)

    resp = client.get(
        "/execute/media/music-assistant/playlists",
        headers={"X-Internal-Secret": _secret()},
        params={"user_id": "bob"},
    )

    assert resp.json()["playlists"] == [{"id": "pl-2", "name": "Via HA"}]
    assert seen["ha_url"] == "http://ha.bob"


# ─── Discovery: use the credentials the caller passed ─────────────────────────


def test_discovery_entities_uses_the_creds_the_caller_passed(monkeypatch):
    called = _forbid_first_user(monkeypatch)
    seen: dict = {}

    async def fake_get_states(ha_url, ha_token):
        seen["ha_url"] = ha_url
        seen["ha_token"] = ha_token
        return [{"entity_id": "light.bob", "state": "on"}]

    import services.execution.ha_client as ha_client

    monkeypatch.setattr(ha_client, "get_states", fake_get_states)
    monkeypatch.setattr(ha_client, "get_areas", lambda *a, **k: _async({}))

    resp = client.get(
        "/discovery/entities",
        headers={"X-Internal-Secret": _secret()},
        params={"ha_url": "http://ha.bob", "ha_token": "bob-ha-token"},
    )

    assert resp.status_code == 200
    assert seen == {"ha_url": "http://ha.bob", "ha_token": "bob-ha-token"}
    assert not called


def test_discovery_entities_without_credentials_names_the_missing_setup(monkeypatch):
    called = _forbid_first_user(monkeypatch)

    resp = client.get(
        "/discovery/entities",
        headers={"X-Internal-Secret": _secret()},
        params={},
    )

    assert resp.status_code == 400
    assert "Home Assistant" in resp.text
    assert not called


# ─── UserContext keeps the per-user Music Assistant credentials ───────────────


def test_user_context_carries_the_per_user_mass_credentials():
    from services.execution.schemas import UserContext

    ctx = UserContext(user="bob", mass_url="http://ma.bob:8095", mass_token="bob-token")
    assert ctx.mass_url == "http://ma.bob:8095"
    assert ctx.mass_token == "bob-token"


def _async(value):
    async def _coro():
        return value

    return _coro()

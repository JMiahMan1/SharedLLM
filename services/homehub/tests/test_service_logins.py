"""Service logins (long-lived tokens) and linking Music Assistant's HA plugin to the hub."""
from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException

from services.homehub.auth import Auth
from services.homehub.core import ConfigEntry, Hub
from services.homehub.ha_auth import HAAuth
from services.homehub.runtime import Runtime
from services.homehub.service_logins_api import MA_CLIENT, MA_USER, link_music_assistant
from services.homehub.store import Store


@pytest.fixture
def parts(tmp_path):
    store = Store(str(tmp_path / "hub.db"), Fernet.generate_key().decode())
    auth = Auth(store, "internal")
    return store, auth, HAAuth(store, auth)


def test_a_service_login_is_its_own_non_admin_user_and_revocable(parts):
    store, auth, ha = parts
    uid = ha.ensure_service_user("Music Assistant")
    assert ha.ensure_service_user("Music Assistant") == uid  # created once
    token = ha.create_long_lived_token(uid, "ma")
    p = auth.authenticate(token)
    assert p is not None and p.user_id == uid and p.is_admin is False
    assert ha.revoke_long_lived(uid, "ma") == 1
    assert auth.authenticate(token) is None  # revoked at once, not after a cache minute


class FakeMA:
    def __init__(self, errors=None):
        self.entry = ConfigEntry("ma", "music_assistant")
        self.calls = []
        self.errors = errors or {}

    async def command(self, command, **args):
        self.calls.append((command, args))
        if command == "config/providers":
            return [{"domain": "spotify", "instance_id": "spotify--1"}, {"domain": "hass", "instance_id": "hass--x"}]
        if command == "config/providers/reconfigure":
            return {"flow_id": "f1", "type": "form", "errors": {}}
        if command == "config/flows/submit":
            return {"flow_id": "f1", "type": "form" if self.errors else "finish", "errors": self.errors}
        return None


async def test_link_gives_music_assistant_a_fresh_login_and_the_hub_url(parts):
    store, auth, ha = parts
    hub = Hub()
    runtime = Runtime(hub, store)
    ma = FakeMA()
    hub.drivers["ma"] = ma
    first = await link_music_assistant(runtime, ha, "http://127.0.0.1:8123")
    submit = next(a for c, a in ma.calls if c == "config/flows/submit")
    token1 = submit["values"]["token"]
    assert submit["values"]["url"] == "http://127.0.0.1:8123" and first["instance_id"] == "hass--x"
    assert auth.authenticate(token1).is_admin is False
    await link_music_assistant(runtime, ha, "http://127.0.0.1:8123")  # linking again replaces the login
    tokens = [a["values"]["token"] for c, a in ma.calls if c == "config/flows/submit"]
    auth._cache.clear()
    assert auth.authenticate(tokens[0]) is None and auth.authenticate(tokens[1]) is not None
    users = [u for u in store.get_kv("users", []) if u.get("service")]
    assert [u["name"] for u in users] == [MA_USER]
    assert MA_CLIENT


async def test_a_refused_link_revokes_the_login_and_aborts_the_flow(parts):
    store, auth, ha = parts
    hub = Hub()
    ma = FakeMA(errors={"base": "cannot_connect"})
    hub.drivers["ma"] = ma
    with pytest.raises(HTTPException) as err:
        await link_music_assistant(Runtime(hub, store), ha, "http://127.0.0.1:8123")
    assert err.value.status_code == 502
    token = next(a for c, a in ma.calls if c == "config/flows/submit")["values"]["token"]
    assert auth.authenticate(token) is None
    assert ("config/flows/abort", {"flow_id": "f1"}) in ma.calls


async def test_link_uses_the_drivers_live_connection(parts):
    """The real driver keeps its MA connection in ``conn``; the link must talk through it."""
    store, _auth, ha = parts
    hub = Hub()

    class Driver:
        entry = ConfigEntry("ma", "music_assistant")
        conn = FakeMA()

    hub.drivers["ma"] = Driver()
    out = await link_music_assistant(Runtime(hub, store), ha, "http://127.0.0.1:8123")
    assert out["instance_id"] == "hass--x" and Driver.conn.calls


async def test_link_needs_music_assistant_connected(parts):
    store, _auth, ha = parts
    with pytest.raises(HTTPException) as err:
        await link_music_assistant(Runtime(Hub(), store), ha, "http://127.0.0.1:8123")
    assert err.value.status_code == 503


def test_ha_ws_commands_music_assistant_asks_for(tmp_path):
    from fastapi.testclient import TestClient

    from services.homehub.api import build_app
    from services.homehub.core import Area, EntityEntry

    store = Store(str(tmp_path / "h.db"), Fernet.generate_key().decode())
    store.set_kv("persons", [{"id": "p1", "name": "Pat", "user_id": "u1", "device_trackers": ["device_tracker.p"]}])
    store.set_kv("users", [{"id": "u1", "name": "Pat", "is_active": True, "group_ids": []}])
    hub = Hub()
    hub.areas["office"] = Area("office", "Office")
    hub.entities["light.desk"] = EntityEntry("light.desk", "wiz", "x", area_id="office", entity_category=None)
    with TestClient(build_app(Runtime(hub, store), Auth(store, "s"))) as client, \
            client.websocket_connect("/api/websocket") as ws:
        ws.receive_json()
        ws.send_json({"type": "auth", "access_token": "s"})
        ws.receive_json()
        ws.send_json({"id": 99, "type": "config/entity_registry/get_entries",
                      "entity_ids": ["light.desk", "light.gone"]})
        got = ws.receive_json()["result"]
        assert got["light.gone"] is None and got["light.desk"]["entity_id"] == "light.desk"
        for i, cmd in enumerate(["person/list", "network/url", "config/entity_registry/list_for_display",
                                 "config/auth/list"], start=1):
            ws.send_json({"id": i, "type": cmd})
            r = ws.receive_json()
            assert r["success"], (cmd, r)
            if cmd == "person/list":
                assert r["result"]["storage"][0]["device_trackers"] == ["device_tracker.p"]
            if cmd == "config/entity_registry/list_for_display":
                assert r["result"]["entities"][0] == {"ei": "light.desk", "pl": "wiz", "ai": "office"}
            if cmd == "config/auth/list":
                assert "credentials" in r["result"][0] and r["result"][0]["credentials"] == []

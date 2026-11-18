"""A duplicate device's hidden copy (homehub duplicates.py) never enters what Jarvis browses.

Execution marks an entity the hub hides with ``attributes._hidden`` (and drops it unless asked to keep it).
The gateway keeps it out of the entity lists and pickers, the fast-path name cache and the RAG sync, while a
lookup by entity id (the state cache) still sees it. Every name here is made up.
"""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import patch

from services.gateway import ha_state_cache, hub_access
from services.gateway import main as gateway_main
from services.gateway.intent_engine import IntentEngine

SHOWN = {"entity_id": "light.pantry_lamp_wiz", "state": "on", "attributes": {"friendly_name": "Pantry Lamp"}}
HIDDEN = {"entity_id": "light.pantry_lamp", "state": "on",
          "attributes": {"friendly_name": "Pantry Lamp", "_hidden": True}}


class _Resp:
    def __init__(self, data, status=200):
        self.status = status
        self._data = data

    async def json(self, *a, **k):
        return self._data

    async def text(self):
        return json.dumps(self._data)


class _Client:
    def __init__(self, data):
        self._data = data
        self.gets: list[tuple[str, dict]] = []
        self.posts: list[tuple[str, dict]] = []

    async def get(self, url, **kwargs):
        self.gets.append((url, kwargs.get("params")))
        return _Resp(self._data)

    async def post(self, url, **kwargs):
        self.posts.append((url, kwargs.get("json")))
        return _Resp({"orphaned_entity_ids": []})


def test_browsable_drops_hidden_entities():
    assert hub_access.browsable([SHOWN, HIDDEN, {"entity_id": "x.y"}]) == [SHOWN, {"entity_id": "x.y"}]
    assert hub_access.is_hidden(HIDDEN) and not hub_access.is_hidden(SHOWN)


def test_visible_entities_never_show_hidden_ones_even_to_an_admin():
    admin = {"user": "admin", "is_admin": True}
    assert asyncio.run(hub_access.visible_entities(admin, [SHOWN, HIDDEN])) == [SHOWN]


def test_the_fast_path_cache_never_learns_the_hidden_copys_name():
    engine = SimpleNamespace()
    IntentEngine.update_entity_cache(engine, [HIDDEN, SHOWN])
    assert engine.entity_cache["pantry lamp"] == "light.pantry_lamp_wiz"
    assert "light.pantry_lamp" not in engine.entity_cache.values()
    assert [r[0] for r in engine.device_records] == ["light.pantry_lamp_wiz"]


def test_the_entity_list_and_picker_leave_hidden_entities_out(client, fake_identity):
    fake = _Client({"entities": [SHOWN, HIDDEN]})
    with patch.object(gateway_main, "get_http_client", return_value=fake):
        resp = client.get("/api/entities")
    assert resp.status_code == 200
    assert [e["entity_id"] for e in resp.json()["entities"]] == ["light.pantry_lamp_wiz"]


def test_the_rag_sync_and_the_cache_get_only_shown_entities(monkeypatch):
    from services import config

    fake = _Client({"entities": [SHOWN, HIDDEN]})
    learned: list = []
    monkeypatch.setattr(config, "any_home_hub", lambda: True)
    monkeypatch.setattr(gateway_main.engine, "update_entity_cache", lambda ents: learned.extend(ents))

    async def go():
        with patch.object(gateway_main, "get_http_client", return_value=fake):
            out = await gateway_main.fetch_ha_entities({"user": "admin", "is_admin": True})
            await asyncio.sleep(0.05)  # the RAG sync runs in the background
        return out

    out = asyncio.run(go())
    assert [e["entity_id"] for e in out] == ["light.pantry_lamp_wiz"]
    assert [e["entity_id"] for e in learned] == ["light.pantry_lamp_wiz"]
    (_url, body), = [p for p in fake.posts if p[0].endswith("/rag/sync/devices")]
    assert [e["entity_id"] for e in body["entities"]] == ["light.pantry_lamp_wiz"]


def test_the_state_cache_asks_for_hidden_entities_too():
    """A command naming the hidden copy still reads its state."""
    assert ha_state_cache._login_params(None, None) == {"include_hidden": "1"}
    assert ha_state_cache._login_params("http://hub.example", "tok") == {
        "ha_url": "http://hub.example", "ha_token": "tok", "include_hidden": "1"}


# -- the duplicates admin routes (discovery_routes.py) ---------------------------------------------

def _hub_routes(monkeypatch):
    from fastapi import HTTPException
    from fastapi.testclient import TestClient

    from services.gateway import discovery_routes

    seen: list = []

    async def admin(request):
        if request.headers.get("Authorization") == "Bearer admin-key":
            return "parent"
        raise HTTPException(status_code=403, detail="Admin only")

    async def call(method, path, *, params=None, json=None, timeout=None):
        seen.append((method, path, json))
        return 200, {"groups": [], "integrations": [], "changed": []}

    monkeypatch.setattr(gateway_main, "_require_admin", admin)
    monkeypatch.setattr(discovery_routes, "_hub", call)
    return TestClient(gateway_main.app, raise_server_exceptions=False), seen


def test_duplicate_routes_are_for_admins_and_pass_only_their_fields(monkeypatch):
    client, seen = _hub_routes(monkeypatch)
    admin = {"Authorization": "Bearer admin-key"}
    for verb, path in (("get", "/api/homehub/devices/duplicates"), ("post", "/api/homehub/devices/duplicates/hide"),
                       ("post", "/api/homehub/devices/duplicates/unhide")):
        assert getattr(client, verb)(path, headers={"Authorization": "Bearer kid"}).status_code == 403
    assert seen == []
    assert client.get("/api/homehub/devices/duplicates", headers=admin).json()["groups"] == []
    client.post("/api/homehub/devices/duplicates/hide", headers=admin,
                json={"hide_domain": "matter", "keep_domain": "wiz", "evil": 1})
    client.post("/api/homehub/devices/duplicates/unhide", headers=admin, json={"domain": "matter", "evil": 1})
    client.patch("/api/homehub/devices/d1", headers=admin, json={"hidden": True, "evil": 1})
    assert seen == [("GET", "/hub/devices/duplicates", None),
                    ("POST", "/hub/devices/duplicates/hide", {"hide_domain": "matter", "keep_domain": "wiz"}),
                    ("POST", "/hub/devices/duplicates/unhide", {"domain": "matter"}),
                    ("PATCH", "/hub/devices/d1", {"hidden": True})]


def test_combine_routes_are_for_admins_and_pass_only_their_fields(monkeypatch):
    client, seen = _hub_routes(monkeypatch)
    admin = {"Authorization": "Bearer admin-key"}
    for action in ("combine", "uncombine", "swap", "rule"):
        r = client.post(f"/api/homehub/devices/duplicates/{action}", headers={"Authorization": "Bearer kid"})
        assert r.status_code == 403
    assert seen == []
    client.post("/api/homehub/devices/duplicates/combine", headers=admin,
                json={"main": "m1", "fallback": "w1", "evil": 1})
    client.post("/api/homehub/devices/duplicates/combine", headers=admin,
                json={"main_domain": "matter", "fallback_domain": "wiz"})
    client.post("/api/homehub/devices/duplicates/uncombine", headers=admin, json={"device": "m1", "main": "x"})
    client.post("/api/homehub/devices/duplicates/swap", headers=admin, json={"device": "m1"})
    client.post("/api/homehub/devices/duplicates/rule", headers=admin, json={"clear": ["matter", "wiz"]})
    assert client.post("/api/homehub/devices/duplicates/explode", headers=admin, json={}).status_code == 404
    assert seen == [("POST", "/hub/devices/duplicates/combine", {"main": "m1", "fallback": "w1"}),
                    ("POST", "/hub/devices/duplicates/combine", {"main_domain": "matter", "fallback_domain": "wiz"}),
                    ("POST", "/hub/devices/duplicates/uncombine", {"device": "m1"}),
                    ("POST", "/hub/devices/duplicates/swap", {"device": "m1"}),
                    ("POST", "/hub/devices/duplicates/rule", {"clear": ["matter", "wiz"]})]

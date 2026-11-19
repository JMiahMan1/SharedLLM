"""Entities the hub hides (a duplicate device's copy, homehub duplicates.py) on execution's side.

``/discovery/entities`` leaves them out of the lists Jarvis browses unless ``include_hidden`` (lookups by id),
and then marks them ``_hidden``; the capability index leaves them out of room and ability lookups but still
answers for them by id. Every name here is made up.
"""
import contextlib
import os

from fastapi.testclient import TestClient

from services.execution import ha_client
from services.execution.capability_index import CapabilityIndex
from services.execution.main import app

client = TestClient(app)
H = {"X-Internal-Secret": os.environ.get("INTERNAL_SECRET", "")}


def _async(value):
    async def _coro(*_a, **_k):
        return value

    return _coro


def _states():
    return [{"entity_id": "light.pantry_lamp", "state": "on", "attributes": {}},
            {"entity_id": "light.pantry_lamp_wiz", "state": "on", "attributes": {}}]


def _patch(monkeypatch, hidden):
    monkeypatch.setattr(ha_client, "get_states", lambda *a, **k: _async(_states())())
    monkeypatch.setattr(ha_client, "get_areas", lambda *a, **k: _async({})())
    monkeypatch.setattr(ha_client, "get_hidden_entities", lambda *a, **k: _async(hidden)())


def test_discovery_leaves_hidden_entities_out(monkeypatch):
    _patch(monkeypatch, {"light.pantry_lamp"})
    resp = client.get("/discovery/entities", headers=H, params={"ha_url": "http://hub.test", "ha_token": "t"})
    assert resp.status_code == 200
    assert [e["entity_id"] for e in resp.json()["entities"]] == ["light.pantry_lamp_wiz"]


def test_discovery_keeps_and_marks_them_for_lookups_by_id(monkeypatch):
    _patch(monkeypatch, {"light.pantry_lamp"})
    resp = client.get("/discovery/entities", headers=H,
                      params={"ha_url": "http://hub.test", "ha_token": "t", "include_hidden": "1"})
    by_id = {e["entity_id"]: e for e in resp.json()["entities"]}
    assert by_id["light.pantry_lamp"]["attributes"]["_hidden"] is True
    assert "_hidden" not in by_id["light.pantry_lamp_wiz"]["attributes"]


def test_nothing_hidden_changes_nothing(monkeypatch):
    _patch(monkeypatch, set())
    resp = client.get("/discovery/entities", headers=H, params={"ha_url": "http://hub.test", "ha_token": "t"})
    assert len(resp.json()["entities"]) == 2


def test_discovery_names_the_hub_that_answered(monkeypatch):
    """RAG keeps each hub's devices apart, so the answer says which hub it came from."""
    _patch(monkeypatch, set())
    monkeypatch.setattr("services.config.HOMEHUB_URL", "http://hub.test")
    resp = client.get("/discovery/entities", headers=H, params={"ha_url": "http://hub.test/", "ha_token": "t"})
    assert resp.json()["source"] == "homehub"

    monkeypatch.setattr("services.config.HOMEHUB_URL", "http://other-hub.test")
    resp = client.get("/discovery/entities", headers=H, params={"ha_url": "http://hub.test", "ha_token": "t"})
    assert resp.json()["source"] == "home_assistant"


class _Resp:
    def __init__(self, status, data):
        self.status = status
        self._data = data

    def raise_for_status(self):
        if self.status >= 400:
            raise RuntimeError(f"HTTP {self.status}")

    async def json(self, *a, **k):
        return self._data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _session(monkeypatch, status, data):
    class _Session:
        def get(self, url, **kwargs):
            assert url == "http://hub.test/api/homehub/hidden_entities"
            return _Resp(status, data)

    @contextlib.asynccontextmanager
    async def fake(_url, verify=True):
        yield _Session()

    monkeypatch.setattr(ha_client, "_ha_session", fake)
    monkeypatch.setattr(ha_client.ha_health, "skip_reason", lambda _u: None)


async def test_the_hidden_list_from_the_hub(monkeypatch):
    _session(monkeypatch, 200, {"entity_ids": ["light.pantry_lamp", 7]})
    assert await ha_client.get_hidden_entities("http://hub.test", "t") == {"light.pantry_lamp"}


async def test_a_plain_home_assistant_hides_nothing(monkeypatch):
    _session(monkeypatch, 404, {"message": "Not found"})
    assert await ha_client.get_hidden_entities("http://hub.test", "t") == set()


async def test_a_failing_list_shows_every_copy(monkeypatch):
    _session(monkeypatch, 500, None)
    assert await ha_client.get_hidden_entities("http://hub.test", "t") == set()


def test_the_capability_index_skips_hidden_copies_in_rooms_but_answers_by_id():
    idx = CapabilityIndex([
        {"entity_id": "light.pantry_lamp", "domain": "light", "area_id": "pantry", "device_id": "m1",
         "abilities": ["power", "dim"], "available": True, "details": {"hidden": True}},
        {"entity_id": "light.pantry_lamp_wiz", "domain": "light", "area_id": "pantry", "device_id": "w1",
         "abilities": ["power", "dim"], "available": True, "details": {}},
        {"entity_id": "media_player.pantry_speaker", "domain": "media_player", "area_id": "pantry",
         "abilities": ["play_media"], "available": True, "details": {"hidden": True}},
    ], {}, [{"area_id": "pantry", "name": "Pantry"}])
    assert idx.in_area("pantry", "power") == ["light.pantry_lamp_wiz"]
    assert idx.with_ability("dim") == ["light.pantry_lamp_wiz"]
    assert idx.nearby("light.pantry_lamp_wiz") == []
    assert idx.media_targets("pantry") == []
    assert idx.hidden("light.pantry_lamp") and not idx.hidden("light.pantry_lamp_wiz")
    assert idx.has("light.pantry_lamp", "dim") and idx.area_of("light.pantry_lamp") == "pantry"


def test_discovery_carries_the_hub_areas_aliases(monkeypatch):
    from services.execution import capability_index

    _patch(monkeypatch, set())
    idx = CapabilityIndex([{"entity_id": "light.pantry_lamp", "area_id": "area_9"}], {},
                          [{"area_id": "area_9", "name": "Pantry", "aliases": ["Larder"]}])

    async def routing_index(ha_url, ha_token, purpose):
        return idx

    monkeypatch.setattr(capability_index, "routing_index", routing_index)
    by_id = {e["entity_id"]: e for e in client.get(
        "/discovery/entities", headers=H, params={"ha_url": "http://hub.test", "ha_token": "t"}).json()["entities"]}
    assert by_id["light.pantry_lamp"]["attributes"]["area_aliases"] == ["Larder"]
    assert "area_aliases" not in by_id["light.pantry_lamp_wiz"]["attributes"]

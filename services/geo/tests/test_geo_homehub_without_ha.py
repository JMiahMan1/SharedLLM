"""Geo with the Home Assistant integration off and HomeHub on (integrations step 1).

A blank HA_URL used to answer 500 "HA_URL not resolved from Identity" for zones,
people history and location pushes even though HomeHub could answer them. Now
geo reaches Home Assistant while it is set up, else HomeHub with the hub's own
login, and says 503 only when neither is set up.
"""
from __future__ import annotations

import contextlib

import pytest
from fastapi.testclient import TestClient

HUB = "http://hub.test:8123"

@pytest.fixture
def config():
    """services.config as the code sees it now (some tests re-import that module)."""
    from services import config as current

    return current



class _Resp:
    def __init__(self, status, body):
        self.status, self._body = status, body

    async def json(self, content_type=None):
        return self._body

    async def text(self):
        return str(self._body)


class _Client:
    def __init__(self, calls, reply):
        self.calls, self.reply = calls, reply

    @contextlib.asynccontextmanager
    async def get(self, url, headers=None, params=None):
        self.calls.append(("GET", url, headers))
        yield _Resp(200, self.reply)

    @contextlib.asynccontextmanager
    async def post(self, url, headers=None, json=None):
        self.calls.append(("POST", url, headers))
        yield _Resp(200, [])


def _patch(monkeypatch, reply=None, *, homehub=HUB):
    import services.geo.main as geo
    from services import config

    calls: list = []
    monkeypatch.setattr(geo, "HA_URL", "")
    monkeypatch.setattr(geo, "HA_TOKEN", None)
    monkeypatch.setattr(config, "HOMEHUB_URL", homehub)

    @contextlib.asynccontextmanager
    async def fake_client():
        yield _Client(calls, reply)

    async def nothing(*_a, **_k):
        return None

    monkeypatch.setattr(geo, "get_client_insecure", fake_client)
    monkeypatch.setattr(geo, "get_redis", nothing)
    monkeypatch.setattr(geo, "record_point", nothing)
    monkeypatch.setattr(geo, "_save_phone_status", nothing)
    return geo, calls, TestClient(geo.app, headers={"X-Internal-Secret": config.INTERNAL_SECRET})


ZONE = {"entity_id": "zone.home", "state": "0",
        "attributes": {"friendly_name": "Home", "latitude": 1.0, "longitude": 2.0, "radius": 50}}


HUB_HOME = {"id": "home", "entity_id": "zone.home", "name": "Home", "latitude": 1.0, "longitude": 2.0,
            "radius": 50, "passive": False, "owner": None, "owner_name": None, "visibility": "family",
            "important": False, "notify": [], "pin": ""}


class _HubPlaces:
    """HomeHub's /hub/zones as geo's hub_places asks for it."""

    def __init__(self, calls, zones):
        self.calls, self.zones = calls, zones

    @contextlib.asynccontextmanager
    async def request(self, method, url, params=None, json=None, headers=None, timeout=None):
        self.calls.append((method, url, params, headers))
        yield _Resp(200, {"zones": self.zones})


def _hub_places(monkeypatch, calls, zones):
    from services.geo import hub_places

    monkeypatch.setattr(hub_places, "get_client", lambda: _HubPlaces(calls, zones))


def test_places_come_from_homehub_with_the_internal_secret(monkeypatch, config):
    _geo, calls, client = _patch(monkeypatch)
    _hub_places(monkeypatch, calls, [HUB_HOME])
    r = client.get("/zones")
    assert r.status_code == 200, r.text
    assert [f["properties"]["entity_id"] for f in r.json()["features"]] == ["zone.home"]
    method, url, params, headers = calls[0]
    assert (method, url, params) == ("GET", f"{HUB}/hub/zones", {"all": "1"})
    assert headers["Authorization"] == f"Bearer {config.INTERNAL_SECRET}"


def test_history_comes_from_homehub_when_ha_is_blank(monkeypatch):
    rows = [[{"entity_id": "device_tracker.a_phone", "attributes": {"latitude": 1.0, "longitude": 2.0},
              "last_updated": "2026-01-01T00:00:00+00:00"}]]
    _geo, calls, client = _patch(monkeypatch, rows)
    r = client.get("/people/device_tracker.a_phone/history")
    assert r.status_code == 200, r.text
    assert r.json()["points"][0]["lat"] == 1.0
    assert calls[0][1] == f"{HUB}/api/history/period"


def test_location_push_goes_to_homehub_when_ha_is_blank(monkeypatch):
    _geo, calls, client = _patch(monkeypatch)
    r = client.post("/people/device_tracker.a_phone/see", json={"latitude": 1.0, "longitude": 2.0})
    assert r.status_code == 200, r.text
    assert calls[-1][1] == f"{HUB}/api/services/device_tracker/see"


def test_people_read_from_homehub_when_ha_is_blank(monkeypatch):
    person = {"entity_id": "person.a", "state": "home",
              "attributes": {"friendly_name": "A", "latitude": 1.0, "longitude": 2.0}}
    _geo, calls, client = _patch(monkeypatch, [person])
    r = client.get("/people", params={"is_admin": "true"})
    assert r.status_code == 200, r.text
    assert any(c[1] == f"{HUB}/api/states" for c in calls)


def test_no_homehub_means_no_places_and_says_so(monkeypatch):
    """HomeHub is the only store of places: with none set up there are none, never a second store."""
    _geo, calls, client = _patch(monkeypatch, homehub="")
    _hub_places(monkeypatch, calls, [HUB_HOME])
    r = client.get("/zones")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["hub_zones"] == "unavailable" and body["features"] == []
    assert "HomeHub is not set up" in body["message"]
    assert calls == []


def test_no_hub_at_all_is_a_clear_503_and_nothing_is_called(monkeypatch):
    geo, calls, client = _patch(monkeypatch, [ZONE], homehub="")
    for r in (client.get("/people/device_tracker.a_phone/history"),
              client.post("/people/device_tracker.a_phone/see", json={"latitude": 1.0, "longitude": 2.0})):
        assert r.status_code == 503, r.text
        assert "No home hub is set up" in r.json()["detail"]
        assert "HA_URL" not in r.json()["detail"]
    assert calls == []
    assert geo._hub_kind() is None


def test_ha_still_wins_for_people_while_it_is_set_up_but_places_are_homehubs(monkeypatch):
    geo, calls, client = _patch(monkeypatch, [ZONE])
    monkeypatch.setattr(geo, "HA_URL", "http://ha.test:8123")
    monkeypatch.setattr(geo, "HA_TOKEN", "made-up-ha-token")
    _hub_places(monkeypatch, calls, [HUB_HOME])
    assert client.get("/people", params={"is_admin": "true"}).status_code == 200
    assert calls[0][1] == "http://ha.test:8123/api/states"
    assert geo._hub_kind() == "home_assistant"
    calls.clear()
    assert client.get("/zones").status_code == 200
    assert [c[1] for c in calls] == [f"{HUB}/hub/zones"]  # Home Assistant's zones are not a store of places

"""Geo's two writes/reads against the hub's HA API use shapes HA (and homehub) accept."""
from __future__ import annotations

import contextlib

from fastapi.testclient import TestClient

from services.config import INTERNAL_SECRET


class _Resp:
    def __init__(self, status, body):
        self.status, self._body = status, body

    async def json(self):
        return self._body

    async def text(self):
        return str(self._body)


class _Client:
    def __init__(self, calls, reply):
        self.calls, self.reply = calls, reply

    @contextlib.asynccontextmanager
    async def get(self, url, headers=None, params=None):
        self.calls.append(("GET", url, params))
        yield _Resp(200, self.reply)

    @contextlib.asynccontextmanager
    async def post(self, url, headers=None, json=None):
        self.calls.append(("POST", url, json))
        yield _Resp(200, [])


def _patch(monkeypatch, reply=None):
    import services.geo.main as geo

    calls = []
    monkeypatch.setattr(geo, "HA_URL", "http://hub.example.com:8123")
    monkeypatch.setattr(geo, "HA_TOKEN", "tok")

    @contextlib.asynccontextmanager
    async def fake_client():
        yield _Client(calls, reply)

    monkeypatch.setattr(geo, "get_client_insecure", fake_client)
    return geo, calls


def test_trip_history_reads_positions_from_the_rest_history(monkeypatch):
    rows = [[
        {"entity_id": "device_tracker.alex_phone", "state": "not_home", "attributes": {"latitude": 1.0, "longitude": 2.0},
         "last_changed": "2026-01-01T00:00:00+00:00", "last_updated": "2026-01-01T00:00:00+00:00"},
        {"entity_id": "device_tracker.alex_phone", "state": "not_home", "attributes": {"latitude": 1.5, "longitude": 2.5},
         "last_changed": "2026-01-01T00:00:00+00:00", "last_updated": "2026-01-01T00:01:00+00:00"},
    ]]
    geo, calls = _patch(monkeypatch, rows)
    client = TestClient(geo.app, headers={"X-Internal-Secret": INTERNAL_SECRET})
    r = client.get("/people/device_tracker.alex_phone/history")
    assert r.status_code == 200
    assert [p["lat"] for p in r.json()["points"]] == [1.0, 1.5]
    assert r.json()["points"][1]["t"] == "2026-01-01T00:01:00+00:00"
    assert "minimal_response" not in calls[0][2]  # HA's minimal rows carry no position


def test_see_sends_only_device_tracker_see_keys(monkeypatch):
    geo, calls = _patch(monkeypatch)

    async def nothing(*_a, **_k):
        return None

    monkeypatch.setattr(geo, "get_redis", nothing)
    monkeypatch.setattr(geo, "record_point", nothing)
    monkeypatch.setattr(geo, "_save_phone_status", nothing)
    client = TestClient(geo.app, headers={"X-Internal-Secret": INTERNAL_SECRET})
    r = client.post("/people/device_tracker.alex_phone/see",
                    json={"latitude": 1.0, "longitude": 2.0, "gps_accuracy": 5, "speed": 3.5, "bearing": 90})
    assert r.status_code == 200, r.text
    _method, url, body = calls[-1]
    assert url.endswith("/api/services/device_tracker/see")
    assert "type" not in body and "speed" not in body and "course" not in body
    assert body["dev_id"] == "alex_phone" and body["gps"] == [1.0, 2.0]
    assert body["attributes"] == {"speed": 3.5, "course": 90}

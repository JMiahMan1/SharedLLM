"""Room detection on the gateway: beacon reports, the room map proxy, and who sees a room.

Fakes only: the hub is phone_routes._hub, Redis a dict, identity's raw-data rule a dict.
Alex and Sam are the parents, Robin their child.
"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from services.gateway import main as gateway_main
from services.gateway import notices, phone_routes, room_presence
from services.gateway.main import app

UUID = "00000000-0000-4000-8000-000000000001"
ADMIN = {"Authorization": "Bearer admin-key"}
KID = {"Authorization": "Bearer kid-key"}
ENTRY = "01ABCDEFGHJKMNPQRSTVWXYZ01"
PHONES = [
    {"entry_id": ENTRY, "name": "Robin's Phone", "kind": "jarvis", "jarvis_user": "robin", "jarvis_fallback": True,
     "app": {"model": "Pixel 9"}, "sensors": [{"entity_id": "sensor.robin_s_phone_room", "state": "Den"}]},
    {"entry_id": "02ABCDEFGHJKMNPQRSTVWXYZ02", "name": "Sam's Phone", "kind": "companion", "jarvis_user": "sam",
     "app": {"model": "Pixel 8"}, "sensors": []},
]
REPORT = {"uuid": UUID, "major": 1, "minor": 2, "tx_power": -59, "tx_power_level": "low", "state": "advertising",
          "reason": None, "device_name": "Pixel 9"}


class FakeRedis:
    def __init__(self):
        self.data: dict[str, str] = {}
        self.fail = False

    async def set(self, key, value, ex=None):
        if self.fail:
            raise ConnectionError("redis down")
        self.data[key] = value

    async def mget(self, keys):
        if self.fail:
            raise ConnectionError("redis down")
        return [self.data.get(k) for k in keys]


@pytest.fixture
def env(monkeypatch):
    redis = FakeRedis()
    seen: list = []
    answers: dict = {}

    @asynccontextmanager
    async def fake_redis(r=None):
        yield redis

    async def hub(method, path, *, json=None):
        seen.append((method, path, json))
        return answers.get((method, path), (200, {"phones": PHONES, "settings": {}}))

    async def authenticated(request):
        auth = request.headers.get("Authorization")
        if auth == "Bearer admin-key":
            return "sam"
        if auth == "Bearer kid-key":
            return "robin"
        raise HTTPException(status_code=401, detail="Authentication required")

    async def admin(request):
        auth = request.headers.get("Authorization")
        if auth == "Bearer admin-key":
            return "sam"
        raise HTTPException(status_code=403 if auth else 401, detail="Admin only")

    monkeypatch.setattr(notices, "_redis", fake_redis)
    monkeypatch.setattr(phone_routes, "_hub", hub)
    monkeypatch.setattr(gateway_main, "_require_authenticated", authenticated)
    monkeypatch.setattr(gateway_main, "_require_admin", admin)
    return {"redis": redis, "seen": seen, "answers": answers}


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


class TestBeaconReport:
    def test_registers_on_the_users_phone_and_is_stored(self, client, env):
        r = client.put("/api/phone/ble_beacon", headers=KID, json=REPORT)
        assert r.status_code == 200
        assert r.json() == {"stored": True, "hub": {"registered": True, "detail": "registered on Robin's Phone"}}
        assert env["seen"][-1] == ("PATCH", f"/hub/mobile_app/phones/{ENTRY}", {"app_data": {"ble_beacon": {
            "uuid": UUID, "major": 1, "minor": 2, "tx_power": -59, "state": "advertising"}}})
        stored = json.loads(env["redis"].data["phone_beacon:robin"])
        assert stored["hub_registered"] is True and stored["state"] == "advertising"

    def test_a_failure_is_reported_with_its_reason(self, client, env):
        body = {**REPORT, "state": "unavailable", "reason": "this phone's Bluetooth chip cannot advertise"}
        assert client.put("/api/phone/ble_beacon", headers=KID, json=body).status_code == 200
        sent = env["seen"][-1][2]["app_data"]["ble_beacon"]
        assert sent["state"] == "unavailable" and sent["reason"] == "this phone's Bluetooth chip cannot advertise"

    def test_switched_off_clears_the_hub_beacon(self, client, env):
        client.put("/api/phone/ble_beacon", headers=KID, json={**REPORT, "state": "disabled"})
        assert env["seen"][-1][2] == {"app_data": {"ble_beacon": None}}

    def test_a_hub_that_refuses_is_said_not_swallowed(self, client, env):
        env["answers"][("PATCH", f"/hub/mobile_app/phones/{ENTRY}")] = (400, {"message": "unknown option: app_data"})
        r = client.put("/api/phone/ble_beacon", headers=KID, json=REPORT).json()
        assert r["hub"] == {"registered": False, "detail": "the device hub refused the beacon: unknown option: app_data"}
        stored = json.loads(env["redis"].data["phone_beacon:robin"])
        assert stored["hub_registered"] is False and "unknown option" in stored["hub_detail"]

    def test_no_phone_on_the_hub_is_said(self, client, env):
        env["answers"][("GET", "/hub/mobile_app/phones")] = (200, {"phones": [], "settings": {}})
        r = client.put("/api/phone/ble_beacon", headers=KID, json=REPORT).json()
        assert r["hub"]["registered"] is False and "no phone for robin" in r["hub"]["detail"]

    def test_storage_down_is_a_503(self, client, env):
        env["redis"].fail = True
        assert client.put("/api/phone/ble_beacon", headers=KID, json=REPORT).status_code == 503

    @pytest.mark.parametrize("bad", [
        {"uuid": "not-a-uuid"}, {"major": 70000}, {"minor": -1}, {"major": True}, {"tx_power": 99},
        {"state": "maybe"}, {"tx_power_level": "loud"}, {"state": "unavailable", "reason": None},
    ])
    def test_bad_reports_are_422(self, client, env, bad):
        assert client.put("/api/phone/ble_beacon", headers=KID, json={**REPORT, **bad}).status_code == 422
        assert env["seen"] == []

    def test_anonymous_is_401(self, client, env):
        assert client.put("/api/phone/ble_beacon", json=REPORT).status_code == 401


def test_pick_phone_prefers_this_model_then_the_jarvis_phone():
    two = [{**PHONES[0]}, {**PHONES[0], "entry_id": "x", "kind": "companion", "app": {"model": "Old"}}]
    assert room_presence.pick_phone(two, "robin", "Pixel 9")[0]["entry_id"] == ENTRY
    assert room_presence.pick_phone(two, "robin", "Unknown")[0]["entry_id"] == ENTRY
    both_companion = [{**p, "kind": "companion"} for p in two]
    phone, why = room_presence.pick_phone(both_companion, "robin", None)
    assert phone is None and "2 phones" in why


class TestPhonesListing:
    def test_each_phone_carries_its_users_last_report(self, client, env):
        client.put("/api/phone/ble_beacon", headers=KID, json=REPORT)
        phones = client.get("/api/homehub/phones", headers=ADMIN).json()["phones"]
        robin = next(p for p in phones if p["jarvis_user"] == "robin")
        sam = next(p for p in phones if p["jarvis_user"] == "sam")
        assert robin["beacon"]["uuid"] == UUID and robin["beacon"]["hub_registered"] is True
        assert sam["beacon"] is None

    def test_unreadable_reports_are_said(self, client, env):
        env["redis"].fail = True
        body = client.get("/api/homehub/phones", headers=ADMIN).json()
        assert body["beacons_error"] and "beacon" not in body["phones"][0]


class TestRoomMap:
    @pytest.mark.parametrize("verb,path", [
        ("get", "/api/homehub/bluetooth/map"), ("delete", "/api/homehub/bluetooth/map?confirm=1"),
        ("delete", "/api/homehub/bluetooth/map/den"), ("post", "/api/homehub/bluetooth/map/sessions"),
        ("get", "/api/homehub/bluetooth/map/sessions/s1"), ("delete", "/api/homehub/bluetooth/map/sessions/s1"),
    ])
    def test_admins_only(self, client, env, verb, path):
        assert client.request(verb.upper(), path, headers=KID).status_code == 403
        assert client.request(verb.upper(), path).status_code == 401
        assert env["seen"] == []

    def test_a_pass_through_the_hub(self, client, env):
        env["answers"][("POST", "/hub/bluetooth/map/sessions")] = (
            200, {"session_id": "s1", "area_id": "den", "tracker": "sensor.robin_s_phone", "ends_at": "x"})
        r = client.post("/api/homehub/bluetooth/map/sessions", headers=ADMIN,
                        json={"area_id": "den", "tracker": "sensor.robin_s_phone", "duration_s": 60})
        assert r.status_code == 200 and r.json()["session_id"] == "s1"
        env["answers"][("GET", "/hub/bluetooth/map/sessions/s1")] = (
            200, {"status": "recording", "samples": 12, "scanners_heard": 2, "seconds_left": 40})
        assert client.get("/api/homehub/bluetooth/map/sessions/s1", headers=ADMIN).json()["samples"] == 12
        client.delete("/api/homehub/bluetooth/map/sessions/s1", headers=ADMIN)
        assert env["seen"][-1][:2] == ("DELETE", "/hub/bluetooth/map/sessions/s1")

    def test_a_beacon_id_is_a_tracker(self, client, env):
        env["answers"][("POST", "/hub/bluetooth/map/sessions")] = (200, {"session_id": "s2"})
        r = client.post("/api/homehub/bluetooth/map/sessions", headers=ADMIN,
                        json={"area_id": "loft", "tracker": f"{UUID.upper()}-1-2", "duration_s": 30})
        assert r.status_code == 200
        assert env["seen"][-1][2]["tracker"] == f"{UUID}-1-2"

    def test_not_heard_is_passed_through_as_409(self, client, env):
        env["answers"][("POST", "/hub/bluetooth/map/sessions")] = (
            409, {"message": "sensor.robin_s_phone has not been heard by any scanner in the last 30 s"})
        r = client.post("/api/homehub/bluetooth/map/sessions", headers=ADMIN,
                        json={"area_id": "den", "tracker": "sensor.robin_s_phone", "duration_s": 60})
        assert r.status_code == 409 and "not been heard" in r.json()["detail"]

    @pytest.mark.parametrize("body", [
        {"area_id": "den", "tracker": "sensor.x", "duration_s": 5},
        {"area_id": "../x", "tracker": "sensor.x", "duration_s": 60},
        {"area_id": "den", "tracker": "light.x", "duration_s": 60},
        {"area_id": "den", "tracker": "sensor.x", "duration_s": "60"},
    ])
    def test_bad_starts_never_reach_the_hub(self, client, env, body):
        assert client.post("/api/homehub/bluetooth/map/sessions", headers=ADMIN, json=body).status_code == 400
        assert env["seen"] == []

    def test_clearing_everything_needs_confirmation(self, client, env):
        assert client.delete("/api/homehub/bluetooth/map", headers=ADMIN).status_code == 400
        assert env["seen"] == []
        env["answers"][("DELETE", "/hub/bluetooth/map?confirm=1")] = (200, {"cleared": 2})
        assert client.delete("/api/homehub/bluetooth/map?confirm=1", headers=ADMIN).json() == {"cleared": 2}

    def test_the_hub_away_is_a_502(self, client, env):
        env["answers"][("GET", "/hub/bluetooth/map")] = (500, None)
        assert client.get("/api/homehub/bluetooth/map", headers=ADMIN).status_code == 502


# -- who sees a room --------------------------------------------------------------------------

RULES = {("alex", "robin"): True, ("sam", "robin"): True, ("alex", "sam"): True, ("sam", "alex"): True}


def _people():
    def person(name, room):
        return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [0, 0]},
                "properties": {"entity_id": f"person.{name}", "state": "home", "room": room,
                               "room_area_id": room.lower()}}

    return {"type": "FeatureCollection", "features": [
        person("alex", "Den"), person("sam", "Loft"), person("robin", "Den"),
        {"type": "Feature", "properties": {"entity_id": "device_tracker.robin_pixel", "state": "home"}},
    ]}


def _rooms(payload):
    return {f["properties"]["entity_id"]: f["properties"].get("room") for f in payload["features"]}


class TestWhoSeesARoom:
    @pytest.fixture
    def rule(self):
        asked: list = []

        async def allowed(viewer, subject):
            asked.append((viewer, subject))
            return RULES.get((viewer, subject), False)

        return allowed, asked

    @pytest.mark.asyncio
    async def test_a_parent_sees_the_child_and_the_other_parent(self, rule):
        allowed, _ = rule
        out = _rooms(await room_presence.filter_people_rooms(_people(), "alex", allowed))
        assert out == {"person.alex": "Den", "person.sam": "Loft", "person.robin": "Den",
                       "device_tracker.robin_pixel": None}

    @pytest.mark.asyncio
    async def test_a_child_sees_only_their_own(self, rule):
        allowed, asked = rule
        out = await room_presence.filter_people_rooms(_people(), "robin", allowed)
        assert _rooms(out) == {"person.alex": None, "person.sam": None, "person.robin": "Den",
                               "device_tracker.robin_pixel": None}
        alex = out["features"][0]["properties"]
        assert "room" not in alex and "room_area_id" not in alex
        assert ("robin", "robin") not in asked  # their own needs no question

    @pytest.mark.asyncio
    async def test_another_adult_sees_only_their_own(self, rule):
        allowed, _ = rule
        payload = _people()
        payload["features"].append({"type": "Feature", "properties": {
            "entity_id": "person.casey", "room": "Loft", "room_area_id": "loft"}})
        out = _rooms(await room_presence.filter_people_rooms(payload, "casey", allowed))
        assert out["person.casey"] == "Loft"
        assert out["person.robin"] is None and out["person.alex"] is None

    @pytest.mark.asyncio
    async def test_identity_down_hides_every_room_but_your_own(self):
        async def broken(viewer, subject):
            raise RuntimeError("identity down")

        out = _rooms(await room_presence.filter_people_rooms(_people(), "sam", broken))
        assert out == {"person.alex": None, "person.sam": "Loft", "person.robin": None,
                       "device_tracker.robin_pixel": None}

    @pytest.mark.asyncio
    async def test_an_unknown_room_is_not_passed_on(self, rule):
        allowed, _ = rule
        payload = _people()
        payload["features"][0]["properties"]["room"] = "unknown"
        assert _rooms(await room_presence.filter_people_rooms(payload, "alex", allowed))["person.alex"] is None

    def test_the_people_route_filters_rooms_for_the_viewer(self, monkeypatch):
        async def acting(request):
            return {"user": "robin", "is_admin": False}

        class Resp:
            status = 200

            async def json(self):
                return _people()

        class Client:
            async def get(self, *a, **k):
                return Resp()

        @asynccontextmanager
        async def fake_client():
            yield Client()

        async def allowed(viewer, subject):
            return RULES.get((viewer, subject), False)

        monkeypatch.setattr(gateway_main, "_acting_identity", acting)
        monkeypatch.setattr(gateway_main, "shared_http_client", fake_client)
        monkeypatch.setattr(room_presence, "identity_allows", allowed)
        body = TestClient(app).get("/api/geo/people").json()
        assert _rooms(body) == {"person.alex": None, "person.sam": None, "person.robin": "Den",
                                "device_tracker.robin_pixel": None}

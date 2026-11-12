"""The hub core, store, auth and the HA-compatible API, with a fake driver."""
from __future__ import annotations

import json
import time

import jwt
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from services.homehub.api import build_app
from services.homehub.auth import Auth
from services.homehub.core import ConfigEntry, DeviceUnavailable, Driver, EntityEntry, Hub
from services.homehub.runtime import Runtime
from services.homehub.store import Store

SECRET = "internal-secret"


class FakeLight(Driver):
    platform = "fake"
    poll_interval = None

    def __init__(self, hub, entry):
        super().__init__(hub, entry)
        self.calls = []

    async def async_setup(self):
        for e in self.entities:
            self.publish(e, "off", {"brightness": None})

    async def async_call(self, entity, domain, service, data):
        self.calls.append((entity.entity_id, domain, service, data))
        if entity.entity_id == "light.broken":
            raise DeviceUnavailable("bulb did not answer")
        on = service == "turn_on" or (service == "toggle" and self.hub.states.get(entity.entity_id).state == "off")
        self.publish(entity, "on" if on else "off", {"brightness": data.get("brightness", 255) if on else None})


@pytest.fixture
def setup(tmp_path):
    store = Store(str(tmp_path / "hub.db"), Fernet.generate_key().decode())
    hub = Hub(time_zone="America/Phoenix", location={"latitude": 33.4, "longitude": -111.9})
    hub.store = store
    entry = ConfigEntry("e1", "fake", "Fake lights", data={"host": "10.0.0.1"})
    hub.config_entries["e1"] = entry
    from services.homehub.core import Area, DeviceEntry

    hub.areas["kitchen"] = Area("kitchen", "Kitchen")
    hub.devices["d1"] = DeviceEntry(id="d1", name="Kitchen Bulb", area_id="kitchen", config_entries=["e1"])
    for eid in ("light.kitchen", "light.broken"):
        hub.entities[eid] = EntityEntry(entity_id=eid, platform="fake", unique_id=eid, config_entry_id="e1",
                                        device_id="d1" if eid == "light.kitchen" else None)
    runtime = Runtime(hub, store)
    driver = FakeLight(hub, entry)
    hub.drivers["e1"] = driver
    auth = Auth(store, SECRET)
    return hub, store, runtime, driver, auth


@pytest.fixture
def client(setup):
    _hub, _store, runtime, driver, auth = setup
    app = build_app(runtime, auth)
    with TestClient(app) as c:
        import asyncio

        asyncio.run(driver.async_setup())
        yield c


H = {"Authorization": f"Bearer {SECRET}"}


def test_unauthorized_is_plain_401_like_ha(client):
    r = client.get("/api/states")
    assert r.status_code == 401 and r.text == "401: Unauthorized"
    assert client.get("/api/states", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_states_and_single_state(client):
    r = client.get("/api/states", headers=H)
    assert r.status_code == 200
    ids = {s["entity_id"] for s in r.json()}
    assert ids == {"light.kitchen", "light.broken"}
    one = client.get("/api/states/light.kitchen", headers=H).json()
    assert one["state"] == "off" and one["attributes"]["friendly_name"] == "Kitchen Bulb"
    missing = client.get("/api/states/light.nope", headers=H)
    assert missing.status_code == 404 and missing.json() == {"message": "Entity not found."}


def test_service_call_returns_changed_states(client, setup):
    _hub, _s, _r, driver, _a = setup
    r = client.post("/api/services/light/turn_on", headers=H, json={"entity_id": "light.kitchen", "brightness": 80})
    assert r.status_code == 200
    body = r.json()
    assert body[0]["entity_id"] == "light.kitchen" and body[0]["state"] == "on"
    assert body[0]["attributes"]["brightness"] == 80
    assert driver.calls[-1] == ("light.kitchen", "light", "turn_on", {"brightness": 80})


def test_area_target_resolves_through_the_device(client, setup):
    _hub, _s, _r, driver, _a = setup
    r = client.post("/api/services/light/turn_on", headers=H, json={"target": {"area_id": "kitchen"}})
    assert r.status_code == 200
    assert [c[0] for c in driver.calls] == ["light.kitchen"]


def test_unreachable_device_is_an_error_not_a_silent_success(client):
    r = client.post("/api/services/light/turn_on", headers=H, json={"entity_id": "light.broken"})
    assert r.status_code == 500 and "did not answer" in r.json()["message"]


def test_partial_failure_still_succeeds(client):
    r = client.post("/api/services/light/turn_on", headers=H,
                    json={"entity_id": ["light.kitchen", "light.broken"]})
    assert r.status_code == 200 and r.json()[0]["entity_id"] == "light.kitchen"


def test_homeassistant_toggle_dispatches_by_entity_domain(client):
    r = client.post("/api/services/homeassistant/toggle", headers=H, json={"entity_id": "light.kitchen"})
    assert r.status_code == 200 and r.json()[0]["state"] == "on"


def test_registered_service_with_response(client, setup):
    hub = setup[0]

    async def handler(hub_, call):
        return {"echo": call.data.get("x")}

    hub.register_service("demo", "echo", handler)
    r = client.post("/api/services/demo/echo?return_response", headers=H, json={"x": 3})
    assert r.json()["service_response"] == {"echo": 3}
    services = {d["domain"]: d["services"] for d in client.get("/api/services", headers=H).json()}
    assert "echo" in services["demo"] and "turn_on" in services["light"]


def test_unknown_service_without_target_is_400(client):
    r = client.post("/api/services/light/turn_on", headers=H, json={})
    assert r.status_code == 400


def test_config_and_config_entries_hide_credentials(client):
    cfg = client.get("/api/config", headers=H).json()
    assert cfg["time_zone"] == "America/Phoenix" and cfg["state"] == "RUNNING"
    entries = client.get("/api/config/config_entries/entry", headers=H).json()
    assert entries[0]["domain"] == "fake"
    assert "data" not in entries[0] and "10.0.0.1" not in json.dumps(entries)


def test_history_and_logbook_come_from_the_recorder(client, setup):
    _hub, _store, runtime, _d, _a = setup
    runtime.start_recording()
    client.post("/api/services/light/turn_on", headers=H, json={"entity_id": "light.kitchen"})
    client.post("/api/services/light/turn_off", headers=H, json={"entity_id": "light.kitchen"})
    hist = client.get("/api/history/period/2020-01-01T00:00:00Z?filter_entity_id=light.kitchen", headers=H).json()
    assert [s["state"] for s in hist[0]] == ["on", "off"]
    log = client.get("/api/logbook/2020-01-01T00:00:00Z?entity=light.kitchen", headers=H).json()
    assert [e["state"] for e in log] == ["on", "off"]


def test_store_round_trips_and_seals_credentials(tmp_path):
    key = Fernet.generate_key().decode()
    store = Store(str(tmp_path / "s.db"), key)
    store.save_config_entry(ConfigEntry("x", "esphome", "node", data={"noise_psk": "SECRETPSK"}))
    raw = (tmp_path / "s.db").read_bytes()
    assert b"SECRETPSK" not in raw
    again = Store(str(tmp_path / "s.db"), key)
    assert again.load_config_entries()[0].data == {"noise_psk": "SECRETPSK"}
    with pytest.raises(RuntimeError):
        Store(str(tmp_path / "plain.db"), None).save_config_entry(ConfigEntry("y", "esphome", data={"a": 1}))


def test_ha_long_lived_tokens_keep_working(setup):
    _hub, store, _r, _d, auth = setup
    store.set_kv("users", [{"id": "u1", "name": "Jeremiah", "is_owner": True, "is_active": True, "group_ids": []},
                           {"id": "u2", "name": "Kid", "is_active": True, "group_ids": ["system-users"]}])
    store.save_refresh_token("rt1", {"user_id": "u1", "token_type": "long_lived_access_token"}, "k" * 64)
    store.save_refresh_token("rt2", {"user_id": "u2"}, "j" * 64)
    now = int(time.time())
    tok = jwt.encode({"iss": "rt1", "iat": now, "exp": now + 3600 * 24 * 3650}, "k" * 64, algorithm="HS256")
    p = auth.authenticate(tok)
    assert p is not None and p.user_id == "u1" and p.is_admin
    kid = jwt.encode({"iss": "rt2", "iat": now, "exp": now + 60}, "j" * 64, algorithm="HS256")
    assert auth.authenticate(kid).is_admin is False
    forged = jwt.encode({"iss": "rt1", "iat": now, "exp": now + 60}, "wrong-key" * 8, algorithm="HS256")
    assert auth.authenticate(forged) is None
    expired = jwt.encode({"iss": "rt1", "iat": now - 100, "exp": now - 50}, "k" * 64, algorithm="HS256")
    assert auth.authenticate(expired) is None


def test_template_endpoint_needs_admin(client, setup):
    _hub, store, _r, _d, _auth = setup
    store.set_kv("users", [{"id": "u2", "name": "Kid", "is_active": True, "group_ids": []}])
    store.save_refresh_token("rt2", {"user_id": "u2"}, "j" * 64)
    now = int(time.time())
    kid = jwt.encode({"iss": "rt2", "iat": now, "exp": now + 60}, "j" * 64, algorithm="HS256")
    r = client.post("/api/template", headers={"Authorization": f"Bearer {kid}"}, json={"template": "{{ 1 }}"})
    assert r.status_code == 401


def test_websocket_auth_states_events_and_calls(client):
    with client.websocket_connect("/api/websocket") as ws:
        assert ws.receive_json()["type"] == "auth_required"
        ws.send_json({"type": "auth", "access_token": SECRET})
        assert ws.receive_json()["type"] == "auth_ok"
        ws.send_json({"id": 1, "type": "get_states"})
        res = ws.receive_json()
        assert res["success"] and len(res["result"]) == 2
        ws.send_json({"id": 2, "type": "subscribe_events", "event_type": "state_changed"})
        assert ws.receive_json()["success"]
        ws.send_json({"id": 3, "type": "call_service", "domain": "light", "service": "turn_on",
                      "service_data": {"entity_id": "light.kitchen"}})
        got = [ws.receive_json(), ws.receive_json()]
        event = next(m for m in got if m["type"] == "event")
        assert event["id"] == 2
        assert event["event"]["data"]["new_state"]["state"] == "on"
        assert next(m for m in got if m["type"] == "result")["success"]
        ws.send_json({"id": 4, "type": "config/entity_registry/list"})
        assert {e["entity_id"] for e in ws.receive_json()["result"]} == {"light.kitchen", "light.broken"}
        ws.send_json({"id": 5, "type": "config_entries/get"})
        assert ws.receive_json()["result"][0]["domain"] == "fake"


def test_websocket_rejects_bad_token(client):
    with client.websocket_connect("/api/websocket") as ws:
        ws.receive_json()
        ws.send_json({"type": "auth", "access_token": "bad"})
        assert ws.receive_json()["type"] == "auth_invalid"


def test_device_tracker_see_from_the_location_relay(client, setup):
    from services.homehub import builtin

    hub = setup[0]
    builtin.register(hub)
    r = client.post("/api/services/device_tracker/see", headers=H,
                    json={"dev_id": "michele_s_phone", "gps": [33.4, -111.9], "gps_accuracy": 12, "battery": 80,
                          "source_type": "gps", "attributes": {"speed": 3}})
    assert r.status_code == 200
    st = hub.states.get("device_tracker.michele_s_phone")
    assert st.attributes["latitude"] == 33.4 and st.attributes["battery_level"] == 80
    assert st.attributes["speed"] == 3
    bad = client.post("/api/services/device_tracker/see", headers=H, json={"dev_id": "../x"})
    assert bad.status_code >= 400


async def test_service_starts_people_and_zones(tmp_path, monkeypatch):
    """main.Service wires zones and persons the way the drivers define them."""
    import importlib

    monkeypatch.setenv("HOMEHUB_DB", str(tmp_path / "hub.db"))
    monkeypatch.setenv("FERNET_KEY", Fernet.generate_key().decode())
    main = importlib.import_module("services.homehub.main")
    svc = main.Service.__new__(main.Service)
    svc.hub = Hub(location={"latitude": 33.4, "longitude": -111.9, "location_name": "Home"})
    svc.store = Store(str(tmp_path / "hub.db"), Fernet.generate_key().decode())
    svc.store.set_kv("zones", [{"id": "work", "name": "Work", "latitude": 33.5, "longitude": -112.0, "radius": 200}])
    svc.store.set_kv("persons", [{"id": "michele", "name": "Michele", "user_id": "u", "device_trackers": []}])
    errors = []
    monkeypatch.setattr(main.log, "exception", lambda *a, **k: errors.append(a))
    await svc._start_people()
    assert not errors
    assert svc.hub.states.get("zone.home") is not None and svc.hub.states.get("zone.work") is not None

"""Which entities voice and chat may control (Home Assistant's Assist exposure): defaults, choices, import, API.

Every name and id here is made up.
"""
from __future__ import annotations

import json

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from services.homehub import exposure, hardware
from services.homehub.api import build_app
from services.homehub.auth import Auth
from services.homehub.core import ConfigEntry, DeviceEntry, EntityEntry, Hub
from services.homehub.entries import EntryError
from services.homehub.importer import import_config_dir
from services.homehub.store import Store
from services.homehub.tests.test_flows import FakeRuntime

SECRET = "internal-secret"
H = {"Authorization": f"Bearer {SECRET}"}


@pytest.fixture
def env(tmp_path):
    store = Store(str(tmp_path / "hub.db"), Fernet.generate_key().decode())
    hub = Hub(time_zone="Etc/GMT+7")
    hub.store = store
    entry = ConfigEntry("e1", "wiz", "Den", {"host": "192.0.2.21"})
    hub.config_entries["e1"] = entry
    hub.devices["d1"] = DeviceEntry("d1", name="Den lamp", config_entries=["e1"])
    for e in (EntityEntry("light.den_lamp", "wiz", "u1", "e1", "d1"),
              EntityEntry("lock.side_door", "demo", "u2", "e1", "d1"),
              EntityEntry("switch.den_lamp_identify", "wiz", "u3", "e1", "d1", entity_category="config"),
              EntityEntry("switch.hidden_plug", "wiz", "u4", "e1", "d1", hidden_by="user"),
              EntityEntry("sensor.den_temperature", "wiz", "u5", "e1", "d1", original_device_class="temperature"),
              EntityEntry("sensor.den_power", "wiz", "u6", "e1", "d1", original_device_class="power"),
              EntityEntry("binary_sensor.den_window", "wiz", "u7", "e1", "d1", device_class="window"),
              EntityEntry("binary_sensor.den_problem", "wiz", "u8", "e1", "d1", original_device_class="problem")):
        hub.entities[e.entity_id] = e
        store.save_entity(e)
        hub.states.set(e.entity_id, "on", {})
    hub.states.set("sensor.api_humidity", "40", {"device_class": "humidity"})  # no registry row
    return hub, store, FakeRuntime(hub, store)


def test_the_defaults_are_home_assistants(env):
    hub, _store, _rt = env
    assert exposure.is_exposed(hub, "light.den_lamp")
    assert not exposure.is_exposed(hub, "lock.side_door")  # locks are never exposed by default
    assert not exposure.is_exposed(hub, "switch.den_lamp_identify")  # config entity
    assert not exposure.is_exposed(hub, "switch.hidden_plug")
    assert exposure.is_exposed(hub, "sensor.den_temperature")
    assert not exposure.is_exposed(hub, "sensor.den_power")
    assert exposure.is_exposed(hub, "binary_sensor.den_window")
    assert not exposure.is_exposed(hub, "binary_sensor.den_problem")
    assert exposure.is_exposed(hub, "sensor.api_humidity")  # device class from the state
    assert exposure.view(hub, "light.den_lamp") == {"exposed": True, "default": True, "chosen": False}


def test_expose_new_off_exposes_nothing_by_default(env):
    hub, store, _rt = env
    store.set_kv(exposure.KV_KEY, {"expose_new": False})
    assert not exposure.is_exposed(hub, "light.den_lamp")
    exposure.set_exposed(hub, "light.den_lamp", True)
    assert exposure.is_exposed(hub, "light.den_lamp")


def test_a_choice_is_kept_in_registry_options_and_can_be_cleared(env):
    hub, store, _rt = env
    exposure.set_exposed(hub, "light.den_lamp", False)
    assert not exposure.is_exposed(hub, "light.den_lamp")
    saved = {e.entity_id: e for e in store.load_entities()}["light.den_lamp"]
    assert saved.options == {"conversation": {"should_expose": False}}
    exposure.set_exposed(hub, "lock.side_door", True)
    assert exposure.is_exposed(hub, "lock.side_door")
    exposure.set_exposed(hub, "light.den_lamp", None)
    assert hub.entities["light.den_lamp"].options == {}
    assert exposure.is_exposed(hub, "light.den_lamp")


def test_an_entity_without_a_registry_row_keeps_its_choice_in_the_store(env):
    hub, store, _rt = env
    exposure.set_exposed(hub, "sensor.api_humidity", False)
    assert store.get_kv(exposure.KV_KEY)["entities"] == {"sensor.api_humidity": False}
    assert not exposure.is_exposed(hub, "sensor.api_humidity")
    assert "sensor.api_humidity" not in exposure.exposed_entities(hub)
    exposure.set_exposed(hub, "sensor.api_humidity", None)
    assert exposure.is_exposed(hub, "sensor.api_humidity")


def test_set_exposed_refuses_a_non_boolean(env):
    hub, _store, _rt = env
    with pytest.raises(ValueError):
        exposure.set_exposed(hub, "light.den_lamp", "yes")  # type: ignore[arg-type]


async def test_the_device_sheet_sets_it_through_update_entity(env):
    _hub, store, rt = env
    view = await hardware.update_entity(rt, "lock.side_door", {"voice": True})
    assert view["voice"] == {"exposed": True, "default": False, "chosen": True}
    assert {e.entity_id: e for e in store.load_entities()}["lock.side_door"].options["conversation"] == {
        "should_expose": True}
    view = await hardware.update_entity(rt, "lock.side_door", {"voice": None})
    assert view["voice"] == {"exposed": False, "default": False, "chosen": False}
    with pytest.raises(EntryError):
        await hardware.update_entity(rt, "lock.side_door", {"voice": "on"})


def test_the_api_answers_one_entity_or_the_list(env):
    hub, store, rt = env
    exposure.set_exposed(hub, "light.den_lamp", False)
    client = TestClient(build_app(rt, Auth(store, SECRET)))
    assert client.get("/api/homehub/exposed_entities").status_code == 401
    one = client.get("/api/homehub/exposed_entities", params={"entity_id": "light.den_lamp"}, headers=H).json()
    # ``name``: what Jarvis says if it has to refuse (never the entity id).
    assert {k: one[k] for k in ("entity_id", "exposed")} == {"entity_id": "light.den_lamp", "exposed": False}
    assert "name" in one and one["name"] != "light.den_lamp"
    listing = client.get("/api/homehub/exposed_entities", headers=H).json()
    assert listing["assistant"] == "conversation"
    assert listing["entity_ids"] == ["binary_sensor.den_window", "sensor.api_humidity", "sensor.den_temperature"]
    r = client.patch("/hub/entities/light.den_lamp", headers=H, json={"voice": True})
    assert r.status_code == 200 and r.json()["voice"]["exposed"] is True
    rows = {r["entity_id"]: r for r in client.get("/hub/hardware/d1", headers=H).json()["entity_list"]}
    assert rows["light.den_lamp"]["voice"] == {"exposed": True, "default": True, "chosen": True}
    assert rows["lock.side_door"]["voice"] == {"exposed": False, "default": False, "chosen": False}


def test_from_ha_storage_reads_both_shapes():
    settings, choices = exposure.from_ha_storage({
        "assistants": {"conversation": {"expose_new": False}, "cloud.alexa": {"expose_new": True}},
        "exposed_entities": {
            "light.a": {"assistants": {"conversation": {"should_expose": True}}},
            "light.b": {"conversation": {"should_expose": False}},
            "light.c": {"assistants": {"cloud.alexa": {"should_expose": True}}},
            "light.d": "junk"}})
    assert settings == {"expose_new": False}
    assert choices == {"light.a": True, "light.b": False}
    assert exposure.from_ha_storage(None) == ({}, {})


def _storage(d, name, data):
    (d / ".storage" / name).write_text(json.dumps({"version": 1, "key": name, "data": data}))


def test_import_carries_home_assistants_exposure(tmp_path):
    d = tmp_path / "homeassistant"
    (d / ".storage").mkdir(parents=True)
    _storage(d, "core.config", {"time_zone": "Etc/GMT+7"})
    _storage(d, "core.entity_registry", {"entities": [
        {"id": "r1", "entity_id": "light.porch", "platform": "wiz", "unique_id": "a",
         "options": {"conversation": {"should_expose": False}}},
        {"id": "r2", "entity_id": "lock.gate", "platform": "demo", "unique_id": "b"},
        {"id": "r3", "entity_id": "light.hall", "platform": "wiz", "unique_id": "c"}]})
    _storage(d, "homeassistant.exposed_entities", {
        "assistants": {"conversation": {"expose_new": True}},
        "exposed_entities": {
            "light.porch": {"assistants": {"conversation": {"should_expose": True}}},  # the registry row wins
            "lock.gate": {"assistants": {"conversation": {"should_expose": True}}},
            "sensor.legacy": {"assistants": {"conversation": {"should_expose": True}}}}})
    store = Store(str(tmp_path / "hub.db"), Fernet.generate_key().decode())
    report = import_config_dir(store, str(d))
    assert report["voice_exposure_unregistered"] == 1
    hub = Hub()
    hub.store = store
    hub.entities = {e.entity_id: e for e in store.load_entities()}
    assert not exposure.is_exposed(hub, "light.porch")
    assert exposure.is_exposed(hub, "lock.gate")
    assert exposure.is_exposed(hub, "light.hall")  # no choice: the default
    assert exposure.is_exposed(hub, "sensor.legacy")
    assert store.get_kv(exposure.KV_KEY) == {"expose_new": True, "entities": {"sensor.legacy": True}}


def test_a_fresh_hub_without_an_import_uses_the_defaults(tmp_path):
    store = Store(str(tmp_path / "hub.db"), Fernet.generate_key().decode())
    hub = Hub()
    hub.store = store
    hub.states.set("light.new_lamp", "off", {})
    hub.states.set("lock.new_lock", "locked", {})
    assert exposure.exposed_entities(hub) == ["light.new_lamp"]


# HA's websocket commands (ha_ws_compat.py): what HA's own frontend and clients send.

def _ws(client, token=SECRET):
    ws = client.websocket_connect("/api/websocket")
    sock = ws.__enter__()
    assert sock.receive_json()["type"] == "auth_required"
    sock.send_json({"type": "auth", "access_token": token})
    assert sock.receive_json()["type"] == "auth_ok"
    return ws, sock


def _ask(sock, mid, **msg):
    sock.send_json({"id": mid, **msg})
    while True:
        reply = sock.receive_json()
        if reply.get("id") == mid:
            return reply


def _kid_token(store):
    import time

    import jwt

    store.set_kv("users", [{"id": "u2", "name": "Kid", "is_active": True, "group_ids": []}])
    store.save_refresh_token("rt2", {"user_id": "u2"}, "j" * 64)
    now = int(time.time())
    return jwt.encode({"iss": "rt2", "iat": now, "exp": now + 60}, "j" * 64, algorithm="HS256")


def test_the_websocket_exposes_and_lists_like_home_assistant(env):
    hub, store, rt = env
    client = TestClient(build_app(rt, Auth(store, SECRET)))
    ws, sock = _ws(client)
    try:
        r = _ask(sock, 1, type="homeassistant/expose_entity", assistants=["conversation"],
                 entity_ids=["lock.side_door", "sensor.api_humidity"], should_expose=True)
        assert r["success"] is True and r["result"] is None
        assert exposure.view(hub, "lock.side_door") == {"exposed": True, "default": False, "chosen": True}
        assert store.get_kv(exposure.KV_KEY)["entities"] == {"sensor.api_humidity": True}  # no registry row
        r = _ask(sock, 2, type="homeassistant/expose_entity", assistants=["conversation"],
                 entity_ids=["light.den_lamp"], should_expose=False)
        listing = _ask(sock, 3, type="homeassistant/expose_entity/list")["result"]["exposed_entities"]
        assert listing["lock.side_door"] == {"conversation": True}
        assert listing["light.den_lamp"] == {"conversation": False}
        assert listing["sensor.den_power"] == {"conversation": False}  # the default, listed too
        assert set(listing) >= {"sensor.api_humidity", "switch.hidden_plug"}

        assert _ask(sock, 4, type="homeassistant/expose_new_entities/get",
                    assistant="conversation")["result"] == {"expose_new": True}
        assert _ask(sock, 5, type="homeassistant/expose_new_entities/set", assistant="conversation",
                    expose_new=False)["success"]
        assert exposure.expose_new(hub) is False
        assert not exposure.is_exposed(hub, "binary_sensor.den_window")  # nothing exposed by default now
        assert exposure.is_exposed(hub, "lock.side_door")  # a choice stays a choice
    finally:
        ws.__exit__(None, None, None)


def test_the_websocket_refuses_what_it_cannot_keep(env):
    hub, store, rt = env
    client = TestClient(build_app(rt, Auth(store, SECRET)))
    ws, sock = _ws(client)
    try:
        r = _ask(sock, 1, type="homeassistant/expose_entity", assistants=["cloud.alexa"],
                 entity_ids=["light.den_lamp"], should_expose=True)
        assert r["success"] is False and r["error"]["code"] == "not_supported"
        assert "cloud.alexa" in r["error"]["message"]
        r = _ask(sock, 2, type="homeassistant/expose_entity", assistants=["conversation"],
                 entity_ids=["light.nowhere"], should_expose=True)
        assert r["error"]["code"] == "not_found" and "light.nowhere" in r["error"]["message"]
        r = _ask(sock, 3, type="homeassistant/expose_entity", assistants=["conversation"],
                 entity_ids=["light.den_lamp"], should_expose="yes")
        assert r["error"]["code"] == "invalid_format"
        r = _ask(sock, 4, type="homeassistant/expose_new_entities/get", assistant="cloud.google_assistant")
        assert r["error"]["code"] == "not_supported"
        assert exposure.chosen(hub, "light.den_lamp") is None  # nothing was half-applied
    finally:
        ws.__exit__(None, None, None)


def test_only_an_admin_changes_exposure_over_the_websocket(env):
    hub, store, rt = env
    client = TestClient(build_app(rt, Auth(store, SECRET)))
    ws, sock = _ws(client, _kid_token(store))
    try:
        r = _ask(sock, 1, type="homeassistant/expose_entity", assistants=["conversation"],
                 entity_ids=["lock.side_door"], should_expose=True)
        assert r["error"]["code"] == "unauthorized"
        r = _ask(sock, 2, type="homeassistant/expose_new_entities/set", assistant="conversation", expose_new=False)
        assert r["error"]["code"] == "unauthorized"
        assert _ask(sock, 3, type="homeassistant/expose_entity/list")["success"]  # reading stays open
        assert not exposure.is_exposed(hub, "lock.side_door")
    finally:
        ws.__exit__(None, None, None)

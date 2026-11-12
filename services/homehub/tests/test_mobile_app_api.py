"""HA's mobile_app protocol: registration, webhooks (plain and encrypted), pushes, settings, Jarvis fallback.

Every id, name and token here is made up.
"""
from __future__ import annotations

import asyncio
import json
import time

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from nacl.encoding import Base64Encoder, HexEncoder, RawEncoder
from nacl.secret import SecretBox

from services.homehub import mobile_app_webhook as maw
from services.homehub.api import build_app
from services.homehub.auth import Auth
from services.homehub.core import ConfigEntry, DeviceEntry, EntityEntry, Hub
from services.homehub.drivers import mobile_app as ma
from services.homehub.runtime import Runtime
from services.homehub.store import Store

SECRET = "internal-secret"
ADMIN = {"Authorization": f"Bearer {SECRET}"}
HOOK = "ab" * 32
ENC_HOOK = "cd" * 32
ENC_SECRET = "ef" * 32
RELAY = "https://mobile-apps.home-assistant.io/api/sendPushNotification"
HOME = {"latitude": 10.0, "longitude": 20.0, "radius": 100, "location_name": "Home"}


def _phone(hub, store, entry_id, name, webhook_id, *, encrypted=False, user_id="u-alex", app_data=None):
    data = {"app_id": "io.homeassistant.companion.android", "app_name": "Home Assistant",
            "app_version": "2026.10.1", "device_name": name, "device_id": f"dev-{entry_id}",
            "manufacturer": "Acme", "model": "Phone 1", "os_name": "Android", "os_version": "16",
            "supports_encryption": encrypted, "user_id": user_id, "webhook_id": webhook_id,
            "app_data": app_data if app_data is not None else {}}
    if encrypted:
        data["secret"] = ENC_SECRET
    entry = ConfigEntry(entry_id, "mobile_app", name, data=data, source="registration",
                        unique_id=f"{data['app_id']}-{data['device_id']}")
    hub.config_entries[entry_id] = entry
    store.save_config_entry(entry)
    dev = DeviceEntry(id=f"device-{entry_id}", name=name, config_entries=[entry_id],
                      identifiers=[["mobile_app", data["device_id"]]])
    hub.devices[dev.id] = dev
    tracker = f"device_tracker.{ma.slugify(name)}"
    hub.entities[tracker] = EntityEntry(entity_id=tracker, platform="mobile_app", unique_id=data["device_id"],
                                        config_entry_id=entry_id, device_id=dev.id)
    return entry


@pytest.fixture
def env(tmp_path, monkeypatch):
    store = Store(str(tmp_path / "hub.db"), Fernet.generate_key().decode())
    store.set_kv("users", [{"id": "u-alex", "name": "Alex Example", "is_owner": True, "is_active": True,
                            "group_ids": ["system-admin"]}])
    store.set_kv("persons", [{"id": "alex", "name": "Alex", "user_id": "u-alex", "device_trackers": []}])
    hub = Hub(time_zone="UTC", location={"latitude": 10.0, "longitude": 20.0})
    hub.store = store
    ma.setup_zones(hub, [{"id": "work", "name": "Work", "latitude": 10.5, "longitude": 20.5, "radius": 100}], HOME)
    plain = _phone(hub, store, "e-plain", "Alex's Phone", HOOK, app_data={"push_url": RELAY, "push_token": "tok-1",
                                                                          "push_websocket_channel": True})
    _phone(hub, store, "e-enc", "Alex Tablet", ENC_HOOK, encrypted=True)
    # An imported sensor: its entity id must survive, whatever the app calls it now.
    hub.entities["sensor.alex_s_phone_battery_level"] = EntityEntry(
        entity_id="sensor.alex_s_phone_battery_level", platform="mobile_app", unique_id=f"{HOOK}_battery_level",
        config_entry_id=plain.entry_id, device_id="device-e-plain", original_device_class="battery",
        unit_of_measurement="%", capabilities={"state_class": "measurement"})
    runtime = Runtime(hub, store)
    started: list[str] = []
    monkeypatch.setattr(runtime, "start_entry", lambda entry: started.append(entry.entry_id))
    auth = Auth(store, SECRET)
    pushes: list[tuple[str, dict]] = []
    relay = {"status": 201, "body": {"rateLimits": {"successful": 1, "maximum": 500, "errors": 0}}}

    async def fake_post(url, payload):
        pushes.append((url, payload))
        if isinstance(relay["status"], Exception):
            raise relay["status"]
        return relay["status"], relay["body"]

    monkeypatch.setattr(maw, "_post_json", fake_post)
    sent: list[dict] = []

    async def fake_send(payload, **kw):
        sent.append(payload)
        return True

    monkeypatch.setattr(ma.jarvis_notify, "send", fake_send)
    app = build_app(runtime, auth)
    with TestClient(app) as client:
        yield {"client": client, "hub": hub, "store": store, "runtime": runtime, "started": started,
               "pushes": pushes, "relay": relay, "sent": sent, "mgr": runtime.mobile_app, "auth": auth}


def hook(c, payload, webhook_id=HOOK):
    return c.post(f"/api/webhook/{webhook_id}", json=payload)


def encrypt(data, secret=ENC_SECRET, legacy=False):
    key = secret.encode()[:32].ljust(32, b"\0") if legacy else secret.encode()
    box = SecretBox(key, encoder=RawEncoder if legacy else HexEncoder)
    return box.encrypt(json.dumps(data).encode(), encoder=Base64Encoder).decode()


def decrypt(blob, secret=ENC_SECRET):
    return json.loads(SecretBox(secret.encode(), encoder=HexEncoder).decrypt(blob.encode(), encoder=Base64Encoder))


def user_token(env):
    """An HA access token for u-alex, made the way the refresh grant makes one."""
    import jwt

    env["store"].save_refresh_token("rt-1", {"user_id": "u-alex", "token_type": "normal"}, "k" * 64)
    now = int(time.time())
    return jwt.encode({"iss": "rt-1", "iat": now, "exp": now + 1800}, "k" * 64, algorithm="HS256")


# -- registration ---------------------------------------------------------------

def test_registration_creates_a_phone_the_ha_way(env):
    c, hub = env["client"], env["hub"]
    body = {"app_id": "io.homeassistant.companion.android", "app_name": "Home Assistant", "app_version": "2026.11",
            "device_name": "Sam's Phone", "device_id": "new-device-1", "manufacturer": "Acme", "model": "X",
            "os_name": "Android", "os_version": "16", "supports_encryption": True,
            "app_data": {"push_url": RELAY, "push_token": "tok-2"}}
    assert c.post("/api/mobile_app/registrations", json=body).status_code == 401
    assert c.post("/api/mobile_app/registrations", json=body, headers=ADMIN).status_code == 403
    tok = {"Authorization": f"Bearer {user_token(env)}"}
    r = c.post("/api/mobile_app/registrations", json=body, headers=tok)
    assert r.status_code == 201
    out = r.json()
    assert len(out["webhook_id"]) == 64 and len(out["secret"]) == 64 and out["cloudhook_url"] is None
    entry = env["mgr"].entry_for_webhook(out["webhook_id"])
    assert entry.title == "Sam's Phone" and entry.unique_id == "io.homeassistant.companion.android-new-device-1"
    assert entry.source == "registration" and entry.data["user_id"] == "u-alex"
    assert env["started"] == [entry.entry_id]
    assert hub.entities["device_tracker.sam_s_phone"].unique_id == "new-device-1"
    assert "notify.sam_s_phone" in hub.entities
    assert "device_tracker.sam_s_phone" in env["store"].get_kv("persons")[0]["device_trackers"]
    stored = {e.entry_id: e for e in env["store"].load_config_entries()}[entry.entry_id]
    assert stored.data["secret"] == out["secret"]  # sealed in the store, as every entry's data
    missing = c.post("/api/mobile_app/registrations", json={"app_id": "x"}, headers=tok)
    assert missing.status_code == 400


def test_an_app_registering_again_keeps_its_entry_and_moves_its_sensors(env):
    c, hub = env["client"], env["hub"]
    tok = {"Authorization": f"Bearer {user_token(env)}"}
    body = {"app_id": "io.homeassistant.companion.android", "app_name": "Home Assistant", "app_version": "2026.12",
            "device_name": "Alex's Phone", "device_id": "dev-e-plain", "manufacturer": "Acme", "model": "Phone 1",
            "os_name": "Android", "supports_encryption": False}
    out = c.post("/api/mobile_app/registrations", json=body, headers=tok).json()
    assert out["webhook_id"] != HOOK
    assert hub.config_entries["e-plain"].data["webhook_id"] == out["webhook_id"]
    sensor = hub.entities["sensor.alex_s_phone_battery_level"]
    assert sensor.unique_id == f"{out['webhook_id']}_battery_level"
    assert env["started"] == ["e-plain"]


# -- webhooks -----------------------------------------------------------------------

def test_sensors_register_and_update_with_ha_unique_ids(env):
    c, hub = env["client"], env["hub"]
    r = hook(c, {"type": "register_sensor", "data": {
        "name": "Steps sensor", "type": "sensor", "unique_id": "steps_sensor", "state": 1200,
        "unit_of_measurement": "steps", "state_class": "total_increasing", "icon": "mdi:walk"}})
    assert r.status_code == 201 and r.json() == {"success": True}
    ent = hub.entities["sensor.alex_s_phone_steps_sensor"]
    assert ent.unique_id == f"{HOOK}_steps_sensor" and ent.original_name == "Alex's Phone Steps sensor"
    st = hub.states.get("sensor.alex_s_phone_steps_sensor")
    assert st.state == "1200" and st.attributes["state_class"] == "total_increasing"
    assert st.attributes["data_source"] == "companion"
    hook(c, {"type": "register_sensor", "data": {"name": "Android Auto", "type": "binary_sensor",
                                                 "unique_id": "android_auto", "state": False,
                                                 "device_class": "connectivity"}})
    assert hub.states.get("binary_sensor.alex_s_phone_android_auto").state == "off"
    r = hook(c, {"type": "update_sensor_states", "data": [
        {"type": "sensor", "unique_id": "battery_level", "state": 87, "icon": "mdi:battery-80",
         "attributes": {"is_charging": False}},
        {"type": "binary_sensor", "unique_id": "android_auto", "state": True},
        {"type": "sensor", "unique_id": "never_registered", "state": 1},
        {"type": "sensor", "unique_id": "steps_sensor"},
    ]})
    out = r.json()
    assert out["battery_level"] == {"success": True} and out["android_auto"] == {"success": True}
    assert out["never_registered"]["error"]["code"] == "not_registered"
    assert out["steps_sensor"]["error"]["code"] == "invalid_format"
    bat = hub.states.get("sensor.alex_s_phone_battery_level")  # the imported entity id, unchanged
    assert bat.state == "87" and bat.attributes["unit_of_measurement"] == "%"
    assert bat.attributes["device_class"] == "battery" and bat.attributes["is_charging"] is False
    assert hub.states.get("binary_sensor.alex_s_phone_android_auto").state == "on"
    assert env["store"].load_entities()  # registered sensors are saved
    # A disabled sensor is acknowledged with is_disabled and not written.
    hub.entities["sensor.alex_s_phone_steps_sensor"].disabled_by = "user"
    out = hook(c, {"type": "update_sensor_states", "data": [{"type": "sensor", "unique_id": "steps_sensor",
                                                             "state": 5}]}).json()
    assert out["steps_sensor"] == {"success": True, "is_disabled": True}
    assert hub.states.get("sensor.alex_s_phone_steps_sensor").state == "1200"


def test_another_phones_sensor_cannot_be_written(env):
    c, hub = env["client"], env["hub"]
    hub.entities["sensor.tablet_x"] = EntityEntry(entity_id="sensor.tablet_x", platform="mobile_app",
                                                  unique_id=f"{HOOK}_x", config_entry_id="e-enc")
    out = hook(c, {"type": "update_sensor_states", "data": [{"type": "sensor", "unique_id": "x", "state": 1}]})
    assert out.json()["x"]["error"]["code"] == "not_registered"


def test_location_updates_drive_the_tracker_and_mark_the_app_fresh(env):
    c, hub, mgr = env["client"], env["hub"], env["mgr"]
    assert not mgr.is_fresh("e-plain")
    hook(c, {"type": "update_location", "data": {"gps": [10.5, 20.5], "gps_accuracy": 12, "battery": 55,
                                                 "speed": 3, "altitude": 300}})
    st = hub.states.get("device_tracker.alex_s_phone")
    assert st.state == "Work" and st.attributes["battery_level"] == 55 and st.attributes["altitude"] == 300
    assert st.attributes["source_type"] == "gps" and st.attributes["gps_accuracy"] == 12.0
    assert mgr.is_fresh("e-plain")
    hook(c, {"type": "update_location", "data": {"gps": [11.0, 21.0], "gps_accuracy": 5, "in_zones": ["zone.home"]}})
    assert hub.states.get("device_tracker.alex_s_phone").state == "home"
    hook(c, {"type": "update_location", "data": {"location_name": "home"}})
    assert hub.states.get("device_tracker.alex_s_phone").state == "home"


def test_config_zones_templates_events_services_tags_and_cameras(env):
    c, hub = env["client"], env["hub"]
    cfg = hook(c, {"type": "get_config"}).json()
    assert cfg["hass_device_id"] == "device-e-plain" and "mobile_app" in cfg["components"]
    assert cfg["entities"]["battery_level"] == {"disabled": False}
    assert cfg["entities"]["dev-e-plain"] == {"disabled": False}  # the tracker keeps its plain unique id
    zones = hook(c, {"type": "get_zones"}).json()
    assert [z["entity_id"] for z in zones] == ["zone.home", "zone.work"]
    hub.states.set("sensor.outside", "71")
    out = hook(c, {"type": "render_template", "data": {"t": {"template": "It is {{ states('sensor.outside') }}"},
                                                       "bad": {"template": "{{ nope( }}"}}}).json()
    assert out["t"] == "It is 71" and "error" in out["bad"]
    events = []
    hub.bus.subscribe(lambda t, d: events.append((t, d)))
    hook(c, {"type": "fire_event", "data": {"event_type": "phone_button", "event_data": {"n": 1}}})
    hook(c, {"type": "scan_tag", "data": {"tag_id": "tag-123"}})
    assert ("phone_button", {"n": 1}) in events
    assert ("tag_scanned", {"tag_id": "tag-123", "device_id": "device-e-plain"}) in events
    calls = []

    async def svc(_hub, call):
        calls.append(call.data)

    hub.register_service("script", "turn_on", svc)
    assert hook(c, {"type": "call_service", "data": {"domain": "script", "service": "turn_on",
                                                     "service_data": {"entity_id": "script.x"}}}).status_code == 200
    assert calls == [{"entity_id": "script.x"}]
    assert hook(c, {"type": "call_service", "data": {"domain": "nope", "service": "x"}}).status_code == 400
    hub.states.set("camera.door", "idle")
    cam = hook(c, {"type": "stream_camera", "data": {"camera_entity_id": "camera.door"}}).json()
    assert cam == {"mjpeg_path": "/api/camera_proxy_stream/camera.door", "hls_path": None}
    assert hook(c, {"type": "stream_camera", "data": {"camera_entity_id": "light.x"}}).status_code == 400


def test_update_registration(env):
    c, hub = env["client"], env["hub"]
    out = hook(c, {"type": "update_registration", "data": {
        "app_version": "2026.12.0", "device_name": "Alex's Phone", "manufacturer": "Acme", "model": "Phone 2",
        "os_version": "17", "app_data": {"push_url": RELAY, "push_token": "tok-new"}}}).json()
    assert out["model"] == "Phone 2" and "webhook_id" not in out and "secret" not in out
    assert hub.config_entries["e-plain"].data["app_data"]["push_token"] == "tok-new"
    assert hub.devices["device-e-plain"].model == "Phone 2" and hub.devices["device-e-plain"].sw_version == "17"


def test_unknown_and_deleted_webhooks(env):
    c, store = env["client"], env["store"]
    r = hook(c, {"type": "get_config"}, webhook_id="99" * 32)
    assert r.status_code == 200 and r.json() == {}
    store.save_blob(maw.DELETED_BLOB, ["88" * 32])
    assert hook(c, {"type": "get_config"}, webhook_id="88" * 32).status_code == 410
    assert c.post(f"/api/webhook/{HOOK}", content=b"not json").status_code == 400
    assert hook(c, {"type": "conversation_process", "data": {"text": "hi"}}).json() == {}
    assert c.post(f"/api/webhook/{HOOK}", content=b"x" * (300 * 1024)).status_code == 413


# -- encryption ----------------------------------------------------------------------

def test_encrypted_webhooks_both_ways(env):
    c, hub = env["client"], env["hub"]
    r = hook(c, {"type": "get_config", "data": {}}, ENC_HOOK)
    assert r.json()["error"]["code"] == "encryption_required"
    r = hook(c, {"type": "get_config", "encrypted": True, "encrypted_data": encrypt({})}, ENC_HOOK)
    body = r.json()
    assert body["encrypted"] is True and decrypt(body["encrypted_data"])["hass_device_id"] == "device-e-enc"
    assert hub.config_entries["e-enc"].data["no_legacy_encryption"] is True
    # Once the modern key worked, a legacy-keyed payload is ignored rather than accepted.
    legacy = {"type": "fire_event", "encrypted": True,
              "encrypted_data": encrypt({"event_type": "legacy"}, legacy=True)}
    events = []
    hub.bus.subscribe(lambda t, d: events.append(t), "legacy")
    assert hook(c, legacy, ENC_HOOK).json() == {}
    assert events == []
    garbage = hook(c, {"type": "get_config", "encrypted": True, "encrypted_data": "AAAA"}, ENC_HOOK)
    assert garbage.status_code == 200 and garbage.json() == {}


def test_legacy_encryption_still_works_for_old_apps(env):
    c, hub = env["client"], env["hub"]
    r = hook(c, {"type": "get_zones", "encrypted": True, "encrypted_data": encrypt({}, legacy=True)}, ENC_HOOK)
    body = r.json()
    assert body["encrypted"] is True and "no_legacy_encryption" not in hub.config_entries["e-enc"].data
    key = ENC_SECRET.encode()[:32]
    zones = json.loads(SecretBox(key, encoder=RawEncoder).decrypt(body["encrypted_data"].encode(),
                                                                   encoder=Base64Encoder))
    assert zones[0]["entity_id"] == "zone.home"


def test_enable_encryption(env):
    c, hub = env["client"], env["hub"]
    secret = hook(c, {"type": "enable_encryption"}).json()["secret"]
    assert len(secret) == 64 and hub.config_entries["e-plain"].data["supports_encryption"] is True
    assert hook(c, {"type": "get_config"}).json()["error"]["code"] == "encryption_required"
    again = hook(c, {"type": "enable_encryption", "encrypted": True, "encrypted_data": encrypt({}, secret)})
    assert again.json()["error"]["code"] == "encryption_already_enabled"


# -- notifications ---------------------------------------------------------------------

def test_notify_goes_through_the_push_relay_like_ha(env, monkeypatch):
    hub = env["hub"]
    drv = ma.MobileAppDriver(hub, hub.config_entries["e-plain"])
    hub.drivers["e-plain"] = drv
    hub.entities["notify.alex_s_phone"] = EntityEntry(entity_id="notify.alex_s_phone", platform="mobile_app",
                                                      unique_id="dev-e-plain", config_entry_id="e-plain")
    monkeypatch.setattr(ma, "people_fixes", lambda h: _no_fixes())
    asyncio.run(drv.async_setup())
    res = asyncio.run(hub.call_service("notify", "mobile_app_alex_s_phone",
                                       {"title": "Garage", "message": "The door is open", "data": {"tag": "g"}}))
    assert res["response"] == {"sent": 1, "via": "push"}
    url, payload = env["pushes"][-1]
    assert url == RELAY
    assert payload == {"message": "The door is open", "title": "Garage", "data": {"tag": "g"}, "push_token": "tok-1",
                       "registration_info": {"app_id": "io.homeassistant.companion.android",
                                             "app_version": "2026.10.1", "webhook_id": HOOK, "os_version": "16"}}
    assert env["sent"] == []
    assert hub.states.get("notify.alex_s_phone").state != "unknown"  # when it last sent
    # Commands reach the app, which is what makes "request location update" work again.
    asyncio.run(hub.call_service("notify", "mobile_app_alex_s_phone", {"message": "request_location_update"}))
    assert env["pushes"][-1][1]["message"] == "request_location_update"
    # The relay down: the phone's Jarvis user gets a notice instead.
    env["relay"]["status"] = 500
    res = asyncio.run(hub.call_service("notify", "mobile_app_alex_s_phone", {"message": "Still open"}))
    assert res["response"] == {"sent": 1} and env["sent"][-1]["body"] == "Still open"


async def _no_fixes():
    return {}


def test_a_relay_off_the_allow_list_is_never_called(env, monkeypatch):
    hub, mgr = env["hub"], env["mgr"]
    hub.config_entries["e-plain"].data["app_data"]["push_url"] = "http://10.0.0.5:8080/steal"
    with pytest.raises(Exception, match="allow-list"):
        asyncio.run(mgr.relay(hub.config_entries["e-plain"], {"message": "x"}))
    assert env["pushes"] == []


def test_local_push_channel_over_the_websocket(env):
    c, hub, mgr = env["client"], env["hub"], env["mgr"]
    token = user_token(env)
    with c.websocket_connect("/api/websocket") as ws:
        ws.receive_json()
        ws.send_json({"type": "auth", "access_token": token})
        assert ws.receive_json()["type"] == "auth_ok"
        ws.send_json({"id": 5, "type": "mobile_app/push_notification_channel", "webhook_id": "00" * 32})
        assert ws.receive_json()["error"]["code"] == "unauthorized"
        ws.send_json({"id": 6, "type": "mobile_app/push_notification_channel", "webhook_id": HOOK,
                      "support_confirm": True})
        assert ws.receive_json()["success"] is True
        assert HOOK in mgr.channels
        c.portal.call(mgr.push, hub.config_entries["e-plain"], {"message": "Hello"})
        event = ws.receive_json()
        assert event["id"] == 6 and event["event"]["message"] == "Hello"
        confirm_id = event["event"]["hass_confirm_id"]
        ws.send_json({"id": 7, "type": "mobile_app/push_notification_confirm", "webhook_id": HOOK,
                      "confirm_id": confirm_id})
        assert ws.receive_json()["success"] is True
        assert env["pushes"] == []  # confirmed locally: the relay was not needed
    deadline = time.time() + 2
    while HOOK in mgr.channels and time.time() < deadline:
        time.sleep(0.05)
    assert HOOK not in mgr.channels  # the channel goes when the socket does


def test_an_unconfirmed_local_push_falls_back_to_the_relay(env, monkeypatch):
    monkeypatch.setattr(maw, "PUSH_CONFIRM_TIMEOUT_S", 0.05)
    mgr, entry = env["mgr"], env["hub"].config_entries["e-plain"]

    async def run():
        sent = []

        async def send(data):
            sent.append(data)

        ch = maw.PushChannel(HOOK, True, send, lambda: mgr.channels.pop(HOOK, None))
        mgr.channels[HOOK] = ch
        assert await mgr.push(entry, {"message": "hi"}) == "local"
        await asyncio.sleep(0.2)
        return sent

    sent = asyncio.run(run())
    assert sent and env["pushes"] and env["pushes"][0][1]["message"] == "hi"
    assert "hass_confirm_id" not in env["pushes"][0][1]
    assert HOOK not in mgr.channels


# -- settings and the admin API ----------------------------------------------------------

def test_settings_are_stored_validated_and_admin_only(env):
    c, hub = env["client"], env["hub"]
    assert c.get("/hub/mobile_app/settings").status_code == 401
    s = c.get("/hub/mobile_app/settings", headers=ADMIN).json()
    assert s["external_url"] == "" and s["push_url_allowlist"] == ["https://mobile-apps.home-assistant.io/"]
    r = c.put("/hub/mobile_app/settings", headers=ADMIN, json={"external_url": "https://hub.example.invalid/",
                                                               "companion_fresh_minutes": 10})
    assert r.status_code == 200 and r.json()["external_url"] == "https://hub.example.invalid"
    assert hub.location["external_url"] == "https://hub.example.invalid"
    assert c.get("/api/config", headers=ADMIN).json()["external_url"] == "https://hub.example.invalid"
    for bad in ({"external_url": "ftp://x"}, {"push_url_allowlist": ["http://plain"]}, {"nope": 1},
                {"jarvis_phone_name": "no placeholder"}):
        assert c.put("/hub/mobile_app/settings", headers=ADMIN, json=bad).status_code == 400
    c.put("/hub/mobile_app/settings", headers=ADMIN, json={"external_url": None})
    assert c.get("/hub/mobile_app/settings", headers=ADMIN).json()["external_url"] == ""


def test_phones_listing_options_and_delete(env):
    c, hub, mgr = env["client"], env["hub"], env["mgr"]
    hook(c, {"type": "update_sensor_states", "data": [{"type": "sensor", "unique_id": "battery_level", "state": 9}]})
    phones = c.get("/hub/mobile_app/phones", headers=ADMIN).json()["phones"]
    alex = next(p for p in phones if p["entry_id"] == "e-plain")
    assert alex["kind"] == "companion" and alex["companion_fresh"] is True and alex["push"]["relay"] is True
    assert alex["notify_service"] == "notify.mobile_app_alex_s_phone"
    assert {"entity_id": "sensor.alex_s_phone_battery_level", "state": "9", "source": "companion"}.items() \
        <= next(s for s in alex["sensors"] if s["entity_id"] == "sensor.alex_s_phone_battery_level").items()
    assert "webhook_id" not in json.dumps(phones) and HOOK not in json.dumps(phones)
    r = c.patch("/hub/mobile_app/phones/e-plain", headers=ADMIN, json={"jarvis_user": "Alex", "jarvis_fallback": False})
    assert r.json()["options"] == {"jarvis_user": "alex", "jarvis_fallback": False}
    assert c.patch("/hub/mobile_app/phones/e-plain", headers=ADMIN, json={"x": 1}).status_code == 400
    assert c.patch("/hub/mobile_app/phones/nope", headers=ADMIN, json={}).status_code == 404
    assert c.delete("/hub/mobile_app/phones/e-plain", headers=ADMIN).json()["deleted"] is True
    assert "e-plain" not in hub.config_entries and "sensor.alex_s_phone_battery_level" not in hub.entities
    assert hook(c, {"type": "get_config"}).status_code == 410  # the app learns to register again
    assert mgr.entry_for_webhook(HOOK) is None


# -- Jarvis as a sensor source --------------------------------------------------------------

def _fixes(**users):
    feats = []
    for user, extra in users.items():
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [20.0, 10.0]},
                      "properties": {"entity_id": f"person.{user}", "state": "home", "source_type": "jarvis",
                                     "gps_accuracy": 8, **extra}})
    return {"type": "FeatureCollection", "features": feats, "source": "jarvis"}


@pytest.fixture
def geo(monkeypatch):
    box = {"payload": _fixes(), "steps": {}}

    async def fake_fetch(base, secret):
        return box["payload"]

    async def fake_steps(base, secret, user):
        return box["steps"].get(user)

    monkeypatch.setattr(ma, "_geo_settings", lambda: ("http://geo.invalid", "s"))
    monkeypatch.setattr(ma, "_fetch_geo", fake_fetch)
    monkeypatch.setattr(ma, "_fetch_steps", fake_steps)
    return box


def test_a_jarvis_phone_gets_tracker_battery_steps_and_activity(env, geo):
    c, hub = env["client"], env["hub"]
    r = c.post("/hub/mobile_app/phones", headers=ADMIN, json={"jarvis_user": "sam"})
    assert r.status_code == 201
    entry = hub.config_entries[r.json()["entry_id"]]
    assert entry.title == "Sam's Phone" and entry.source == "jarvis" and env["started"] == [entry.entry_id]
    assert c.post("/hub/mobile_app/phones", headers=ADMIN, json={"jarvis_user": "sam"}).status_code == 400
    geo["payload"] = _fixes(sam={"battery": 41, "activity": "driving"})
    geo["steps"]["sam"] = 4321
    drv = ma.MobileAppDriver(hub, entry)
    hub.drivers[entry.entry_id] = drv
    asyncio.run(drv.async_poll())
    assert hub.states.get("device_tracker.sam_s_phone").state == "home"
    bat = hub.states.get("sensor.sam_s_phone_battery_level")
    assert bat.state == "41" and bat.attributes["data_source"] == "jarvis"
    assert hub.states.get("sensor.sam_s_phone_steps_sensor").state == "4321"
    assert hub.states.get("sensor.sam_s_phone_detected_activity").state == "in_vehicle"
    listed = c.get("/hub/mobile_app/phones", headers=ADMIN).json()["phones"]
    sam = next(p for p in listed if p["entry_id"] == entry.entry_id)
    assert sam["kind"] == "jarvis" and {s["source"] for s in sam["sensors"]} == {"jarvis"}


def test_jarvis_fills_a_quiet_companion_phone_and_stands_back_when_it_reports(env, geo):
    c, hub = env["client"], env["hub"]
    entry = hub.config_entries["e-plain"]
    entry.options["jarvis_user"] = "alex"
    drv = ma.MobileAppDriver(hub, entry)
    hub.drivers["e-plain"] = drv
    geo["payload"] = _fixes(alex={"battery": 33})
    c.put("/hub/mobile_app/settings", headers=ADMIN, json={"auto_jarvis_phones": False})
    asyncio.run(drv.async_poll())
    assert hub.states.get("sensor.alex_s_phone_battery_level").state == "33"
    assert hub.states.get("device_tracker.alex_s_phone").attributes["data_source"] == "jarvis"
    hook(c, {"type": "update_sensor_states", "data": [{"type": "sensor", "unique_id": "battery_level", "state": 90}]})
    geo["payload"] = _fixes(alex={"battery": 12})
    hub._geo_people_cache["at"] = 0.0
    asyncio.run(drv.async_poll())
    assert hub.states.get("sensor.alex_s_phone_battery_level").state == "90"  # the app is fresh: it wins


def test_a_user_with_two_phones_gets_jarvis_data_on_the_one_last_seen(env, geo):
    c, hub, mgr = env["client"], env["hub"], env["mgr"]
    c.put("/hub/mobile_app/settings", headers=ADMIN, json={"auto_jarvis_phones": False, "companion_fresh_minutes": 1})
    old = _phone(hub, env["store"], "e-old", "Alex Old Phone", "12" * 32)
    hub.entities["sensor.alex_old_phone_battery_level"] = EntityEntry(
        entity_id="sensor.alex_old_phone_battery_level", platform="mobile_app",
        unique_id=f"{'12' * 32}_battery_level", config_entry_id="e-old")
    for e in (hub.config_entries["e-plain"], old):
        e.options["jarvis_user"] = "alex"
        hub.drivers[e.entry_id] = ma.MobileAppDriver(hub, e)
    geo["payload"] = _fixes(alex={"battery": 70})
    asyncio.run(hub.drivers["e-plain"].async_poll())
    asyncio.run(hub.drivers["e-old"].async_poll())
    assert hub.states.get("sensor.alex_old_phone_battery_level") is None  # no evidence yet: no guess
    mgr._last_seen["e-plain"] = time.time() - 600  # reported 10 minutes ago, stale now, still the newer one
    asyncio.run(hub.drivers["e-plain"].async_poll())
    asyncio.run(hub.drivers["e-old"].async_poll())
    assert hub.states.get("sensor.alex_s_phone_battery_level").state == "70"
    assert hub.states.get("sensor.alex_old_phone_battery_level") is None
    # An admin can rule the stale install out instead.
    mgr._last_seen.clear()
    c.patch("/hub/mobile_app/phones/e-plain", headers=ADMIN, json={"jarvis_fallback": False})
    hub._geo_people_cache["at"] = 0.0
    asyncio.run(hub.drivers["e-old"].async_poll())
    assert hub.states.get("sensor.alex_old_phone_battery_level").state == "70"


def test_every_reporting_jarvis_user_gets_a_phone_automatically(env, geo):
    hub = env["hub"]
    entry = hub.config_entries["e-plain"]
    entry.options["jarvis_user"] = "alex"
    hub.drivers["e-plain"] = ma.MobileAppDriver(hub, entry)
    geo["payload"] = _fixes(alex={}, robin={"battery": 50}, kim={})
    asyncio.run(hub.drivers["e-plain"].async_poll())
    names = sorted(e.title for e in env["mgr"].entries() if e.source == "jarvis")
    assert names == ["Kim's Phone", "Robin's Phone"]
    asyncio.run(env["mgr"].ensure_jarvis_phones({"alex", "robin", "kim"}))
    assert len([e for e in env["mgr"].entries() if e.source == "jarvis"]) == 2  # once only


def test_a_non_admin_never_sees_another_phones_webhook_id(env):
    import jwt

    store, c = env["store"], env["client"]
    kid_user = {"id": "u-kid", "name": "Kid", "is_active": True, "group_ids": ["system-users"]}
    store.set_kv("users", [*store.get_kv("users"), kid_user])
    store.save_refresh_token("rt-kid", {"user_id": "u-kid", "token_type": "normal"}, "q" * 64)
    now = int(time.time())
    kid = jwt.encode({"iss": "rt-kid", "iat": now, "exp": now + 600}, "q" * 64, algorithm="HS256")
    for token, visible in ((kid, False), (user_token(env), True)):
        with c.websocket_connect("/api/websocket") as ws:
            ws.receive_json()
            ws.send_json({"type": "auth", "access_token": token})
            assert ws.receive_json()["type"] == "auth_ok"
            ws.send_json({"id": 1, "type": "config/entity_registry/list"})
            rows = {r["entity_id"]: r for r in ws.receive_json()["result"]}
            ws.send_json({"id": 2, "type": "config/entity_registry/get",
                          "entity_id": "sensor.alex_s_phone_battery_level"})
            one = ws.receive_json()["result"]
        battery = rows["sensor.alex_s_phone_battery_level"]
        assert (HOOK in json.dumps(battery)) is visible and (HOOK in json.dumps(one)) is visible
        if not visible:
            assert battery["unique_id"] == "battery_level"

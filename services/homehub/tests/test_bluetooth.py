"""The Bluetooth platform: parsing, discovery, flows, sensors, room presence and the room map.

Every address, UUID, key and area here is made up (02:00:00:... MACs, Den/Loft/Hall).
"""
from __future__ import annotations

import asyncio
import random

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from services.homehub import bluetooth_presence as bp
from services.homehub import flows
from services.homehub import mobile_app_webhook as maw
from services.homehub.api import build_app
from services.homehub.auth import Auth
from services.homehub.bluetooth import Advertisement, BluetoothError, BluetoothManager, attach_esphome_proxy
from services.homehub.capabilities import entity_capabilities
from services.homehub.core import Area, ConfigEntry, DeviceEntry, EntityEntry, Hub
from services.homehub.discovery import DiscoveryManager
from services.homehub.drivers import mobile_app as ma
from services.homehub.runtime import Runtime
from services.homehub.store import Store

SECRET = "internal-secret"
ADMIN = {"Authorization": f"Bearer {SECRET}"}
BTHOME_UUID = "0000fcd2-0000-1000-8000-00805f9b34fb"
SENSOR = "02:00:00:00:00:01"
ENC_SENSOR = "02:00:00:00:00:02"
BEACON_UUID = "0f0e0d0c-0b0a-4908-8706-050403020100"
TAG_UUID = "11111111-2222-4333-8444-555555555555"
DEN, LOFT, HALL = "esphome_020000000a01", "esphome_020000000a02", "bluez_020000000a03"
# BTHome v2: temperature 25.06 C, humidity 50.55 %.
PLAIN = b"\x40\x02\xca\x09\x03\xbf\x13"
BINDKEY = "0f1e2d3c4b5a69788796a5b4c3d2e1f0"  # made up


def bthome_encrypted(address: str, key_hex: str, payload: bytes = b"\x02\xca\x09\x03\xbf\x13",
                     counter: int = 1) -> bytes:
    """An encrypted BTHome v2 frame (AES-CCM over the payload, nonce = MAC + UUID + flags + counter)."""
    from cryptography.hazmat.primitives.ciphers.aead import AESCCM

    adv_info = b"\x41"
    count = counter.to_bytes(4, "little")
    nonce = bytes.fromhex(address.replace(":", "")) + b"\xd2\xfc" + adv_info + count
    sealed = AESCCM(bytes.fromhex(key_hex), tag_length=4).encrypt(nonce, payload, None)
    return adv_info + sealed[:-4] + count + sealed[-4:]


ENCRYPTED = bthome_encrypted("02:00:00:00:00:02", BINDKEY)


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


class FakeDiscovery(DiscoveryManager):
    def __init__(self, hub, store):
        super().__init__(hub, store, notifier=self._notify)
        self.sent: list[dict] = []
        self.scheduled = 0

    async def _notify(self, payload):
        self.sent.append(payload)
        return True

    def _schedule_notices(self):
        self.scheduled += 1


@pytest.fixture
def env(tmp_path):
    store = Store(str(tmp_path / "hub.db"), Fernet.generate_key().decode())
    hub = Hub(time_zone="UTC")
    hub.store = store
    for area_id, name in (("den", "Den"), ("loft", "Loft"), ("hall", "Hall"), ("garage", "Garage")):
        hub.areas[area_id] = Area(area_id, name)
    clock = Clock()
    runtime = Runtime(hub, store)
    disc = FakeDiscovery(hub, store)
    runtime.discovery = disc
    mgr = BluetoothManager(hub, store, clock=clock, wall=lambda: 1_800_000_000.0 + clock.t, adapter="",
                           discovery=lambda: disc)
    for sid, name, area in ((DEN, "Den proxy", "den"), (LOFT, "Loft proxy", "loft"), (HALL, "Host adapter", "hall")):
        mgr.add_scanner(sid, "esphome" if sid.startswith("esphome") else "bluez", name, source="test")
        mgr.set_scanner(sid, {"area_id": area})
    return hub, store, runtime, mgr, clock, disc


def adv(address, scanner, t, rssi=-60, *, service_data=None, manufacturer_data=None, name=None):
    return Advertisement(address, rssi, scanner, t, name, manufacturer_data or {}, service_data or {},
                         list(service_data or {}))


def beacon_adv(scanner, t, rssi, uuid=BEACON_UUID, major=1, minor=2, power=-59):
    return adv("02:00:00:00:0b:01", scanner, t, rssi, manufacturer_data={0x004C: bp.ibeacon_bytes(uuid, major, minor,
                                                                                                    power)})


def run(mgr, clock, seconds, rssi, *, rng=None, uuid=BEACON_UUID, background=(DEN, LOFT, HALL), jitter=2.0):
    """``seconds`` of the beacon heard at ``rssi`` ({scanner: dBm or None}); every scanner in ``background``
    hears some other device too (so it stays online)."""
    rng = rng or random.Random(7)
    end = clock.t + seconds
    while clock.t < end:
        clock.t += 0.5
        for sid in background:
            mgr.ingest(adv("02:00:00:00:ff:ff", sid, clock.t, -90))
        for sid, value in rssi.items():
            if value is not None:
                mgr.ingest(beacon_adv(sid, clock.t, round(value + rng.uniform(-jitter, jitter)), uuid=uuid))
        if int(clock.t * 2) % 2 == 0:
            mgr.tick()


def add_tag(hub, mgr, name="Keys", uuid=BEACON_UUID):
    from services.homehub.bluetooth import Tracker

    entry = ConfigEntry("tag1", "ibeacon", name, data={"uuid": uuid, "major": 1, "minor": 2, "tx_power": -59})
    hub.config_entries[entry.entry_id] = entry
    for eid, uid in (("sensor.keys_room", f"{uuid}_1_2_ble_room"),
                     ("sensor.keys_room_distance", f"{uuid}_1_2_ble_room_distance")):
        hub.entities[eid] = EntityEntry(entity_id=eid, platform="ibeacon", unique_id=uid, config_entry_id="tag1")
    t = Tracker(bp.beacon_key(uuid, 1, 2), "ibeacon", "tag1", name, -59, "sensor.keys_room",
                "sensor.keys_room_distance")
    mgr.add_tracker(t)
    return t


# -- identities and parsing ---------------------------------------------------------------

def test_ibeacon_frames_round_trip_and_bad_beacons_say_why():
    b = bp.parse_ibeacon({0x004C: bp.ibeacon_bytes(BEACON_UUID, 7, 9, -61)})
    assert (b.uuid, b.major, b.minor, b.tx_power) == (BEACON_UUID, 7, 9, -61)
    assert bp.parse_ibeacon({0x004C: b"\x10\x05"}) is None
    assert bp.parse_beacon_key(f"{BEACON_UUID.upper()}-7-9") == (BEACON_UUID, 7, 9)
    with pytest.raises(ValueError, match="major must be between"):
        bp.beacon_from_dict({"uuid": BEACON_UUID, "major": 70000, "minor": 1})
    with pytest.raises(ValueError, match="not a UUID"):
        bp.beacon_from_dict({"uuid": "nope", "major": 1, "minor": 1})
    with pytest.raises(ValueError, match="whole number"):
        bp.beacon_from_dict({"uuid": BEACON_UUID, "major": 1.5, "minor": 1})


def test_a_bthome_sensor_is_identified_and_shows_up_in_discovery(env):
    _hub, _store, _runtime, mgr, clock, disc = env
    mgr.ingest(adv(SENSOR, DEN, clock.t, service_data={BTHOME_UUID: PLAIN}, name="ATC_1"))
    found = disc.list()
    assert len(found) == 1
    f = found[0]
    assert f["integration"] == "bthome" and f["label"] == "BTHome" and f["new"] and f["adoptable"]
    assert f["needs"] == [] and f["details"]["encrypted"] is False and f["details"]["area"] == "Den"
    assert disc.scheduled == 1
    mgr.ingest(adv(ENC_SENSOR, LOFT, clock.t, service_data={BTHOME_UUID: ENCRYPTED}))
    enc = next(x for x in disc.list() if x["mac"] == "020000000002")
    assert enc["details"]["encrypted"] is True and enc["needs"][0]["key"] == "bindkey"


async def test_adding_a_sensor_registers_its_readings_and_publishes_them_live(env):
    hub, _store, runtime, mgr, clock, disc = env
    mgr.ingest(adv(SENSOR, DEN, clock.t, service_data={BTHOME_UUID: PLAIN}, name="ATC_1"))
    finding = disc.get(disc.list()[0]["id"])
    result = await flows.adopt(runtime, finding, {"area_id": "Den"})
    assert result["status"] == "adopted" and result["domain"] == "bthome"
    entry = hub.config_entries[result["entry_id"]]
    assert entry.unique_id == SENSOR and "bindkey" not in entry.data
    dev = hub.devices[result["device_id"]]
    assert dev.area_id == "den" and ["bluetooth", SENSOR] in dev.connections
    assert dev.model == "Temperature/Humidity sensor" and "model" in dev.confirmed
    temp = next(hub.entities[e] for e in result["entities"] if hub.entities[e].unique_id == f"{SENSOR}-temperature")
    assert temp.trusted("original_device_class") == "temperature" and temp.trusted("unit_of_measurement") == "°C"
    rssi = next(hub.entities[e] for e in result["entities"] if e.endswith("signal_strength"))
    assert rssi.disabled_by == "integration"
    for _ in range(20):
        await asyncio.sleep(0)
        if result["entry_id"] in hub.drivers:
            break
    st = hub.states.get(temp.entity_id)
    assert st is not None and float(st.state) == 25.06 and st.attributes["unit_of_measurement"] == "°C"
    # A new frame (another packet id) arrives live: humidity 60.00 %.
    clock.t += 5
    mgr.ingest(adv(SENSOR, DEN, clock.t, service_data={BTHOME_UUID: b"\x40\x00\x02\x03\x70\x17"}))
    hum = next(e for e in result["entities"] if hub.entities[e].unique_id == f"{SENSOR}-humidity")
    assert float(hub.states.get(hum).state) == 60.0
    assert disc.list()[0]["known"] is True
    await runtime.shutdown()


async def test_an_encrypted_sensor_asks_for_its_bindkey_and_refuses_a_wrong_one(env):
    hub, store, runtime, mgr, clock, disc = env
    mgr.ingest(adv(ENC_SENSOR, LOFT, clock.t, service_data={BTHOME_UUID: ENCRYPTED}))
    finding = disc.get(disc.list()[0]["id"])
    _status, body = await flows.adopt_response(runtime, finding, {})
    assert body["status"] == "needs_input" and body["needs"][0]["key"] == "bindkey"
    _status, body = await flows.adopt_response(runtime, finding, {"input": {"bindkey": "00" * 16}})
    assert body["status"] == "needs_input" and "did not decrypt" in body["error"]
    _status, body = await flows.adopt_response(runtime, finding, {"input": {"bindkey": "xyz"}})
    assert body["status"] == "needs_input" and "32 hex" in body["error"]
    _status, body = await flows.adopt_response(runtime, finding, {"input": {"bindkey": BINDKEY}})
    assert body["status"] == "adopted", body
    entry = hub.config_entries[body["entry_id"]]
    assert entry.data["bindkey"] == BINDKEY and BINDKEY not in str(entry.public())
    reloaded = {e.entry_id: e for e in store.load_config_entries()}[entry.entry_id]
    assert reloaded.data["bindkey"] == BINDKEY  # sealed in the store, readable by the hub only
    await runtime.shutdown()


def test_an_ibeacon_is_listed_quietly_and_an_admin_can_track_it(env):
    _hub, _store, _runtime, mgr, clock, disc = env
    mgr.ingest(beacon_adv(DEN, clock.t, -60, uuid=TAG_UUID))
    f = disc.list()[0]
    assert f["integration"] == "ibeacon" and f["new"] and disc.scheduled == 0  # never announced


async def test_the_ibeacon_flow_makes_room_sensors_and_its_driver_tracks_it(env):
    hub, _store, runtime, mgr, clock, disc = env
    mgr.ingest(beacon_adv(DEN, clock.t, -60, uuid=TAG_UUID))
    finding = disc.get(disc.list()[0]["id"])
    status, body = await flows.adopt_response(runtime, finding, {})
    assert body["status"] == "needs_input" and body["needs"][0]["key"] == "name"
    status, body = await flows.adopt_response(runtime, finding, {"name": "Keys"})
    assert body["status"] == "adopted", body
    assert sorted(body["entities"]) == ["sensor.keys_room", "sensor.keys_room_distance"]
    for _ in range(20):
        await asyncio.sleep(0)
        if bp.beacon_key(TAG_UUID, 1, 2) in mgr.trackers:
            break
    assert mgr.trackers[bp.beacon_key(TAG_UUID, 1, 2)].room_entity == "sensor.keys_room"
    assert "presence_room" in entity_capabilities(hub, "sensor.keys_room")["abilities"]
    status, body = await flows.adopt_response(runtime, finding, {"name": "Keys again"})
    assert status == 409
    await runtime.shutdown()


async def test_the_manual_ibeacon_flow_validates_the_identity(env):
    from services.homehub import manual_flows

    hub, _store, runtime, _mgr, _clock, _disc = env
    finding = manual_flows.manual_finding("ibeacon", {})
    _status, body = await flows.adopt_response(runtime, finding, {"input": {"name": "Bag", "uuid": "x", "major": 1,
                                                                           "minor": 1}})
    assert body["status"] == "needs_input" and "not a UUID" in body["error"]
    _status, body = await flows.adopt_response(runtime, finding, {"input": {"name": "Bag", "uuid": TAG_UUID,
                                                                           "major": "3", "minor": "4"}})
    assert body["status"] == "adopted" and hub.config_entries[body["entry_id"]].unique_id == f"{TAG_UUID}_3_4"
    await runtime.shutdown()


# -- presence ------------------------------------------------------------------------------

def test_nearest_scanner_switches_rooms_only_after_the_dwell(env):
    hub, _store, _runtime, mgr, clock, _disc = env
    add_tag(hub, mgr)
    assert hub.states.get("sensor.keys_room") is None
    run(mgr, clock, 4, {DEN: -55, LOFT: -80})
    st = hub.states.get("sensor.keys_room")
    assert st.state == "Den" and st.attributes["area_id"] == "den" and st.attributes["method"] == "nearest_scanner"
    assert st.attributes["scanners_heard"] == 2 and st.attributes["source"] == "ble"
    assert st.attributes["scanner"] == "Den proxy" and 0.5 < st.attributes["confidence"] <= 1
    assert st.attributes["icon"] == "mdi:map-marker-radius" and st.attributes["last_seen"]
    assert float(hub.states.get("sensor.keys_room_distance").state) > 0
    run(mgr, clock, mgr.params.dwell_s - 4, {DEN: -80, LOFT: -55})
    assert hub.states.get("sensor.keys_room").state == "Den"  # not yet: still dwelling
    run(mgr, clock, 6, {DEN: -80, LOFT: -55})
    assert hub.states.get("sensor.keys_room").state == "Loft"


def test_a_brief_flicker_does_not_move_the_room(env):
    hub, _store, _runtime, mgr, clock, _disc = env
    add_tag(hub, mgr)
    run(mgr, clock, 6, {DEN: -55, LOFT: -80})
    run(mgr, clock, 4, {DEN: -80, LOFT: -55})
    run(mgr, clock, 6, {DEN: -55, LOFT: -80})
    run(mgr, clock, 4, {DEN: -80, LOFT: -55})
    assert hub.states.get("sensor.keys_room").state == "Den"


def test_unheard_beacon_goes_not_home_after_the_timeout_and_comes_back_at_once(env):
    hub, _store, _runtime, mgr, clock, _disc = env
    add_tag(hub, mgr)
    run(mgr, clock, 4, {HALL: -60})
    assert hub.states.get("sensor.keys_room").state == "Hall"
    run(mgr, clock, mgr.params.away_s - 10, {})
    assert hub.states.get("sensor.keys_room").state == "Hall"  # quiet, not yet away
    run(mgr, clock, 15, {})
    st = hub.states.get("sensor.keys_room")
    assert st.state == "not_home" and st.attributes["area_id"] is None
    assert hub.states.get("sensor.keys_room_distance").state == "unknown"
    run(mgr, clock, 2, {LOFT: -60})
    assert hub.states.get("sensor.keys_room").state == "Loft"


def test_calibration_offsets_and_reference_power(env):
    hub, _store, _runtime, mgr, clock, _disc = env
    t = add_tag(hub, mgr)
    t.tx_power = None
    mgr.set_scanner(DEN, {"rssi_offset": -15, "ref_power": -50})
    run(mgr, clock, 4, {DEN: -60, LOFT: -68}, jitter=0)
    st = hub.states.get("sensor.keys_room")
    assert st.state == "Loft"  # -60 at the Den proxy reads as -75 after its offset
    with pytest.raises(BluetoothError, match="rssi_offset"):
        mgr.set_scanner(DEN, {"rssi_offset": 99})
    with pytest.raises(BluetoothError, match="no area"):
        mgr.set_scanner(DEN, {"area_id": "Attic"})
    with pytest.raises(BluetoothError) as exc:
        mgr.set_scanner("esphome_nope", {"area_id": "den"})
    assert exc.value.status == 404


def test_esphome_proxy_feeds_the_bus_with_its_device_area(env):
    from aioesphomeapi import APIVersion, BluetoothProxyFeature, DeviceInfo
    from aioesphomeapi.api_pb2 import BluetoothLERawAdvertisement, BluetoothLERawAdvertisementsResponse

    hub, _store, _runtime, mgr, _clock, _disc = env
    entry = ConfigEntry("node1", "esphome", "Attic node")
    hub.config_entries["node1"] = entry
    hub.devices["d-node"] = DeviceEntry(id="d-node", name="Attic node", area_id="garage", config_entries=["node1"])

    class FakeDriver:
        def __init__(self):
            self.entry = entry
            self.entities = []

    class FakeClient:
        def __init__(self):
            self.cb = None
            self.unsubscribed = False

        def subscribe_bluetooth_le_raw_advertisements(self, cb):
            self.cb = cb

            def unsub():
                self.unsubscribed = True
            return unsub

    flags = int(BluetoothProxyFeature.PASSIVE_SCAN | BluetoothProxyFeature.RAW_ADVERTISEMENTS)
    info = DeviceInfo(name="attic", friendly_name="Attic node", mac_address="02:00:00:00:0C:01",
                      bluetooth_mac_address="02:00:00:00:0C:02", bluetooth_proxy_feature_flags=flags)
    client = FakeClient()
    detach = attach_esphome_proxy(hub, FakeDriver(), client, info, APIVersion(1, 12))
    assert detach is not None
    data = bytes([3, 0xFF]) + b"\x4c\x00"  # flags-free frame with a 2-byte manufacturer field
    client.cb(BluetoothLERawAdvertisementsResponse(advertisements=[
        BluetoothLERawAdvertisement(address=0x020000000D01, rssi=-61, address_type=0, data=data)]))
    sc = mgr.scanner_public("esphome_020000000c02")
    assert sc["area_id"] == "garage" and sc["area_source"] == "device" and sc["mode"] == "raw"
    assert sc["advertisements"] == 1 and mgr.devices["02:00:00:00:0D:01"].rssi["esphome_020000000c02"][0] == -61
    detach()
    assert client.unsubscribed and mgr.scanner_public("esphome_020000000c02")["connected"] is False
    plain = DeviceInfo(name="plain", mac_address="02:00:00:00:0C:09")
    assert attach_esphome_proxy(hub, FakeDriver(), FakeClient(), plain, APIVersion(1, 12)) is None


# -- phones ----------------------------------------------------------------------------------

def _phone(hub, store, app_data=None):
    data = {"app_id": "com.example.jarvis", "app_name": "Jarvis", "app_version": "1.0", "device_name": "Alex's Phone",
            "device_id": "dev-1", "manufacturer": "Acme", "model": "Phone 1", "os_name": "Android",
            "os_version": "16", "supports_encryption": False, "user_id": "u-alex", "webhook_id": "ab" * 32,
            "app_data": app_data or {}}
    entry = ConfigEntry("e-phone", "mobile_app", "Alex's Phone", data=data, source="registration",
                        unique_id="com.example.jarvis-dev-1")
    hub.config_entries[entry.entry_id] = entry
    store.save_config_entry(entry)
    hub.devices["device-phone"] = DeviceEntry(id="device-phone", name="Alex's Phone", config_entries=["e-phone"],
                                              identifiers=[["mobile_app", "dev-1"]])
    hub.entities["device_tracker.alex_s_phone"] = EntityEntry(
        entity_id="device_tracker.alex_s_phone", platform="mobile_app", unique_id="dev-1", config_entry_id="e-phone",
        device_id="device-phone")
    return entry


async def test_a_phone_registering_its_beacon_gets_room_sensors_and_its_person_a_room(env):
    hub, store, runtime, mgr, clock, _disc = env
    entry = _phone(hub, store)
    runtime.start_entry = lambda e: None
    mam = maw.MobileAppManager(runtime)
    await ma.setup_persons(hub, [{"id": "alex", "name": "Alex", "user_id": "u-alex",
                                  "device_trackers": ["device_tracker.alex_s_phone"]}])
    hub.publish("device_tracker.alex_s_phone", "home", {"source_type": "gps", "latitude": 1.0, "longitude": 2.0})
    reg = {"app_version": "1.1", "device_name": "Alex's Phone", "manufacturer": "Acme", "model": "Phone 1"}
    bad = await maw.COMMANDS["update_registration"](mam, entry, {**reg, "app_data": {"ble_beacon": {
        "uuid": BEACON_UUID, "major": -1, "minor": 2}}})
    assert bad.status == 400 and "major" in str(bad.body)
    ok = await maw.COMMANDS["update_registration"](mam, entry, {**reg, "app_data": {"ble_beacon": {
        "uuid": BEACON_UUID, "major": 1, "minor": 2, "tx_power": -59}}})
    assert ok.status == 200
    assert entry.data["app_data"]["ble_beacon"]["major"] == 1
    room = hub.entities["sensor.alex_s_phone_room"]
    dist = hub.entities["sensor.alex_s_phone_room_distance"]
    assert room.unique_id == f"{'ab' * 32}_ble_room" and dist.unique_id == f"{'ab' * 32}_ble_room_distance"
    assert room.entity_category is None and dist.trusted("unit_of_measurement") == "m"
    tracker = mgr.trackers[bp.beacon_key(BEACON_UUID, 1, 2)]
    assert tracker.kind == "phone" and tracker.tracker_entity == "device_tracker.alex_s_phone"
    run(mgr, clock, 4, {LOFT: -58, DEN: -85})
    assert hub.states.get("sensor.alex_s_phone_room").state == "Loft"
    person = hub.states.get("person.alex")
    assert person.attributes["room"] == "Loft" and person.attributes["room_area_id"] == "loft"
    caps = entity_capabilities(hub, "person.alex")
    assert "presence_room" in caps["abilities"] and caps["details"]["room_area_id"] == "loft"
    assert "presence_room" in entity_capabilities(hub, "sensor.alex_s_phone_room")["abilities"]
    # The phone stops registering a beacon: the tracker goes and its room sensor says why.
    await maw.COMMANDS["update_registration"](mam, entry, {**reg, "app_data": {}})
    assert not mgr.trackers and hub.states.get("sensor.alex_s_phone_room").state == "unavailable"


async def test_register_with_a_malformed_beacon_is_refused(env):
    from services.homehub.core import HubError

    _hub, _store, runtime, _mgr, _clock, _disc = env
    runtime.start_entry = lambda e: None
    mam = maw.MobileAppManager(runtime)
    with pytest.raises(HubError, match="ble_beacon"):
        await mam.register("u-alex", {"app_id": "x", "app_name": "X", "app_version": "1", "device_name": "P",
                                      "manufacturer": "A", "model": "M", "os_name": "Android",
                                      "app_data": {"ble_beacon": {"uuid": BEACON_UUID}}})


# -- the room map ----------------------------------------------------------------------------

ROOMS = {"den": {DEN: -55, LOFT: -78, HALL: -74}, "loft": {DEN: -79, LOFT: -56, HALL: -70},
         "hall": {DEN: -71, LOFT: -69, HALL: -57}}


def record(mgr, clock, area, seconds=40, rng=None):
    key = bp.beacon_key(BEACON_UUID, 1, 2)
    run(mgr, clock, 2, ROOMS[area], rng=rng)
    s = mgr.start_session(area, key, seconds)
    run(mgr, clock, seconds + 2, ROOMS[area], rng=rng)
    return s, mgr.session(s["session_id"])


def test_a_mapping_session_records_samples_and_the_map_cross_validates(env):
    _hub, _store, _runtime, mgr, clock, _disc = env
    rng = random.Random(3)
    _s, st = record(mgr, clock, "den", rng=rng)
    assert st["status"] == "done" and st["samples"] >= 20 and st["scanners_heard"] == 3 and st["seconds_left"] == 0
    assert mgr.map_view()["method"] == "nearest_scanner"  # one room is not a map
    for area in ("loft", "hall", "den", "loft", "hall"):
        record(mgr, clock, area, rng=rng)
    view = mgr.map_view()
    assert view["method"] == "fingerprint"
    areas = {a["area_id"]: a for a in view["areas"]}
    assert areas["den"]["sessions"] == 2 and areas["den"]["samples"] >= 30 and areas["den"]["last_recorded"]
    assert areas["garage"]["sessions"] == 0 and areas["garage"]["accuracy"] is None
    assert view["overall_accuracy"] >= 0.9 and areas["loft"]["accuracy"] >= 0.9
    assert set(view["confusion"]) == {"den", "loft", "hall"}
    assert view["confusion"]["den"].get("den", 0) > 0 and view["stale_reason"] is None


def test_cross_validation_needs_two_sessions_of_a_room():
    one = {"den": [{"samples": [{"a": -50.0}] * 5}], "loft": [{"samples": [{"a": -80.0}] * 5}]}
    cv = bp.cross_validate(one)
    assert cv["overall"] is None and cv["per_area"] == {"den": None, "loft": None}
    assert bp.cross_validate({"den": one["den"]})["overall"] is None


def test_sessions_fail_fast_when_the_tracker_is_not_heard_or_the_area_is_unknown(env):
    _hub, _store, _runtime, mgr, clock, _disc = env
    key = bp.beacon_key(BEACON_UUID, 1, 2)
    with pytest.raises(BluetoothError) as exc:
        mgr.start_session("den", key, 60)
    assert exc.value.status == 409 and "not been heard" in str(exc.value)
    run(mgr, clock, 2, {DEN: -60})
    clock.t += 31
    with pytest.raises(BluetoothError) as exc:
        mgr.start_session("den", key, 60)
    assert exc.value.status == 409
    with pytest.raises(BluetoothError) as exc:
        mgr.start_session("attic", key, 60)
    assert exc.value.status == 404
    run(mgr, clock, 2, {DEN: -60})
    with pytest.raises(BluetoothError, match="duration_s"):
        mgr.start_session("den", key, 10)
    with pytest.raises(BluetoothError) as exc:
        mgr.start_session("den", "sensor.nobody", 60)
    assert exc.value.status == 404


def test_a_session_that_loses_the_tracker_fails_and_a_cancelled_one_keeps_nothing(env):
    _hub, _store, _runtime, mgr, clock, _disc = env
    key = bp.beacon_key(BEACON_UUID, 1, 2)
    run(mgr, clock, 2, ROOMS["den"])
    s = mgr.start_session("den", key, 30)
    run(mgr, clock, 32, {})
    st = mgr.session(s["session_id"])
    assert st["status"] == "failed" and "samples" in st["error"] and not mgr.map
    run(mgr, clock, 2, ROOMS["den"])
    s = mgr.start_session("den", key, 30)
    with pytest.raises(BluetoothError) as exc:
        mgr.start_session("loft", key, 30)
    assert exc.value.status == 409
    run(mgr, clock, 5, ROOMS["den"])
    mgr.cancel_session(s["session_id"])
    run(mgr, clock, 30, ROOMS["den"])
    assert not mgr.map and s["session_id"] not in mgr.sessions


def _mapped(env):
    _hub, _store, _runtime, mgr, clock, _disc = env
    rng = random.Random(5)
    for area in ("den", "loft", "hall", "den", "loft", "hall"):
        record(mgr, clock, area, rng=rng)
    return mgr


def test_the_estimate_uses_fingerprints_with_confidence_and_survives_a_scanner_going(env):
    hub, _store, _runtime, mgr, clock, _disc = env
    _mapped(env)
    add_tag(hub, mgr)
    run(mgr, clock, 4, ROOMS["hall"])
    st = hub.states.get("sensor.keys_room")
    assert st.state == "Hall" and st.attributes["method"] == "fingerprint" and st.attributes["confidence"] > 0.6
    # The host adapter (Hall) goes quiet: it is left out of the score, and the room still holds.
    run(mgr, clock, 70, {DEN: -71, LOFT: -69}, background=(DEN, LOFT))
    assert hub.states.get("sensor.keys_room").state == "Hall"
    assert "not hearing anything" in mgr.map_view()["stale_reason"]


def test_an_unmapped_room_wins_only_through_a_clearly_stronger_scanner(env):
    hub, _store, _runtime, mgr, clock, _disc = env
    _mapped(env)
    add_tag(hub, mgr)
    garage = "esphome_020000000a09"
    mgr.add_scanner(garage, "esphome", "Garage proxy", source="test")
    mgr.set_scanner(garage, {"area_id": "garage"})
    bg = (DEN, LOFT, HALL, garage)
    run(mgr, clock, 4, {**ROOMS["den"], garage: -70}, background=bg)
    assert hub.states.get("sensor.keys_room").state == "Den"
    run(mgr, clock, 20, {DEN: -85, LOFT: -88, HALL: -84, garage: -52}, background=bg)
    st = hub.states.get("sensor.keys_room")
    assert st.state == "Garage" and st.attributes["method"] == "nearest_scanner"
    assert "was added after the rooms were mapped" in mgr.map_view()["stale_reason"]


def test_moving_a_scanner_after_mapping_flags_the_map_stale(env):
    _hub, _store, _runtime, mgr, clock, _disc = env
    _mapped(env)
    run(mgr, clock, 2, {})
    assert mgr.map_view()["stale_reason"] is None
    mgr.set_scanner(HALL, {"area_id": "garage"})
    assert "moved to another area" in mgr.map_view()["stale_reason"]


def test_the_map_survives_a_restart_and_clears(env):
    hub, store, _runtime, mgr, clock, _disc = env
    _mapped(env)
    before = mgr.map_view()
    again = BluetoothManager(hub, store, clock=clock, wall=mgr.wall, adapter="")
    for sid, sc in mgr.scanners.items():
        again.add_scanner(sid, sc.kind, sc.name, source="test")
    run(again, clock, 2, {})
    after = again.map_view()
    assert after["method"] == "fingerprint" and after["overall_accuracy"] == before["overall_accuracy"]
    assert [a["sessions"] for a in after["areas"]] == [a["sessions"] for a in before["areas"]]
    assert again.scanner_area(DEN) == "den"  # scanner settings are stored too
    assert again.clear_map("den") == 2
    with pytest.raises(BluetoothError):
        again.clear_map("den")
    assert again.clear_map() == 4 and not store.get_kv("bluetooth_map")["areas"]


# -- the API --------------------------------------------------------------------------------

def test_the_bluetooth_api(env):
    _hub, store, runtime, mgr, clock, _disc = env
    app = build_app(runtime, Auth(store, SECRET))
    c = TestClient(app)
    assert c.get("/hub/bluetooth").status_code == 401
    body = c.get("/hub/bluetooth", headers=ADMIN).json()
    assert body["host_adapter"]["enabled"] is False and "HOMEHUB_BLUETOOTH_ADAPTER" in body["host_adapter"]["reason"]
    assert {p["domain"] for p in body["parsers"]} >= {"bthome", "xiaomi_ble", "govee_ble"}
    assert all(p["version"] for p in body["parsers"])
    run(mgr, clock, 2, {DEN: -60})
    devs = c.get("/hub/bluetooth/devices", headers=ADMIN).json()["devices"]
    beacon = next(d for d in devs if d["ibeacon"])
    assert beacon["ibeacon"] == bp.beacon_key(BEACON_UUID, 1, 2) and DEN in beacon["rssi"]
    r = c.patch(f"/hub/bluetooth/scanners/{DEN}", headers=ADMIN, json={"area_id": "Loft", "ref_power": -62})
    assert r.status_code == 200 and r.json()["area_id"] == "loft" and r.json()["ref_power"] == -62
    assert c.patch("/hub/bluetooth/scanners/nope", headers=ADMIN, json={}).status_code == 404
    assert c.patch(f"/hub/bluetooth/scanners/{DEN}", headers=ADMIN, json={"colour": 1}).status_code == 400
    c.patch(f"/hub/bluetooth/scanners/{DEN}", headers=ADMIN, json={"area_id": "den"})
    key = bp.beacon_key(BEACON_UUID, 1, 2)
    r = c.post("/hub/bluetooth/map/sessions", headers=ADMIN, json={"area_id": "attic", "tracker": key})
    assert r.status_code == 404
    r = c.post("/hub/bluetooth/map/sessions", headers=ADMIN,
               json={"area_id": "den", "tracker": bp.beacon_key(TAG_UUID, 9, 9)})
    assert r.status_code == 409 and "not been heard" in r.json()["message"]
    r = c.post("/hub/bluetooth/map/sessions", headers=ADMIN, json={"area_id": "den", "tracker": key, "duration_s": 30})
    assert r.status_code == 200 and set(r.json()) == {"session_id", "area_id", "tracker", "ends_at"}
    sid = r.json()["session_id"]
    st = c.get(f"/hub/bluetooth/map/sessions/{sid}", headers=ADMIN).json()
    assert st["status"] == "recording" and st["seconds_left"] == 30
    run(mgr, clock, 32, ROOMS["den"])
    assert c.get(f"/hub/bluetooth/map/sessions/{sid}", headers=ADMIN).json()["status"] == "done"
    view = c.get("/hub/bluetooth/map", headers=ADMIN).json()
    assert {"method", "areas", "scanners", "confusion", "overall_accuracy", "stale_reason"} <= set(view)
    assert view["overall_accuracy"] is None
    assert c.delete("/hub/bluetooth/map", headers=ADMIN).status_code == 400
    assert c.delete("/hub/bluetooth/map/den", headers=ADMIN).json()["sessions"] == 1
    assert c.delete("/hub/bluetooth/map/den", headers=ADMIN).status_code == 404
    assert c.delete("/hub/bluetooth/map?confirm=1", headers=ADMIN).json()["sessions"] == 0
    assert c.delete(f"/hub/bluetooth/map/sessions/{sid}", headers=ADMIN).status_code == 200
    assert c.get(f"/hub/bluetooth/map/sessions/{sid}", headers=ADMIN).status_code == 404


async def test_the_gateway_sets_and_clears_a_jarvis_phones_beacon_through_the_admin_api(env):
    from services.homehub.core import HubError

    hub, store, runtime, mgr, clock, _disc = env
    entry = _phone(hub, store, app_data={"push_url": "https://push.example.invalid/x", "push_token": "tok"})
    runtime.start_entry = lambda e: None
    mam = maw.MobileAppManager(runtime)
    c = TestClient(build_app(runtime, Auth(store, SECRET)))
    url = f"/hub/mobile_app/phones/{entry.entry_id}"
    beacon = {"uuid": BEACON_UUID, "major": 1, "minor": 2, "tx_power": -59, "state": "advertising"}
    r = c.patch(url, headers=ADMIN, json={"app_data": {"ble_beacon": beacon}})
    assert r.status_code == 200, r.text
    assert entry.data["app_data"]["push_url"] == "https://push.example.invalid/x"  # merged, not replaced
    assert entry.data["app_data"]["ble_beacon"]["state"] == "advertising"
    assert bp.beacon_key(BEACON_UUID, 1, 2) in mgr.trackers
    run(mgr, clock, 4, {DEN: -58})
    assert hub.states.get("sensor.alex_s_phone_room").state == "Den"
    for bad in ({"app_data": {"push_url": "x"}}, {"app_data": {"ble_beacon": {"uuid": "nope"}}}, {"app_data": []}):
        with pytest.raises(HubError):
            await mam.set_phone_options(entry.entry_id, bad)
    assert c.patch(url, headers=ADMIN, json={"app_data": {"colour": 1}}).status_code >= 400
    assert entry.data["app_data"]["push_url"] == "https://push.example.invalid/x"
    r = c.patch(url, headers=ADMIN, json={"app_data": {"ble_beacon": None}})
    assert r.status_code == 200
    assert not mgr.trackers
    for eid in ("sensor.alex_s_phone_room", "sensor.alex_s_phone_room_distance"):
        st = hub.states.get(eid)
        assert st.state == "unavailable" and st.attributes["reason"] == "room beacon turned off on the phone"
    stored = {e.entry_id: e for e in store.load_config_entries()}[entry.entry_id]
    assert stored.data["app_data"] == {"push_url": "https://push.example.invalid/x", "push_token": "tok",
                                       "ble_beacon": None}

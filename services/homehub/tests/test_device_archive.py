"""Archiving devices that stopped answering, restoring them when they come back, deleting them a year later.

Every id, name, address and key here is made up.
"""
from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from services.homehub import device_archive, entries, hardware
from services.homehub.api import build_app
from services.homehub.auth import Auth
from services.homehub.core import UNAVAILABLE, Area, ConfigEntry, DeviceEntry, EntityEntry, Hub, HubError, State
from services.homehub.device_archive import DeviceArchive
from services.homehub.recorder_statistics import Statistics
from services.homehub.runtime import Runtime
from services.homehub.store import Store
from services.homehub.tests.test_flows import FakeRuntime

SECRET = "internal-secret"
H = {"Authorization": f"Bearer {SECRET}"}
T0 = datetime(2026, 1, 1, 12, tzinfo=UTC)
MAC = "02:00:00:aa:bb:01"


class Clock:
    def __init__(self, at: datetime) -> None:
        self.at = at

    def __call__(self) -> datetime:
        return self.at

    def days(self, n: float) -> None:
        self.at += timedelta(days=n)


@pytest.fixture
def env(tmp_path):
    store = Store(str(tmp_path / "hub.db"), Fernet.generate_key().decode())
    hub = Hub(time_zone="Etc/GMT+7")
    hub.store = store
    hub.areas["den"] = Area("den", "Den")
    store.save_area(hub.areas["den"])
    rt = FakeRuntime(hub, store)
    rt.statistics = Statistics(str(tmp_path / "statistics.db"), store.path, time_zone="Etc/GMT+7")

    def add(entry, devices, ents, state="on"):
        hub.config_entries[entry.entry_id] = entry
        store.save_config_entry(entry)
        for d in devices:
            hub.devices[d.id] = d
            store.save_device(d)
        for e in ents:
            hub.entities[e.entity_id] = e
            store.save_entity(e)
            # Restored from the store, last heard at T0 (not "now": the test's clock is its own).
            hub.states.set(e.entity_id, state, {}, restore=State(e.entity_id, state, {}, T0, T0))

    # A plug on the LAN (one entry, one device) that answers.
    add(ConfigEntry("plug", "tasmota", "Den plug", {"host": "192.0.2.10", "mac": MAC}),
        [DeviceEntry("d-plug", name="Den plug", area_id="den", config_entries=["plug"],
                     connections=[["mac", MAC]])],
        [EntityEntry("switch.den_plug", "tasmota", "plug-relay", "plug", "d-plug")])
    # An ESPHome node with an encryption key (a credential sealed in the entry's data).
    add(ConfigEntry("node", "esphome", "Porch node", {"host": "192.0.2.11", "device_name": "porch-node",
                                                      "noise_psk": "made-up-key", "mac": "020000aabb02"}),
        [DeviceEntry("d-node", name="Porch node", config_entries=["node"])],
        [EntityEntry("sensor.porch_temp", "esphome", "node-temp", "node", "d-node")])
    # Matter: one entry, two nodes.
    add(ConfigEntry("fabric", "matter", "Matter", {"url": "ws://127.0.0.1:5580/ws"}),
        [DeviceEntry("d-bulb", name="Hall bulb", config_entries=["fabric"],
                     identifiers=[["matter", "deviceid_0000000000000001-0000000000000007-MatterNodeDevice"]]),
         DeviceEntry("d-lamp", name="Desk lamp", config_entries=["fabric"])],
        [EntityEntry("light.hall_bulb", "matter", "m-bulb", "fabric", "d-bulb"),
         EntityEntry("light.desk_lamp", "matter", "m-lamp", "fabric", "d-lamp")])
    # A cloud account: never archived, however quiet.
    add(ConfigEntry("cloud", "srp_energy", "Power account", {"username": "someone", "password": "pw"}),
        [], [EntityEntry("sensor.power_cost", "srp_energy", "srp-cost", "cloud")])
    for e in hub.config_entries.values():
        e.state = "loaded"
    yield hub, store, rt
    rt.statistics.close()
    store.close()


def make(rt, clock, notices=None):
    async def notifier(payload):
        if notices is not None:
            notices.append(payload)
        return True

    archive = DeviceArchive(rt, notifier=notifier, clock=clock)
    rt.archive = archive
    return archive


def live(hub, *entity_ids, value="on"):
    """A driver published a live value (what a working connection, poll or push does)."""
    for eid in entity_ids:
        hub.publish(eid, value, {})


def past_grace(archive, clock):
    archive._started_at = clock() - timedelta(seconds=device_archive.STARTUP_GRACE_S + 1)
    for entry_id in list(archive._loaded_since):
        archive._loaded_since[entry_id] = archive._started_at


# -- contact ----------------------------------------------------------------------------------

def test_contact_is_recorded_only_for_what_answers_and_survives_a_restart(env):
    hub, _store, rt = env
    clock = Clock(T0)
    archive = make(rt, clock)
    archive.load()
    live(hub, "switch.den_plug", "light.hall_bulb", "sensor.power_cost")
    clock.days(3)
    archive.refresh()
    assert archive.last_contact("entry", "plug") == clock()
    assert archive.last_contact("device", "d-bulb") == clock()
    # Restored but never published since the start: no contact (a dead node keeps its last state on restart).
    assert archive.last_contact("entry", "node") == T0
    assert archive.last_contact("device", "d-lamp") == T0
    # Not an integration that is archived.
    assert "cloud" not in archive.entry_contact
    archive.flush()
    again = DeviceArchive(rt, clock=clock)
    again.load()
    assert again.last_contact("entry", "plug") == clock()


def test_first_contact_comes_from_the_restored_states(env):
    hub, _store, rt = env
    went = T0 - timedelta(days=40)
    hub.states.set("sensor.porch_temp", UNAVAILABLE, {},
                   restore=State("sensor.porch_temp", UNAVAILABLE, {}, went, went))
    heard = T0 - timedelta(days=5)
    hub.states.set("switch.den_plug", "on", {}, restore=State("switch.den_plug", "on", {}, heard, heard))
    archive = make(rt, Clock(T0))
    archive.load()
    assert archive.last_contact("entry", "node") == went
    assert archive.last_contact("entry", "plug") == heard


# -- archiving --------------------------------------------------------------------------------

async def test_archives_after_the_threshold_once_with_one_notice(env):
    hub, store, rt = env
    clock = Clock(T0)
    notices: list[dict] = []
    archive = make(rt, clock, notices)
    archive.load()
    past_grace(archive, clock)
    clock.days(89)
    live(hub, "switch.den_plug", "light.hall_bulb")
    assert (await archive.sweep())["archived"] == []

    clock.days(2)  # the node and the desk lamp: 91 days without a word; the plug and the bulb answered lately
    hub.publish("light.hall_bulb", UNAVAILABLE, {})  # off at the wall for two days: not long enough
    result = await archive.sweep()
    assert sorted(i["id"] for i in result["archived"]) == ["d-lamp", "node"]
    node = hub.config_entries["node"]
    assert node.archived_at and node.state == "not_loaded"
    assert "node" in rt.stopped
    assert hub.states.get("sensor.porch_temp").state == UNAVAILABLE
    assert hub.is_hidden("sensor.porch_temp") and hub.is_hidden("light.desk_lamp")
    assert not hub.is_hidden("switch.den_plug")
    assert hub.config_entries["plug"].archived_at is None and hub.config_entries["fabric"].archived_at is None
    assert hub.config_entries["cloud"].archived_at is None
    # Kept: configuration, name, area, credentials.
    stored = {e.entry_id: e for e in store.load_config_entries()}
    assert stored["node"].archived_at == node.archived_at and stored["node"].data["noise_psk"] == "made-up-key"
    # One notice for the sweep, naming them; nothing more on the next sweep.
    assert len(notices) == 1 and "archived" in notices[0]["title"] and "Porch node" in notices[0]["body"]
    assert notices[0]["user"] == "@admins" and notices[0]["once"].startswith("homehub:devices-archived:")
    assert (await archive.sweep())["archived"] == []
    assert len(notices) == 1


async def test_nothing_is_archived_while_the_hub_is_starting_or_when_turned_off(env):
    _hub, _store, rt = env
    clock = Clock(T0)
    archive = make(rt, clock)
    archive.load()
    clock.days(200)
    archive._started_at = clock()
    assert (await archive.sweep())["archived"] == []
    past_grace(archive, clock)
    archive.update_settings({"enabled": False})
    assert (await archive.sweep())["archived"] == []
    assert archive.candidates()  # the dry run still says what it would do


async def test_an_archived_entry_is_never_set_up_or_retried(env):
    hub, store, _rt = env
    rt = Runtime(hub, store)
    entry = hub.config_entries["node"]
    entry.archived_at = (T0 - timedelta(days=1)).isoformat()
    rt.start_entry(entry)
    await rt.start_all()
    assert "node" not in rt._tasks
    assert entry.state == "not_loaded" and entry.reason.startswith("Archived on")


async def test_a_command_to_an_archived_device_fails_plainly(env):
    hub, _store, rt = env
    clock = Clock(T0)
    archive = make(rt, clock)
    archive.load()
    await archive.archive("entry", "node")
    await archive.archive("device", "d-lamp")
    with pytest.raises(HubError, match="device archived: Porch node"):
        await hub.call_service("sensor", "turn_on", {"entity_id": "sensor.porch_temp"})
    with pytest.raises(HubError, match="device archived: Desk lamp"):
        await hub.call_service("light", "turn_on", {"entity_id": "light.desk_lamp"})
    # The ESPHome node's own actions say so too, instead of waiting for the device as if it were away.
    assert hub.archived_action_reason("esphome", "porch_node_play_chime").startswith("device archived")
    assert hub.archived_action_reason("esphome", "other_node_play_chime") is None


async def test_archived_devices_leave_the_lists_and_the_status(env):
    hub, _store, rt = env
    archive = make(rt, Clock(T0))
    archive.load()
    await archive.archive("entry", "node")
    await archive.archive("device", "d-lamp")
    listed = {e["entry_id"] for e in entries.list_entries(rt)["entries"]}
    assert "node" not in listed and "fabric" in listed
    devices = {d["id"] for d in hardware.device_list(rt)["devices"]}
    assert "d-node" not in devices and "d-lamp" not in devices and "d-bulb" in devices
    rows = {r["domain"]: r for r in hardware.integrations(rt)["integrations"]}
    assert rows["esphome"]["archived"] == 1 and rows["esphome"]["entries"] == []
    assert "sensor.porch_temp" in hub.hidden_entity_ids()


# -- restoring --------------------------------------------------------------------------------

async def test_a_device_announcing_itself_again_is_restored_by_its_id_not_its_address(env):
    hub, _store, rt = env
    clock = Clock(T0)
    archive = make(rt, clock)
    archive.load()
    await archive.archive("entry", "plug")
    rt.started.clear()
    # Another device now has its old address: not it.
    assert archive.on_sighting({"id": "tasmota:x", "host": "192.0.2.10", "mac": "02:00:00:ff:ff:ff"}) == []
    assert hub.config_entries["plug"].archived_at
    clock.days(1)
    restored = archive.on_sighting({"id": "tasmota:plug", "host": "192.0.2.44", "mac": MAC, "source": "mdns"})
    assert restored and restored[0]["why"] == "rediscovered by mdns"
    entry = hub.config_entries["plug"]
    assert entry.archived_at is None and rt.started == ["plug"]
    assert entry.data["host"] == "192.0.2.10"  # its configuration is kept (address following moves it)
    assert archive.last_contact("entry", "plug") == clock()
    assert not hub.is_hidden("switch.den_plug")
    actions = [a["action"] for a in archive.store.get_kv(device_archive.KV_AUDIT)]
    assert actions[-2:] == ["archived", "restored"]


async def test_discovery_restores_through_its_record_hook(env):
    from services.homehub.discovery import DiscoveryManager

    hub, store, rt = env
    archive = make(rt, Clock(T0))
    archive.load()
    await archive.archive("entry", "plug")
    mgr = DiscoveryManager(hub, store)
    mgr.runtime = rt
    mgr.record({"id": "tasmota:plug", "integration": "tasmota", "host": "192.0.2.44", "mac": MAC, "ids": []})
    assert hub.config_entries["plug"].archived_at is None


async def test_a_matter_node_that_answers_again_is_restored_by_the_sweep(env):
    hub, _store, rt = env
    clock = Clock(T0)
    archive = make(rt, clock)
    archive.load()
    await archive.archive("device", "d-lamp")
    assert hub.devices["d-lamp"].archived_at
    clock.days(10)
    live(hub, "light.desk_lamp")
    result = await archive.sweep()
    assert [r["id"] for r in result["restored"]] == ["d-lamp"]
    assert hub.devices["d-lamp"].archived_at is None and not hub.is_hidden("light.desk_lamp")


async def test_an_admin_restores_one(env):
    hub, _store, rt = env
    archive = make(rt, Clock(T0))
    archive.load()
    await archive.archive("entry", "node")
    archive.restore("entry", "node")
    assert hub.config_entries["node"].archived_at is None and "node" in rt.started
    with pytest.raises(HubError, match="not archived"):
        archive.restore("entry", "node")


# -- deleting ---------------------------------------------------------------------------------

def seed_history(store, stats, entity_id, when):
    ts = when.timestamp()
    store.import_history_rows([(entity_id, "21.5", json.dumps({}), ts, ts)])
    stats.add_hourly_amounts("sensor:" + entity_id.split(".")[1], source="sensor", unit="kWh", name=None,
                             amounts=[(when, 1.0)])
    stats.upsert_meta(entity_id, source="recorder", unit="°C", has_sum=False, mean_type=1)
    stats._exec("INSERT INTO statistics(metadata_id, start_ts, mean, origin) VALUES (?, ?, ?, 0)",
                (stats.get_meta(entity_id)["id"], ts, 21.5))


async def test_deleted_a_year_after_archiving_with_history_statistics_and_credentials(env):
    hub, store, rt = env
    clock = Clock(T0)
    notices: list[dict] = []
    archive = make(rt, clock, notices)
    archive.load()
    seed_history(store, rt.statistics, "sensor.porch_temp", T0 - timedelta(days=2))
    seed_history(store, rt.statistics, "switch.den_plug", T0 - timedelta(days=2))
    await archive.archive("entry", "node")
    notices.clear()

    clock.days(364)
    assert archive.due() == []
    assert await archive.delete_due() == []
    clock.days(2)
    preview = archive.preview()
    assert [i["id"] for i in preview["delete"]] == ["node"]
    assert preview["delete"][0]["history_rows"] == 1 and preview["delete"][0]["statistics_rows"] == 1
    assert "node" in hub.config_entries  # the dry run changed nothing

    done = await archive.delete_due()
    assert [d["id"] for d in done] == ["node"]
    assert "node" not in hub.config_entries and "d-node" not in hub.devices
    assert "sensor.porch_temp" not in hub.entities and hub.states.get("sensor.porch_temp") is None
    assert "node" not in {e.entry_id for e in store.load_config_entries()}  # sealed credentials gone with it
    assert store.count_history(["sensor.porch_temp"]) == 0
    assert rt.statistics.get_meta("sensor.porch_temp") is None
    # Someone else's history is untouched.
    assert store.count_history(["switch.den_plug"]) == 1 and rt.statistics.get_meta("switch.den_plug")
    record = store.get_kv(device_archive.KV_AUDIT)[-1]
    assert record["action"] == "deleted" and record["why"] == "schedule" and record["credentials_removed"]
    assert record["history_rows"] == 1 and record["statistics_rows"] == 1
    assert len(notices) == 1 and "deleted" in notices[0]["title"]


async def test_never_deletes_something_that_came_back(env):
    hub, _store, rt = env
    clock = Clock(T0)
    archive = make(rt, clock)
    archive.load()
    await archive.archive("device", "d-lamp")
    clock.days(200)
    archive.touch_device("d-lamp", clock())  # it answered (before the sweep could restore it)
    clock.days(200)
    assert archive.due() == []
    with pytest.raises(HubError, match="answered since it was archived"):
        await archive.delete("device", "d-lamp")
    assert "d-lamp" in hub.devices


async def test_a_matter_node_is_deleted_without_its_fabric(env):
    hub, _store, rt = env
    archive = make(rt, Clock(T0))
    archive.load()
    await archive.archive("device", "d-lamp")
    out = await archive.delete("device", "d-lamp")
    assert out["status"] == "deleted" and "d-lamp" not in hub.devices
    assert "fabric" in hub.config_entries and "d-bulb" in hub.devices


async def test_only_archived_items_can_be_deleted(env):
    _hub, _store, rt = env
    archive = make(rt, Clock(T0))
    archive.load()
    with pytest.raises(HubError, match="not archived"):
        await archive.delete("entry", "plug")


# -- phones -----------------------------------------------------------------------------------

def test_a_phone_counts_only_its_own_reports(env):
    hub, store, rt = env
    entry = ConfigEntry("phone", "mobile_app", "Old phone", {"device_name": "Old phone", "webhook_id": "w1"})
    hub.config_entries["phone"] = entry
    store.save_config_entry(entry)
    entry.state = "loaded"

    class Phones:
        seen: float | None = None

        def last_seen(self, entry_id):
            return self.seen

    hub.mobile_app_manager = Phones()
    clock = Clock(T0)
    archive = make(rt, clock)
    archive.load()
    clock.days(100)
    archive.refresh()
    assert archive.last_contact("entry", "phone") == T0  # loaded, but the phone itself never said a word
    hub.mobile_app_manager.seen = (T0 + timedelta(days=99)).timestamp()
    archive.refresh()
    assert archive.last_contact("entry", "phone") == T0 + timedelta(days=99)


async def test_an_archived_phone_that_reports_is_restored(env):
    hub, store, rt = env
    entry = ConfigEntry("phone", "mobile_app", "Old phone", {"device_name": "Old phone", "webhook_id": "w1"})
    hub.config_entries["phone"] = entry
    store.save_config_entry(entry)

    class Phones:
        seen: float | None = None

        def last_seen(self, entry_id):
            return self.seen

    hub.mobile_app_manager = Phones()
    clock = Clock(T0)
    archive = make(rt, clock)
    archive.load()
    await archive.archive("entry", "phone")
    assert hub.archived_action_reason("notify", "mobile_app_old_phone").startswith("device archived")
    clock.days(5)
    hub.mobile_app_manager.seen = clock().timestamp()
    result = await archive.sweep()
    assert [r["id"] for r in result["restored"]] == ["phone"] and entry.archived_at is None


# -- the API ----------------------------------------------------------------------------------

def test_api_lists_previews_restores_deletes_and_validates_settings(env):
    hub, store, rt = env
    clock = Clock(T0)
    archive = make(rt, clock)
    archive.load()
    asyncio.run(archive.archive("entry", "node"))
    c = TestClient(build_app(rt, Auth(store, SECRET)))
    assert c.get("/hub/archive").status_code == 401
    body = c.get("/hub/archive", headers=H).json()
    [item] = body["items"]
    assert item["id"] == "node" and item["kind"] == "entry" and item["label"]
    assert item["delete_on"].startswith("2027-01-01")
    assert body["settings"] == {"enabled": True, "archive_after_days": 90, "delete_after_days": 365}
    assert c.get("/hub/archive/preview", headers=H).json()["delete"] == []

    assert c.put("/hub/archive/settings", json={"archive_after_days": 3}, headers=H).status_code == 400
    assert c.put("/hub/archive/settings", json={"archive_after_days": True}, headers=H).status_code == 400
    assert c.put("/hub/archive/settings", json={"later": 1}, headers=H).status_code == 400
    ok = c.put("/hub/archive/settings", json={"archive_after_days": 120, "delete_after_days": 400}, headers=H)
    assert ok.json()["settings"]["archive_after_days"] == 120
    assert c.get("/hub/archive", headers=H).json()["items"][0]["delete_on"].startswith("2027-02-05")

    assert c.post("/hub/archive/restore", json={"kind": "nope", "id": "node"}, headers=H).status_code == 400
    assert c.post("/hub/archive/restore", json={"kind": "entry", "id": "missing"}, headers=H).status_code == 404
    assert c.post("/hub/archive/delete", json={"kind": "entry", "id": "plug"}, headers=H).status_code == 409
    assert c.post("/hub/archive/restore", json={"kind": "entry", "id": "node"}, headers=H).json()["status"] == "restored"
    assert c.get("/hub/archive", headers=H).json()["items"] == []

    asyncio.run(archive.archive("entry", "node"))
    gone = c.post("/hub/archive/delete", json={"kind": "entry", "id": "node"}, headers=H).json()
    assert gone["status"] == "deleted" and "node" not in hub.config_entries
    assert store.get_kv(device_archive.KV_AUDIT)[-1]["why"] == "admin"

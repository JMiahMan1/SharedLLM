"""Following a device set up by IP to its new address when the router moves it.

Documentation addresses (192.0.2.0/24) and made-up MACs.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from services.homehub import address_follow as af
from services.homehub import entries
from services.homehub.core import ConfigEntry, DeviceEntry, EntityEntry, Hub

CAM_MAC, BULB_MAC, EXTENDER = "02:00:00:00:00:93", "02:00:00:00:01:63", "02:00:00:00:02:30"


def camera_hub(*, mac: str | None = None, state: str = "idle"):
    hub = Hub(time_zone="UTC")
    entry = ConfigEntry("cam1", "mjpeg", "3D Printer Camera", {}, options={
        "mjpeg_url": "http://192.0.2.163:8080/", "still_image_url": "http://user:pw@192.0.2.163:8080/?action=snapshot"})
    entry.state = "loaded"
    hub.config_entries["cam1"] = entry
    hub.devices["d1"] = DeviceEntry(id="d1", name="3D Printer Camera", config_entries=["cam1"],
                                    connections=[["mac", mac]] if mac else [])
    hub.entities["camera.printer"] = EntityEntry("camera.printer", "mjpeg", "p", "cam1", "d1")
    hub.publish("camera.printer", state, {})
    return hub, entry


def test_addresses_are_read_from_data_or_options_and_rewritten_keeping_the_rest():
    _hub, entry = camera_hub()
    assert af.addresses(entry) == {"mjpeg_url": ("options", "192.0.2.163"), "still_image_url": ("options", "192.0.2.163")}
    assert af.with_new_ip("http://user:pw@192.0.2.163:8080/?action=snapshot", "192.0.2.93") == \
        "http://user:pw@192.0.2.93:8080/?action=snapshot"
    assert af.with_new_ip("192.0.2.163:6053", "192.0.2.93") == "192.0.2.93:6053"
    assert af.with_new_ip("192.0.2.163", "192.0.2.93") == "192.0.2.93"
    host = ConfigEntry("e", "esphome", "x", {"host": "garage.local"})
    assert af.addresses(host) == {}  # a host name follows the device by itself


def test_a_working_device_learns_its_mac_and_a_failing_one_does_not():
    hub, _entry = camera_hub()
    learn, moves = af.plan(hub, {"192.0.2.163": CAM_MAC}, set())
    assert [(d.id, m) for d, m in learn] == [("d1", "020000000093")] and moves == []
    af.learn_mac(hub, hub.devices["d1"], "020000000093")
    assert ["mac", CAM_MAC] in hub.devices["d1"].connections  # stored as the registry writes MACs
    # Not answering: its old address may already be someone else's, so nothing is learned.
    hub2, _ = camera_hub(state="unavailable")
    assert af.plan(hub2, {"192.0.2.163": BULB_MAC}, set()) == ([], [])


def test_a_device_with_a_known_mac_is_followed_to_its_new_address():
    hub, entry = camera_hub(mac=CAM_MAC, state="unavailable")
    # The router gave the camera .93 and its old address to a bulb.
    learn, moves = af.plan(hub, {"192.0.2.93": CAM_MAC, "192.0.2.163": BULB_MAC}, set())
    assert learn == [] and [(m.old_ip, m.new_ip, m.mac) for m in moves] == [("192.0.2.163", "192.0.2.93", CAM_MAC)]
    af.apply_move(hub, moves[0])
    assert entry.options["mjpeg_url"] == "http://192.0.2.93:8080/"
    assert entry.options["still_image_url"] == "http://user:pw@192.0.2.93:8080/?action=snapshot"
    assert af.plan(hub, {"192.0.2.93": CAM_MAC}, set()) == ([], [])  # already there: nothing more


def test_an_extenders_mac_and_an_ambiguous_one_are_never_followed():
    hub, _entry = camera_hub(mac=EXTENDER, state="unavailable")
    neighbours = {"192.0.2.93": EXTENDER, "192.0.2.94": EXTENDER}
    assert af.plan(hub, neighbours, {EXTENDER}) == ([], [])
    assert af.plan(hub, neighbours, set())[1] == []  # seen at two addresses: no guess


def test_a_followed_device_that_vanished_asks_for_an_early_sweep():
    hub, _entry = camera_hub(mac=CAM_MAC, state="unavailable")
    assert af.lost(hub, {"192.0.2.163": BULB_MAC}) is True
    assert af.lost(hub, {"192.0.2.93": CAM_MAC}) is False
    working, _ = camera_hub(mac=CAM_MAC)
    assert af.lost(working, {}) is False


class _Store:
    def __init__(self):
        self.saved = []

    def save_config_entry(self, entry):
        self.saved.append(entry.entry_id)

    def save_device(self, device):
        pass


class _Runtime:
    def __init__(self, hub):
        self.hub, self.store = hub, _Store()
        self.stopped: list[str] = []
        self.started: list[str] = []

    async def stop_entry(self, entry_id):
        self.stopped.append(entry_id)

    def start_entry(self, entry):
        self.started.append(entry.entry_id)


async def test_discovery_follows_the_device_reloads_it_and_tells_admins(monkeypatch, tmp_path):
    from services.homehub import discovery

    hub, entry = camera_hub(mac=CAM_MAC, state="unavailable")
    hub.store = _Store()
    sent: list[dict] = []

    async def notifier(payload):
        sent.append(payload)
        return True

    mgr = discovery.DiscoveryManager(hub, SimpleNamespace(get_kv=lambda *a, **k: {}, set_kv=lambda *a, **k: None),
                                     notifier=notifier)
    rt = _Runtime(hub)
    mgr.runtime = rt
    arp = tmp_path / "arp"
    arp.write_text("IP address       HW type     Flags       HW address            Mask     Device\n"
                   f"192.0.2.93       0x1         0x2         {CAM_MAC}     *        eth0\n"
                   f"192.0.2.163      0x1         0x2         {BULB_MAC}     *        eth0\n")
    monkeypatch.setenv("HOMEHUB_ARP_TABLE", str(arp))
    await mgr.follow_addresses()
    assert entry.options["mjpeg_url"] == "http://192.0.2.93:8080/"
    assert rt.stopped == ["cam1"] and rt.started == ["cam1"]
    assert len(sent) == 1 and "192.0.2.93" in sent[0]["body"] and "nothing needs doing" in sent[0]["body"]


async def test_an_admin_can_change_a_cameras_stream_and_snapshot_urls():
    hub, entry = camera_hub()
    rt = _Runtime(hub)
    fields = {f["key"] for f in entries.reconfigure_fields(entry)}
    assert {"mjpeg_url", "still_image_url"} <= fields
    await entries.reconfigure_entry(rt, "cam1", {"data": {"mjpeg_url": "http://192.0.2.93:8080/"}})
    assert entry.options["mjpeg_url"] == "http://192.0.2.93:8080/" and "mjpeg_url" not in entry.data
    assert rt.started == ["cam1"]
    with pytest.raises(entries.EntryError):
        await entries.reconfigure_entry(rt, "cam1", {"data": {"mjpeg_url": ""}})
    with pytest.raises(entries.EntryError):
        await entries.reconfigure_entry(rt, "cam1", {"data": {"still_image_url": "file:///etc/passwd"}})

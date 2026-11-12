"""A MAC shared by several addresses (a range extender) is no device's id; loopback never goes stale.

A Wi-Fi extender answers ARP for every device behind it with its own MAC. Taken
as an identity, that one MAC merged a camera hub, a vacuum and a TV into "the TV
moved". Every id, address and MAC here is made up (RFC 5737, 02:00:00 MACs).
"""
from __future__ import annotations

from services.homehub import discovery
from services.homehub.core import ConfigEntry
from services.homehub.tests.test_hardware import env  # noqa: F401  (the shared fixture)

EXTENDER = "02:00:00:aa:bb:cc"
ARP = f"""IP address       HW type     Flags       HW address            Mask     Device
192.0.2.30       0x1         0x2         {EXTENDER}     *        eth0
192.0.2.31       0x1         0x2         {EXTENDER}     *        eth0
192.0.2.66       0x1         0x2         {EXTENDER}     *        eth0
192.0.2.70       0x1         0x2         02:00:00:00:00:70     *        eth0
"""


def test_a_mac_at_several_addresses_is_shared():
    neighbours = discovery.parse_arp(ARP)
    assert discovery.shared_macs(neighbours) == {"020000aabbcc"}


async def test_an_extender_mac_makes_no_finding_and_fills_no_mac(env, monkeypatch):  # noqa: F811
    _hub, _store, rt, _sent = env
    mgr = rt.discovery
    # A finding an earlier scan made from the extender's MAC, and one that got it filled in.
    mgr.record({"id": "roborock:020000aabbcc", "integration": "roborock", "host": "192.0.2.31",
                "mac": "020000aabbcc", "ids": ["020000aabbcc"], "source": "dhcp", "props": {}})
    mgr.record({"id": "roku:x00000000001", "integration": "roku", "host": "192.0.2.66", "mac": "020000aabbcc",
                "ids": ["X00000000001", "020000aabbcc"], "source": "ssdp", "props": {}})
    monkeypatch.setattr(mgr, "read_arp", lambda: ARP)

    async def names(host):
        return {"192.0.2.31": "roborock-vacuum-a1.lan"}.get(host)

    monkeypatch.setattr(mgr, "reverse_name", names)
    await mgr._scan_dhcp()
    assert "roborock:020000aabbcc" not in mgr.findings
    roku = mgr.findings["roku:x00000000001"]
    assert roku["mac"] is None and roku["ids"] == ["X00000000001"]


def test_a_service_on_the_hub_reached_by_loopback_has_not_moved(env):  # noqa: F811
    hub, _store, rt, _sent = env
    entry = ConfigEntry("ma1", "music_assistant", "Music Assistant", {"url": "http://127.0.0.1:8095"},
                        unique_id="0123abcd")
    entry.state = "loaded"
    hub.config_entries["ma1"] = entry
    f = discovery.parse_mdns("_mass._tcp.local.", "abc._mass._tcp.local.", "192.0.2.5", 8095,
                             {b"server_id": b"0123abcd", b"base_url": b"http://192.0.2.5:8095",
                              b"server_version": b"2.6.0"})
    rt.discovery.record(f)
    pub = rt.discovery.public(f)
    assert pub["known"] is True and pub["moved"] is None

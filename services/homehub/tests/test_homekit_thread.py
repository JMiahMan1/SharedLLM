"""HomeKit over Thread: route checks, CoAP pairing, the flow and the driver.

Addresses are documentation prefixes (2001:db8::/32, 192.0.2.x); the routing
table is a fake ``/proc/net/ipv6_route``; no packet is ever sent.
"""
from __future__ import annotations

import asyncio
import ipaddress
from typing import ClassVar

import pytest

from services.homehub import flows, homekit_thread, pairing
from services.homehub.core import ConfigEntry, DeviceUnavailable
from services.homehub.discovery import parse_mdns
from services.homehub.drivers import homekit as homekit_driver
from services.homehub.drivers.homekit import HomeKitDriver
from services.homehub.tests.conftest import make_hub
from services.homehub.tests.test_flows import FakeRuntime, rt  # noqa: F401 (fixture)
from services.homehub.tests.test_homekit_services import BRIDGE, FakePairing

HK_ID = "AA:BB:CC:00:00:01"
ACC = "2001:db8:7:1::42"


def _route_line(net: str, dev: str) -> str:
    n = ipaddress.IPv6Network(net)
    zero = "0" * 32
    return (f"{int(n.network_address):032x} {n.prefixlen:02x} {zero} 00 {zero} 00000100 00000001 00000000 "
            f"00000001 {dev:>8}")


@pytest.fixture
def routes(tmp_path):
    def write(*entries: tuple[str, str]) -> str:
        path = tmp_path / "ipv6_route"
        path.write_text("\n".join(_route_line(n, d) for n, d in entries) + "\n")
        return str(path)

    return write


def thread_finding(host: str = ACC) -> dict:
    return parse_mdns("_hap._udp.local.", "Hall Sensor._hap._udp.local.", host, 5683,
                      {b"sf": b"1", b"id": HK_ID.encode(), b"md": b"Sensor", b"ci": b"10", b"ff": b"2",
                       b"c#": b"3"})


async def no_routers():
    return []


async def two_routers():
    return ["Living Room Hub", "Office Border Router"]


# -- reachability ------------------------------------------------------------------------------

def test_thread_addresses_must_be_ipv6():
    assert str(homekit_thread.thread_address("[2001:db8::5%eth0]")) == "2001:db8::5"
    with pytest.raises(flows.FlowError, match="not an IPv6 address"):
        homekit_thread.thread_address("192.0.2.80")
    with pytest.raises(flows.FlowError, match="no usable address"):
        homekit_thread.thread_address("hall-sensor.local.")


def test_routes_are_read_and_the_most_specific_one_wins(routes):
    path = routes(("::/0", "eth0"), ("2001:db8:7::/48", "eth0"), ("2001:db8:7:1::/64", "wlan0"), ("::1/128", "lo"))
    table = homekit_thread.ipv6_routes(path)
    assert len(table) == 4
    assert homekit_thread.route_to(ipaddress.IPv6Address(ACC), table)[1] == "wlan0"
    assert homekit_thread.route_to(ipaddress.IPv6Address("2001:db8:9::1"), table) is None  # only the default
    with pytest.raises(Exception, match="Cannot read the IPv6 routing table"):
        homekit_thread.ipv6_routes(path + ".missing")


async def test_a_route_to_the_thread_network_is_enough(routes):
    addr, dev = await homekit_thread.require_route(ACC, routes_path=routes(("2001:db8:7:1::/64", "eth0")),
                                                   border_routers=no_routers)
    assert str(addr) == ACC and dev == "eth0"


async def test_no_border_router_fails_at_once_and_says_so(routes):
    with pytest.raises(flows.FlowError) as err:
        await homekit_thread.require_route(ACC, routes_path=routes(("::/0", "eth0")), border_routers=no_routers)
    assert err.value.status == 409 and "No Thread border router was found" in str(err.value)


async def test_a_border_router_without_a_route_names_the_fix(routes):
    with pytest.raises(flows.FlowError) as err:
        await homekit_thread.require_route(ACC, routes_path=routes(("::/0", "eth0")), border_routers=two_routers)
    msg = str(err.value)
    assert "Living Room Hub, Office Border Router" in msg and "accept_ra_rt_info_max_plen=64" in msg


async def test_a_failed_border_router_browse_is_reported_not_hidden(routes):
    async def broken():
        raise OSError("no multicast here")

    with pytest.raises(flows.FlowError, match="no multicast here"):
        await homekit_thread.require_route(ACC, routes_path=routes(("::/0", "eth0")), border_routers=broken)


# -- pairing over CoAP --------------------------------------------------------------------------

class FakeDiscovery:
    """Stands in for aiohomekit's CoAPDiscovery: pair setup with a code, a CoAP pairing back."""

    made: ClassVar[list[FakeDiscovery]] = []

    def __init__(self, controller, description, *, stall: bool = False):
        self.controller, self.description, self.stall = controller, description, stall
        self.closed = False
        FakeDiscovery.made.append(self)

    async def async_start_pairing(self, alias):
        if self.stall:
            await asyncio.sleep(3600)
        self.alias = alias

        async def finish(code):
            self.code = code
            fake = FakePairing(BRIDGE)
            fake.pairing_data = {"AccessoryPairingID": HK_ID, "AccessoryIP": self.description.address,
                                 "AccessoryPort": self.description.port, "Connection": "CoAP",
                                 "iOSPairingId": "made-up-controller", "iOSDeviceLTSK": "00", "AccessoryLTPK": "00"}
            return fake

        return finish

    async def close(self):
        self.closed = True


async def test_pair_over_coap_returns_coap_pairing_data(routes):
    FakeDiscovery.made.clear()
    data, accessories = await homekit_thread.pair(
        thread_finding(), "123-45-678", "entry-x", routes_path=routes(("2001:db8:7::/48", "eth0")),
        border_routers=no_routers, make_discovery=FakeDiscovery)
    d = FakeDiscovery.made[0]
    assert d.alias == "entry-x" and d.code == "123-45-678" and d.closed
    assert d.description.type == "_hap._udp.local." and d.description.address == ACC and d.description.port == 5683
    assert d.description.id == HK_ID.lower() and int(d.description.feature_flags) == 2
    assert data["Connection"] == "CoAP" and data["AccessoryIP"] == ACC
    assert accessories == BRIDGE


async def test_pair_never_starts_when_there_is_no_route(routes):
    FakeDiscovery.made.clear()
    with pytest.raises(flows.FlowError, match="No Thread border router"):
        await homekit_thread.pair(thread_finding(), "123-45-678", "x", routes_path=routes(("::/0", "eth0")),
                                  border_routers=no_routers, make_discovery=FakeDiscovery)
    assert FakeDiscovery.made == []


async def test_a_silent_accessory_times_out_with_a_reason(routes):
    def stalled(controller, desc):
        return FakeDiscovery(controller, desc, stall=True)

    with pytest.raises(flows.FlowError) as err:
        await homekit_thread.pair(thread_finding(), "123-45-678", "x", routes_path=routes(("2001:db8:7::/48", "eth0")),
                                  border_routers=no_routers, make_discovery=stalled, timeout=0.05)
    assert err.value.status == 504 and "did not answer" in str(err.value)


async def test_aiohomekits_own_coap_discovery_builds_the_pairing(routes, monkeypatch):
    """The real CoAPDiscovery/CoAPPairing with the CoAP exchange faked at the connection."""
    from aiohomekit.controller.coap.connection import CoAPHomeKitConnection
    from aiohomekit.controller.coap.pairing import CoAPPairing

    seen = {}

    async def setup(self, with_auth):
        seen["address"] = self.address
        return b"salt", b"srpB"

    async def finish(self, pin, salt, srp_b):
        seen["pin"] = pin
        return {"AccessoryPairingID": HK_ID, "AccessoryLTPK": "00", "iOSPairingId": "made-up-controller",
                "iOSDeviceLTSK": "00", "iOSDeviceLTPK": "00"}

    async def accessories(self):
        return BRIDGE

    monkeypatch.setattr(CoAPHomeKitConnection, "do_pair_setup", setup)
    monkeypatch.setattr(CoAPHomeKitConnection, "do_pair_setup_finish", finish)
    monkeypatch.setattr(CoAPPairing, "list_accessories_and_characteristics", accessories)
    data, accs = await homekit_thread.pair(thread_finding(), "123-45-678", "entry-y",
                                           routes_path=routes(("2001:db8:7::/48", "eth0")), border_routers=no_routers)
    assert seen == {"address": f"[{ACC}]:5683", "pin": "123-45-678"}
    assert data["Connection"] == "CoAP" and data["AccessoryIP"] == ACC and data["AccessoryPort"] == 5683
    assert accs == BRIDGE


# -- the flow -----------------------------------------------------------------------------------

async def test_thread_accessory_is_adopted_through_the_flow(rt, routes, monkeypatch):  # noqa: F811
    path = routes(("2001:db8:7::/48", "eth0"))
    real = homekit_thread.pair

    async def pair(finding, code, alias, **kw):
        return await real(finding, code, alias, routes_path=path, border_routers=no_routers,
                          make_discovery=FakeDiscovery)

    monkeypatch.setattr(homekit_thread, "pair", pair)
    finding = thread_finding()
    assert flows.unsupported_reason(finding) is None
    assert flows.needs_for(finding)[0]["key"] == "code"
    result = await flows.adopt(rt, finding, {"name": "Hall sensor", "input": {"code": "12345678"}})
    entry = rt.hub.config_entries[result["entry_id"]]
    assert entry.domain == "homekit_controller" and entry.unique_id == HK_ID.lower()
    assert entry.data["Connection"] == "CoAP" and entry.data["AccessoryIP"] == ACC
    assert rt.started == [entry.entry_id]


async def test_the_flow_passes_the_border_router_reason_through(rt, routes, monkeypatch):  # noqa: F811
    path = routes(("::/0", "eth0"))
    real = homekit_thread.pair

    async def pair(finding, code, alias, **kw):
        return await real(finding, code, alias, routes_path=path, border_routers=no_routers)

    monkeypatch.setattr(homekit_thread, "pair", pair)
    with pytest.raises(flows.FlowError) as err:
        await flows.adopt(rt, thread_finding(), {"input": {"code": "123-45-678"}})
    assert err.value.status == 409 and "No Thread border router" in str(err.value)
    assert not rt.hub.config_entries


async def test_the_ip_path_is_unchanged(monkeypatch):
    called = []

    async def thread_pair(*a, **kw):
        called.append(a)

    monkeypatch.setattr(homekit_thread, "pair", thread_pair)
    finding = parse_mdns("_hap._tcp.local.", "Porch._hap._tcp.local.", "192.0.2.60", 51826,
                         {b"sf": b"1", b"id": b"AA:BB:CC:00:00:02", b"ci": b"2"})
    assert finding["props"]["transport"] == "ip"

    class Boom(Exception):
        pass

    def boom(*a, **kw):
        raise Boom()

    monkeypatch.setattr("aiohomekit.controller.ip.controller.IpController", boom)
    with pytest.raises(Boom):
        await pairing.homekit_pair(finding, "123-45-678", "x")
    assert called == []


# -- the driver ---------------------------------------------------------------------------------

def coap_entry(**data):
    return {"AccessoryPairingID": HK_ID, "AccessoryIP": ACC, "AccessoryPort": 5683, "Connection": "CoAP",
            "iOSPairingId": "made-up-controller", "iOSDeviceLTSK": "00", "AccessoryLTPK": "00", **data}


async def test_load_pairing_uses_aiohomekits_coap_controller(routes):
    from aiohomekit.controller.coap.pairing import CoAPPairing

    entry = ConfigEntry("hk-thread", "homekit_controller", "Hall sensor", coap_entry())
    controller, p = await homekit_thread.load_pairing(entry, routes_path=routes(("2001:db8:7::/48", "eth0")))
    assert isinstance(p, CoAPPairing) and p.connection.address == f"[{ACC}]:5683"
    assert controller.aliases["hk-thread"] is p


async def test_load_pairing_without_a_route_is_unavailable_with_the_reason(routes):
    entry = ConfigEntry("hk-thread", "homekit_controller", "Hall sensor", coap_entry())
    with pytest.raises(DeviceUnavailable, match="no route to the Thread network"):
        await homekit_thread.load_pairing(entry, routes_path=routes(("::/0", "eth0")))


async def test_driver_drives_a_thread_accessory(monkeypatch):
    hub, entry = make_hub("homekit_controller", data=coap_entry(), entities=[
        {"entity_id": "light.porch_light", "unique_id": f"{HK_ID}_2_10"}])
    fake = FakePairing(BRIDGE)
    seen = {}

    async def load(e, zc):
        seen["entry"], seen["zc"] = e.entry_id, zc
        return "coap-controller", fake

    async def stub_zeroconf():
        return "shared-zeroconf"

    monkeypatch.setattr(homekit_thread, "load_pairing", load)
    monkeypatch.setattr(homekit_driver, "_shared_zeroconf", stub_zeroconf)
    drv = HomeKitDriver(hub, entry)
    await drv.async_setup()
    assert seen == {"entry": entry.entry_id, "zc": "shared-zeroconf"}
    assert drv._controller == "coap-controller"
    assert hub.states.get("light.porch_light").state == "on"


async def test_driver_still_refuses_unknown_transports():
    hub, entry = make_hub("homekit_controller", data=coap_entry(Connection="BLE"))
    with pytest.raises(Exception, match="only IP and CoAP"):
        await HomeKitDriver(hub, entry).async_setup()

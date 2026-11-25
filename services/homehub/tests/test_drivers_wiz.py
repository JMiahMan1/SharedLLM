"""WiZ driver: states from getPilot, commands as setPilot, a silent bulb, and the UDP transport."""
from __future__ import annotations

import asyncio
import json

import pytest

from services.homehub.core import DeviceUnavailable, ServiceNotFound
from services.homehub.drivers import wiz
from services.homehub.drivers.wiz import WizDriver, udp_request
from services.homehub.tests.conftest import make_hub

MAC = "02000000010a"


def tw_entities(mac: str = MAC) -> list[dict]:
    return [
        {"entity_id": f"light.wiz_{mac}", "unique_id": mac},
        {"entity_id": f"number.wiz_{mac}_effect_speed", "unique_id": f"{mac}_effect_speed"},
        {"entity_id": f"sensor.wiz_{mac}_power", "unique_id": f"{mac}_power"},
    ]


class FakeBulb:
    """Answers like a real bulb; the replies are the shapes a live ESP21_SHTW_01 sent."""

    def __init__(self, module: str = "ESP21_SHTW_01", cct=(2700, 2700, 5000, 5000)) -> None:
        self.pilot = {"mac": MAC, "rssi": -15, "state": True, "sceneId": 0, "temp": 2700, "dimming": 100}
        self.module = module
        self.cct = list(cct)
        self.online = True
        self.calls: list[tuple[str, dict]] = []

    async def __call__(self, method: str, params: dict | None = None) -> dict:
        self.calls.append((method, dict(params or {})))
        if not self.online:
            raise DeviceUnavailable("WiZ bulb did not answer")
        if method == "getPilot":
            return dict(self.pilot)
        if method == "getSystemConfig":
            return {"mac": MAC, "moduleName": self.module, "fwVersion": "1.29.1"}
        if method == "getModelConfig":
            return {"cctRange": self.cct}
        if method == "getPower":
            return {"power": 8500}
        if method == "setPilot":
            p = dict(params or {})
            if "sceneId" in p:
                self.pilot.pop("temp", None)
                for c in "rgb":
                    self.pilot.pop(c, None)
            if "r" in p:
                self.pilot.pop("temp", None)
                self.pilot["sceneId"] = 0
            if "temp" in p:
                for c in "rgb":
                    self.pilot.pop(c, None)
                self.pilot["sceneId"] = 0
            self.pilot.update(p)
            return {"success": True}
        raise AssertionError(method)


def make_driver(bulb: FakeBulb, entities=None, title="WiZ Tunable White EC7291"):
    hub, entry = make_hub("wiz", {"host": "192.0.2.12"}, tw_entities() if entities is None else entities)
    entry.title = title
    drv = WizDriver(hub, entry)
    drv._request = bulb  # type: ignore[method-assign]
    hub.drivers[entry.entry_id] = drv
    return hub, drv


async def test_setup_publishes_the_light_speed_and_power():
    bulb = FakeBulb()
    hub, drv = make_driver(bulb)
    await drv.async_setup()
    light = hub.states.get(f"light.wiz_{MAC}")
    assert light.state == "on"
    assert light.attributes["brightness"] == 255
    assert light.attributes["color_mode"] == "color_temp"
    assert light.attributes["color_temp_kelvin"] == 2700
    assert light.attributes["supported_color_modes"] == ["color_temp"]
    assert (light.attributes["min_color_temp_kelvin"], light.attributes["max_color_temp_kelvin"]) == (2700, 5000)
    assert "Cozy" in light.attributes["effect_list"] and "Ocean" not in light.attributes["effect_list"]
    assert light.attributes["supported_features"] == 4
    assert hub.states.get(f"sensor.wiz_{MAC}_power").state == "8.5"
    assert hub.states.get(f"sensor.wiz_{MAC}_power").attributes["unit_of_measurement"] == "W"


async def test_a_bulb_off_at_the_wall_loads_unavailable_and_polls_fast():
    """Bathroom bulbs lose power at the switch many times a day; that is not a broken entry."""
    from services.homehub.drivers import wiz

    bulb = FakeBulb()
    bulb.online = False
    hub, drv = make_driver(bulb)
    await drv.async_setup()  # does not raise
    assert hub.states.get(drv.light.entity_id).state == "unavailable"
    assert drv.poll_interval == wiz.OFFLINE_POLL_S
    bulb.online = True  # the switch goes back on
    await drv.async_poll()
    assert hub.states.get(drv.light.entity_id).state == "on"
    assert drv.poll_interval == wiz.ONLINE_POLL_S
    assert drv.module == "ESP21_SHTW_01"  # identified once it answered


async def test_a_command_reaches_a_bulb_that_just_came_back_before_the_next_poll():
    bulb = FakeBulb()
    bulb.online = False
    hub, drv = make_driver(bulb)
    await drv.async_setup()
    bulb.online = True
    await drv.async_call(drv.light, "light", "turn_off", {})
    assert bulb.pilot["state"] is False
    assert hub.states.get(drv.light.entity_id).state == "off"


async def test_an_entry_without_entities_gets_them_from_the_bulb_like_the_flow_makes_them():
    bulb = FakeBulb()
    hub, drv = make_driver(bulb, entities=[])
    hub.devices["dev1"].config_entries = ["another-entry"]
    drv.entry.unique_id = MAC
    await drv.async_setup()
    by_uid = {e.unique_id: e for e in hub.entities.values() if e.config_entry_id == drv.entry.entry_id}
    assert set(by_uid) == {MAC, f"{MAC}_effect_speed", f"{MAC}_power"}
    light = by_uid[MAC]
    assert light.entity_id.startswith("light.") and light.device_id
    dev = hub.devices[light.device_id]
    assert dev.manufacturer == "WiZ" and ["mac", "02:00:00:00:01:0a"] in dev.connections
    assert by_uid[f"{MAC}_power"].original_device_class == "power"
    assert by_uid[f"{MAC}_effect_speed"].entity_category == "config"
    assert hub.states.get(light.entity_id).state == "on"
    assert hub.states.get(by_uid[f"{MAC}_power"].entity_id).state == "8.5"


async def test_an_unmetered_bulb_gets_no_power_sensor_and_a_silent_one_retries_setup():
    bulb = FakeBulb()
    bulb.online = False
    hub, drv = make_driver(bulb, entities=[])
    with pytest.raises(DeviceUnavailable, match="cannot be made"):
        await drv.async_setup()
    assert not [e for e in hub.entities.values() if e.config_entry_id == drv.entry.entry_id]
    bulb.online = True
    real = bulb.__call__

    async def unmetered(method, params=None):
        if method == "getPower":
            return {}
        return await real(method, params)

    drv._request = unmetered  # type: ignore[method-assign]
    await drv.async_setup()
    uids = {e.unique_id for e in hub.entities.values() if e.config_entry_id == drv.entry.entry_id}
    assert uids == {MAC, f"{MAC}_effect_speed"}


async def test_disabled_entities_are_not_made_again():
    bulb = FakeBulb()
    hub, drv = make_driver(bulb, entities=[{"entity_id": "light.kept", "unique_id": MAC, "disabled_by": "user"}])
    await drv.async_setup()
    assert [e.entity_id for e in hub.entities.values()] == ["light.kept"]


async def test_poll_marks_a_vanished_bulb_unavailable_and_recovers(monkeypatch, caplog):
    monkeypatch.setattr(wiz, "RECHECK_AFTER_S", 0)
    bulb = FakeBulb()
    hub, drv = make_driver(bulb)
    await drv.async_setup()
    bulb.online = False
    await drv.async_poll()  # must not raise
    assert hub.states.get(f"light.wiz_{MAC}").state == "on"  # one missed poll is not enough
    assert drv.poll_interval == wiz.OFFLINE_POLL_S  # but it is asked again soon
    await drv.async_poll()
    assert hub.states.get(f"light.wiz_{MAC}").state == "unavailable"
    assert hub.states.get(f"sensor.wiz_{MAC}_power").state == "unavailable"
    assert "unreachable" in caplog.text
    bulb.online = True
    bulb.pilot["state"] = False
    await drv.async_poll()
    assert hub.states.get(f"light.wiz_{MAC}").state == "off"
    assert hub.states.get(f"light.wiz_{MAC}").attributes["brightness"] is None


async def test_turn_on_with_brightness_and_kelvin_sends_set_pilot_and_reads_back():
    bulb = FakeBulb()
    hub, drv = make_driver(bulb)
    await drv.async_setup()
    res = await hub.call_service("light", "turn_on",
                                 {"entity_id": f"light.wiz_{MAC}", "brightness_pct": 40, "color_temp_kelvin": 9000})
    sent = [p for m, p in bulb.calls if m == "setPilot"][-1]
    assert sent == {"state": True, "dimming": 40, "temp": 5000}  # clamped to the bulb's range
    st = hub.states.get(f"light.wiz_{MAC}")
    assert st.attributes["brightness"] == round(40 * 255 / 100)
    assert st.attributes["color_temp_kelvin"] == 5000
    assert any(s.entity_id == f"light.wiz_{MAC}" for s in res["changed_states"])


async def test_brightness_never_goes_below_the_bulbs_floor_and_zero_turns_off():
    bulb = FakeBulb()
    hub, drv = make_driver(bulb)
    await drv.async_setup()
    await hub.call_service("light", "turn_on", {"entity_id": f"light.wiz_{MAC}", "brightness": 3})
    assert [p for m, p in bulb.calls if m == "setPilot"][-1]["dimming"] == 10
    await hub.call_service("light", "turn_on", {"entity_id": f"light.wiz_{MAC}", "brightness": 0})
    assert [p for m, p in bulb.calls if m == "setPilot"][-1] == {"state": False}


async def test_toggle_and_turn_off():
    bulb = FakeBulb()
    hub, drv = make_driver(bulb)
    await drv.async_setup()
    await hub.call_service("light", "toggle", {"entity_id": f"light.wiz_{MAC}"})
    assert hub.states.get(f"light.wiz_{MAC}").state == "off"
    await hub.call_service("light", "toggle", {"entity_id": f"light.wiz_{MAC}"})
    assert hub.states.get(f"light.wiz_{MAC}").state == "on"
    await hub.call_service("light", "turn_off", {"entity_id": f"light.wiz_{MAC}"})
    assert [p for m, p in bulb.calls if m == "setPilot"][-1] == {"state": False}


async def test_rgb_bulb_takes_colours_and_scenes():
    bulb = FakeBulb(module="ESP01_SHRGB1C_31", cct=(2200, 2200, 6500, 6500))
    hub, drv = make_driver(bulb, title="WiZ RGBW Tunable 7398B2")
    await drv.async_setup()
    eid = f"light.wiz_{MAC}"
    assert hub.states.get(eid).attributes["supported_color_modes"] == ["color_temp", "rgb"]
    await hub.call_service("light", "turn_on", {"entity_id": eid, "rgb_color": [255, 0, 40]})
    assert [p for m, p in bulb.calls if m == "setPilot"][-1] == {"state": True, "r": 255, "g": 0, "b": 40}
    st = hub.states.get(eid)
    assert st.attributes["color_mode"] == "rgb" and st.attributes["rgb_color"] == [255, 0, 40]
    await hub.call_service("light", "turn_on", {"entity_id": eid, "hs_color": [120, 100]})
    assert [p for m, p in bulb.calls if m == "setPilot"][-1] == {"state": True, "r": 0, "g": 255, "b": 0}
    await hub.call_service("light", "turn_on", {"entity_id": eid, "effect": "ocean"})
    assert [p for m, p in bulb.calls if m == "setPilot"][-1] == {"state": True, "sceneId": 1}
    assert hub.states.get(eid).attributes["effect"] == "Ocean"
    with pytest.raises(ServiceNotFound):
        await hub.call_service("light", "turn_on", {"entity_id": eid, "effect": "Disco inferno"})


async def test_effect_speed_number():
    bulb = FakeBulb()
    hub, drv = make_driver(bulb)
    await drv.async_setup()
    await hub.call_service("number", "set_value", {"entity_id": f"number.wiz_{MAC}_effect_speed", "value": 500})
    assert [p for m, p in bulb.calls if m == "setPilot"][-1] == {"speed": 200}
    assert hub.states.get(f"number.wiz_{MAC}_effect_speed").state == "200"


async def test_a_command_to_a_silent_bulb_raises_device_unavailable():
    bulb = FakeBulb()
    hub, drv = make_driver(bulb)
    await drv.async_setup()
    bulb.online = False
    with pytest.raises(DeviceUnavailable):
        await hub.call_service("light", "turn_on", {"entity_id": f"light.wiz_{MAC}"})
    assert hub.states.get(f"light.wiz_{MAC}").state == "unavailable"


# -- the UDP transport, against a stand-in bulb on the loopback interface ----------
class _StandIn(asyncio.DatagramProtocol):
    def __init__(self, drop_first: int = 0, silent: bool = False) -> None:
        self.drop_first = drop_first
        self.silent = silent
        self.seen = 0

    def connection_made(self, transport) -> None:
        self.transport = transport

    def datagram_received(self, data: bytes, addr) -> None:
        self.seen += 1
        if self.silent or self.seen <= self.drop_first:
            return
        req = json.loads(data)
        reply = {"method": req["method"], "env": "", "result": {"state": True, "dimming": 55}}
        self.transport.sendto(json.dumps(reply).encode(), addr)


async def _serve(proto: _StandIn):
    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(lambda: proto, local_addr=("127.0.0.1", 0))
    return transport, transport.get_extra_info("sockname")[1]


async def test_udp_request_resends_after_a_lost_datagram(monkeypatch):
    monkeypatch.setattr(wiz, "RESEND_EVERY", 0.05)
    proto = _StandIn(drop_first=1)
    transport, port = await _serve(proto)
    try:
        result = await udp_request("127.0.0.1", "getPilot", port=port, timeout=1.0)
    finally:
        transport.close()
    assert result == {"state": True, "dimming": 55}
    assert proto.seen >= 2


async def test_udp_request_times_out_with_device_unavailable(monkeypatch):
    monkeypatch.setattr(wiz, "RESEND_EVERY", 0.05)
    proto = _StandIn(silent=True)
    transport, port = await _serve(proto)
    try:
        with pytest.raises(DeviceUnavailable, match="did not answer"):
            await udp_request("127.0.0.1", "getPilot", port=port, timeout=0.3)
    finally:
        transport.close()


async def test_one_missed_poll_does_not_make_a_working_bulb_unavailable(monkeypatch, caplog):
    monkeypatch.setattr(wiz, "RECHECK_AFTER_S", 0)
    bulb = FakeBulb()
    hub, drv = make_driver(bulb)
    await drv.async_setup()
    misses = iter([True])
    real = bulb.__call__

    async def flaky(method, params=None):
        if method == "getPilot" and next(misses, False):
            raise DeviceUnavailable("WiZ bulb did not answer getPilot")
        return await real(method, params)

    drv._request = flaky
    await drv.async_poll()
    assert hub.states.get(f"light.wiz_{MAC}").state == "on"
    assert "unreachable" not in caplog.text
    assert drv.poll_interval == wiz.ONLINE_POLL_S


async def test_an_unreachable_bulb_names_the_error_even_when_it_has_no_text(monkeypatch):
    loop = asyncio.get_running_loop()

    async def fails(*_a, **_k):
        raise OSError()

    monkeypatch.setattr(loop, "create_datagram_endpoint", fails)
    with pytest.raises(wiz.WizTransportError, match=r"could not open a socket: OSError$"):
        await udp_request("192.0.2.5", "getPilot", timeout=0.1)


# -- 2026-11-18: working bulbs called unreachable because the hub could not open a socket in time ----------

async def test_the_transport_never_asks_the_loop_to_resolve_the_bulbs_address(monkeypatch):
    """uvloop resolves ``remote_addr`` on libuv's 4-thread pool, which slow DNS lookups had filled."""
    loop = asyncio.get_running_loop()
    real = loop.create_datagram_endpoint
    seen: list[dict] = []

    async def spy(factory, *args, **kwargs):
        seen.append(dict(kwargs))
        return await real(factory, *args, **kwargs)

    async def no_dns(*_a, **_k):
        raise AssertionError("a numeric bulb address must not be resolved")

    monkeypatch.setattr(loop, "create_datagram_endpoint", spy)
    monkeypatch.setattr(loop, "getaddrinfo", no_dns)
    monkeypatch.setattr(wiz, "RESEND_EVERY", 0.05)
    proto = _StandIn()
    transport, port = await _serve(proto)
    try:
        assert await udp_request("127.0.0.1", "getPilot", port=port, timeout=1.0) == {"state": True, "dimming": 55}
    finally:
        transport.close()
    bulb_call = seen[-1]
    assert "remote_addr" not in bulb_call and bulb_call.get("sock") is not None


def test_the_transport_works_on_uvloop_the_loop_the_hub_runs_on():
    uvloop = pytest.importorskip("uvloop")

    async def main():
        wiz.RESEND_EVERY, old = 0.05, wiz.RESEND_EVERY
        proto = _StandIn(drop_first=1)
        transport, port = await _serve(proto)
        try:
            return await udp_request("127.0.0.1", "getPilot", port=port, timeout=1.0)
        finally:
            transport.close()
            wiz.RESEND_EVERY = old

    loop = uvloop.new_event_loop()
    try:
        assert loop.run_until_complete(main()) == {"state": True, "dimming": 55}
    finally:
        loop.close()


async def test_replies_from_another_address_are_ignored():
    proto = wiz._UDPReply("getPilot", "192.0.2.9")
    proto.datagram_received(json.dumps({"method": "getPilot", "result": {"state": True}}).encode(), ("192.0.2.8", 38899))
    assert not proto.future.done()
    proto.datagram_received(json.dumps({"method": "getPilot", "result": {"state": True}}).encode(), ("192.0.2.9", 38899))
    assert proto.future.result()["result"] == {"state": True}


async def test_a_poll_the_hub_could_not_send_leaves_the_bulb_as_it_was(caplog):
    bulb = FakeBulb()
    hub, drv = make_driver(bulb)
    await drv.async_setup()

    async def no_socket(method, params=None):
        raise wiz.WizTransportError("no UDP socket")

    drv._request = no_socket
    for _ in range(wiz.MISSES_BEFORE_UNAVAILABLE + 1):
        await drv.async_poll()
    assert hub.states.get(f"light.wiz_{MAC}").state == "on"
    assert "poll not sent" in caplog.text


async def test_a_bulb_shown_unavailable_is_sent_the_command_straight_away(monkeypatch):
    """No getPilot first: the command finds out whether it is back, and the reply is not delayed by a probe."""
    monkeypatch.setattr(wiz, "RECHECK_AFTER_S", 0)
    bulb = FakeBulb()
    hub, drv = make_driver(bulb)
    await drv.async_setup()
    bulb.online = False
    for _ in range(wiz.MISSES_BEFORE_UNAVAILABLE):
        await drv.async_poll()
    assert hub.states.get(drv.light.entity_id).state == "unavailable"
    bulb.online = True
    bulb.calls.clear()
    await hub.call_service("light", "turn_off", {"entity_id": drv.light.entity_id})
    assert bulb.calls[0] == ("setPilot", {"state": False})
    assert hub.states.get(drv.light.entity_id).state == "off"
    assert drv.poll_interval == wiz.ONLINE_POLL_S


async def test_a_silent_bulb_cannot_hold_a_command_past_its_budget(monkeypatch):
    monkeypatch.setattr(wiz, "COMMAND_BUDGET_S", 0.2)
    bulb = FakeBulb()
    hub, drv = make_driver(bulb)
    await drv.async_setup()

    async def hangs(method, params=None):
        await asyncio.sleep(3600)

    drv._request = hangs
    # The budget, not the hour-long hang, ends it (wait_for would stop a missing budget at a
    # minute with a TimeoutError, not DeviceUnavailable). No stopwatch: a shared runner can stall.
    with pytest.raises(DeviceUnavailable, match="within"):
        await asyncio.wait_for(drv.async_call(drv.light, "light", "turn_on", {}), 60)
    assert hub.states.get(drv.light.entity_id).state == "unavailable"


async def test_one_silent_bulb_does_not_stop_the_others_in_the_same_call(monkeypatch):
    """A room is one call with several entities: each bulb is sent its command at once.

    Checked by order of events, not by a stopwatch: a wall-clock bound on a shared CI runner
    failed once (1.1 s for a 0.3 s budget) when the runner stalled the loop. The silent bulb is
    named first, so a call that waited for it before the next bulb would reach the good bulb
    only after the budget had cut the silent one off.
    """
    monkeypatch.setattr(wiz, "COMMAND_BUDGET_S", 0.3)
    hub, entry = make_hub("wiz", {"host": "192.0.2.12"}, tw_entities())
    good = WizDriver(hub, entry)
    bulb = FakeBulb()
    hub.drivers[entry.entry_id] = good
    from services.homehub.core import ConfigEntry, EntityEntry

    other = ConfigEntry(entry_id="entry2", domain="wiz", title="silent", data={"host": "192.0.2.13"})
    hub.config_entries[other.entry_id] = other
    hub.entities["light.silent"] = EntityEntry(entity_id="light.silent", unique_id="02000000010b", platform="wiz",
                                               config_entry_id="entry2", device_id="dev1")
    silent = WizDriver(hub, other)
    events: list[str] = []

    async def hangs(method, params=None):
        events.append("silent asked")
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            events.append("silent cut off")
            raise

    async def answers(method, params=None):
        if method == "setPilot":
            events.append("good asked")
        return await bulb(method, params)

    silent._request = hangs  # type: ignore[method-assign]
    hub.drivers[other.entry_id] = silent
    good._request = answers  # type: ignore[method-assign]
    await good.async_setup()
    await asyncio.wait_for(
        hub.call_service("light", "turn_off", {"entity_id": ["light.silent", good.light.entity_id]}), 60)
    # The good bulb was asked while the silent one was still waiting, and the budget (not the
    # hour-long hang, which wait_for would have stopped at a minute) ended the silent one.
    assert events == ["silent asked", "good asked", "silent cut off"]
    assert hub.states.get(good.light.entity_id).state == "off"
    assert hub.states.get("light.silent").state == "unavailable"

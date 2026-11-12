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


async def test_an_entry_without_entities_loads_without_touching_the_network():
    bulb = FakeBulb()
    _, drv = make_driver(bulb, entities=[])
    await drv.async_setup()
    await drv.async_poll()
    assert bulb.calls == []


async def test_poll_marks_a_vanished_bulb_unavailable_and_recovers(caplog):
    bulb = FakeBulb()
    hub, drv = make_driver(bulb)
    await drv.async_setup()
    bulb.online = False
    await drv.async_poll()  # must not raise
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

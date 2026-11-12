"""MatterDriver against a fake Matter Server speaking the WebSocket protocol.

The fake answers the way python-matter-server and the matter.js server do:
``server_info`` on connect, ``start_listening`` with every node, results keyed
by ``message_id``, errors as ``error_code``/``details``, and
``attribute_updated`` events as ``[node_id, path, value]``. Node data and
unique_ids are shaped like the real export (fabric 23D10E1156185BE0).
"""
from __future__ import annotations

import asyncio
import copy
import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from services.homehub.core import UNAVAILABLE, DeviceUnavailable, HubError, ServiceNotFound
from services.homehub.drivers import matter as matter_mod
from services.homehub.drivers.matter import MatterDriver, parse_unique_id
from services.homehub.tests.conftest import make_hub

FABRIC = 0x23D10E1156185BE0
CT_NODE = 0x1E   # WiZ A19 Filament: colour temperature only
COLOR_NODE = 0x26  # LIGHTINGINSIDE: colour temperature, hue/saturation and xy


def uid(node: int, endpoint: int, key: str, cluster: int, attribute: int) -> str:
    return f"{FABRIC:016X}-{node:016X}-MatterNodeDevice-{endpoint}-{key}-{cluster}-{attribute}"


CT_ATTRS = {
    "0/40/1": "Signify", "0/40/3": "WiZ A19 Filament", "0/40/9": 0, "0/40/10": "v1.0",
    "0/51/1": 7, "0/51/2": 3600, "0/54/4": -61,
    "1/3/0": 0, "1/3/1": 1,
    "1/6/0": True, "1/6/16387": 0, "1/6/65532": 1,
    "1/8/0": 254, "1/8/2": 1, "1/8/3": 254, "1/8/16": 0, "1/8/17": 254, "1/8/18": 0, "1/8/19": 5,
    "1/8/16384": None, "1/8/65532": 3,
    "1/768/7": 370, "1/768/8": 2, "1/768/16395": 200, "1/768/16396": 370, "1/768/16400": 250,
    "1/768/65532": 16,
}
COLOR_ATTRS = {
    "0/40/9": 1, "0/40/10": "1.1.0",
    "1/3/0": 0,
    "1/6/0": False, "1/6/16387": None, "1/6/65532": 1,
    "1/8/0": 4, "1/8/2": 1, "1/8/3": 254, "1/8/17": None, "1/8/16384": None, "1/8/65532": 3,
    "1/768/0": 6, "1/768/1": 170, "1/768/3": 0, "1/768/4": 0, "1/768/7": 370, "1/768/8": 2,
    "1/768/16395": 153, "1/768/16396": 370, "1/768/16400": None, "1/768/65532": 25,
}

ENTITIES = [
    {"entity_id": "light.hall_bathroom_1_light", "unique_id": uid(CT_NODE, 1, "MatterLight", 6, 0),
     "capabilities": {"max_color_temp_kelvin": 5000, "min_color_temp_kelvin": 2702,
                      "supported_color_modes": ["color_temp"]}, "supported_features": 32},
    {"entity_id": "light.master_bedroom_lamp_left", "unique_id": uid(COLOR_NODE, 1, "MatterLight", 6, 0),
     "capabilities": {"max_color_temp_kelvin": 6535, "min_color_temp_kelvin": 2702,
                      "supported_color_modes": ["color_temp", "hs", "xy"]}, "supported_features": 32},
    {"entity_id": "number.hall_bathroom_1_off_transition_time",
     "unique_id": uid(CT_NODE, 1, "off_transition_time", 8, 19),
     "capabilities": {"max": 65534, "min": 0, "mode": "box", "step": 0.1}, "unit_of_measurement": "s"},
    {"entity_id": "number.hall_bathroom_1_on_level", "unique_id": uid(CT_NODE, 1, "on_level", 8, 17),
     "capabilities": {"max": 255, "min": 0, "mode": "box", "step": 1}},
    {"entity_id": "number.hall_bathroom_1_power_on_level",
     "unique_id": uid(CT_NODE, 1, "power_on_level", 8, 16384),
     "capabilities": {"max": 255, "min": 0, "mode": "box", "step": 1}},
    {"entity_id": "number.hall_bathroom_1_power_on_color_temperature",
     "unique_id": uid(CT_NODE, 1, "power_on_color_temperature", 768, 16400),
     "capabilities": {"max": 5000, "min": 0, "mode": "box", "step": 1}, "unit_of_measurement": "K"},
    {"entity_id": "select.hall_bathroom_1_power_on_behavior_on_startup",
     "unique_id": uid(CT_NODE, 1, "MatterStartUpOnOff", 6, 16387),
     "capabilities": {"options": ["on", "off", "toggle", "previous"]}},
    {"entity_id": "select.master_bedroom_lamp_left_power_on_behavior_on_startup",
     "unique_id": uid(COLOR_NODE, 1, "MatterStartUpOnOff", 6, 16387),
     "capabilities": {"options": ["on", "off", "toggle", "previous"]}},
    {"entity_id": "button.hall_bathroom_1_identify", "unique_id": uid(CT_NODE, 1, "IdentifyButton", 3, 65529),
     "original_device_class": "identify"},
    {"entity_id": "update.master_bedroom_lamp_left", "unique_id": uid(COLOR_NODE, 0, "MatterUpdate", 40, 9),
     "supported_features": 23},
    {"entity_id": "sensor.hall_bathroom_1_wi_fi_rssi", "unique_id": uid(CT_NODE, 0, "WiFiDiagnosticsRssi", 54, 4),
     "capabilities": {"state_class": "measurement"}, "unit_of_measurement": "dBm"},
    {"entity_id": "sensor.hall_bathroom_1_reboot_count",
     "unique_id": uid(CT_NODE, 0, "GeneralDiagnosticsRebootCount", 51, 1)},
    {"entity_id": "sensor.hall_bathroom_1_uptime", "unique_id": uid(CT_NODE, 0, "GeneralDiagnosticsUpTime", 51, 2)},
]


class FakeMatterServer:
    """Just enough of the Matter Server to drive the hub's client."""

    def __init__(self, compressed_fabric_id: int = FABRIC) -> None:
        self.info = {"fabric_id": 2, "compressed_fabric_id": compressed_fabric_id, "schema_version": 14,
                     "min_supported_schema_version": 11, "sdk_version": "matter-server/1.5.5",
                     "wifi_credentials_set": False, "thread_credentials_set": False,
                     "bluetooth_enabled": False}
        self.nodes = {
            CT_NODE: {"node_id": CT_NODE, "available": True, "is_bridge": False, "attributes": dict(CT_ATTRS)},
            COLOR_NODE: {"node_id": COLOR_NODE, "available": True, "is_bridge": False,
                         "attributes": dict(COLOR_ATTRS)},
        }
        self.commands: list[dict] = []   # every device_command / write_attribute received
        self.errors: dict[str, dict] = {}  # command -> {"error_code", "details"} to answer with
        self.sockets: list[web.WebSocketResponse] = []
        self.connections = 0
        self.server: TestServer | None = None

    @property
    def url(self) -> str:
        return str(self.server.make_url("/ws"))

    async def start(self) -> None:
        app = web.Application()
        app.router.add_get("/ws", self.handler)
        self.server = TestServer(app)
        await self.server.start_server()

    async def close(self) -> None:
        for ws in list(self.sockets):
            await ws.close()
        await self.server.close()

    async def drop_clients(self) -> None:
        for ws in list(self.sockets):
            await ws.close()

    async def emit(self, event: str, data) -> None:
        for ws in list(self.sockets):
            await ws.send_str(json.dumps({"event": event, "data": data}))

    async def set_attr(self, node_id: int, path: str, value) -> None:
        self.nodes[node_id]["attributes"][path] = value
        await self.emit("attribute_updated", [node_id, path, value])

    async def handler(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.connections += 1
        self.sockets.append(ws)
        await ws.send_str(json.dumps(self.info))
        try:
            async for msg in ws:
                req = json.loads(msg.data)
                await self.answer(ws, req)
        finally:
            self.sockets.remove(ws)
        return ws

    async def answer(self, ws: web.WebSocketResponse, req: dict) -> None:
        mid, cmd, args = req["message_id"], req["command"], req.get("args") or {}
        if cmd in self.errors:
            await ws.send_str(json.dumps({"message_id": mid, **self.errors[cmd]}))
            return
        result = None
        if cmd == "start_listening":
            result = copy.deepcopy(list(self.nodes.values()))
        elif cmd == "device_command":
            self.commands.append({"command": cmd, **args})
            await self.apply_command(args)
        elif cmd == "write_attribute":
            self.commands.append({"command": cmd, **args})
            self.nodes[args["node_id"]]["attributes"][args["attribute_path"]] = args["value"]
            ep, cl, at = (int(x) for x in args["attribute_path"].split("/"))
            result = [{"Path": {"EndpointId": ep, "ClusterId": cl, "AttributeId": at}, "Status": 0}]
        elif cmd == "read_attribute":
            attrs = self.nodes[args["node_id"]]["attributes"]
            paths = args["attribute_path"]
            paths = paths if isinstance(paths, list) else [paths]
            result = {p: attrs[p] for p in paths if p in attrs}
        else:
            await ws.send_str(json.dumps({"message_id": mid, "error_code": 9, "details": f"unknown {cmd}"}))
            return
        await ws.send_str(json.dumps({"message_id": mid, "result": result}))

    async def apply_command(self, args: dict) -> None:
        """Change node attributes the way a bulb would, without events (the read-back must find them)."""
        a = self.nodes[args["node_id"]]["attributes"]
        ep, name, p = args["endpoint_id"], args["command_name"], args["payload"]
        if name == "On":
            a[f"{ep}/6/0"] = True
        elif name == "Off":
            a[f"{ep}/6/0"] = False
        elif name == "MoveToLevelWithOnOff":
            a[f"{ep}/8/0"] = p["level"]
            a[f"{ep}/6/0"] = p["level"] > 0
        elif name == "MoveToColorTemperature":
            a[f"{ep}/768/7"], a[f"{ep}/768/8"] = p["colorTemperatureMireds"], 2
        elif name == "MoveToHueAndSaturation":
            a[f"{ep}/768/0"], a[f"{ep}/768/1"], a[f"{ep}/768/8"] = p["hue"], p["saturation"], 0
        elif name == "MoveToColor":
            a[f"{ep}/768/3"], a[f"{ep}/768/4"], a[f"{ep}/768/8"] = p["colorX"], p["colorY"], 1


@pytest.fixture
async def server():
    srv = FakeMatterServer()
    await srv.start()
    yield srv
    await srv.close()


def build(url: str, entities=None):
    hub, entry = make_hub("matter", data={"url": url, "use_addon": True, "integration_created_addon": True},
                          entities=entities if entities is not None else ENTITIES)
    return hub, MatterDriver(hub, entry)


@pytest.fixture
async def setup(server):
    hub, drv = build(server.url)
    await drv.async_setup()
    hub.drivers[drv.entry.entry_id] = drv
    yield hub, drv, server
    await drv.async_unload()


async def until(cond, timeout: float = 3.0) -> None:
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not cond():
        if loop.time() > end:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.01)


def device_commands(server: FakeMatterServer) -> list[tuple]:
    return [(c["cluster_id"], c["command_name"], c["payload"]) for c in server.commands if c["command"] == "device_command"]


# -- unique_id ---------------------------------------------------------------

def test_parse_unique_id_matches_every_export_shape():
    shapes = [
        ("23D10E1156185BE0-000000000000001E-MatterNodeDevice-1-MatterLight-6-0", (0x1E, 1, "MatterLight", 6, 0)),
        ("23D10E1156185BE0-0000000000000026-MatterNodeDevice-0-MatterUpdate-40-9", (0x26, 0, "MatterUpdate", 40, 9)),
        ("23D10E1156185BE0-000000000000001E-MatterNodeDevice-1-IdentifyButton-3-1", (0x1E, 1, "IdentifyButton", 3, 1)),
        ("23D10E1156185BE0-000000000000001E-MatterNodeDevice-1-on_off_transition_time-8-16",
         (0x1E, 1, "on_off_transition_time", 8, 16)),
        ("23D10E1156185BE0-000000000000001E-MatterNodeDevice-1-power_on_color_temperature-768-16400",
         (0x1E, 1, "power_on_color_temperature", 768, 16400)),
        ("23D10E1156185BE0-000000000000001E-MatterNodeDevice-1-MatterStartUpOnOff-6-16387",
         (0x1E, 1, "MatterStartUpOnOff", 6, 16387)),
    ]
    for unique_id, (node, ep, key, cluster, attr) in shapes:
        ref = parse_unique_id(unique_id)
        assert ref is not None, unique_id
        assert (ref.fabric, ref.node_id, ref.endpoint, ref.key, ref.cluster, ref.attribute) == \
               (FABRIC, node, ep, key, cluster, attr)
    assert parse_unique_id("a8bb50d2bc6c") is None


def test_the_addon_host_name_is_replaced(monkeypatch):
    monkeypatch.delenv("HOMEHUB_MATTER_URL", raising=False)
    _, drv = build("ws://core-matter-server:5580/ws", entities=[])
    assert drv.url == "ws://localhost:5580/ws"
    monkeypatch.setenv("HOMEHUB_MATTER_URL", "ws://192.0.2.205:5580/ws")
    _, drv = build("ws://core-matter-server:5580/ws", entities=[])
    assert drv.url == "ws://192.0.2.205:5580/ws"


# -- states ------------------------------------------------------------------

async def test_setup_publishes_lights_as_home_assistant_did(setup):
    hub, _, _ = setup
    ct = hub.states.get("light.hall_bathroom_1_light")
    assert ct.state == "on"
    assert ct.attributes["brightness"] == 255  # level 254 of 1..254
    assert ct.attributes["color_mode"] == "color_temp"
    assert ct.attributes["supported_color_modes"] == ["color_temp"]
    assert ct.attributes["color_temp_kelvin"] == 2703  # 370 mireds
    assert (ct.attributes["min_color_temp_kelvin"], ct.attributes["max_color_temp_kelvin"]) == (2703, 5000)
    assert ct.attributes["supported_features"] == 32

    color = hub.states.get("light.master_bedroom_lamp_left")
    assert color.state == "off"
    assert color.attributes["supported_color_modes"] == ["color_temp", "hs", "xy"]
    assert color.attributes["brightness"] is None and color.attributes["color_mode"] is None


async def test_config_entities_and_diagnostics(setup):
    hub, _, _ = setup
    st = hub.states.get
    assert st("number.hall_bathroom_1_off_transition_time").state == "0.5"
    assert st("number.hall_bathroom_1_off_transition_time").attributes["step"] == 0.1
    assert st("number.hall_bathroom_1_on_level").state == "254"
    assert st("number.hall_bathroom_1_power_on_level").state == "255"  # null = previous level
    assert st("number.hall_bathroom_1_power_on_color_temperature").state == "4000"  # 250 mireds
    assert st("select.hall_bathroom_1_power_on_behavior_on_startup").state == "off"
    assert st("select.master_bedroom_lamp_left_power_on_behavior_on_startup").state == "previous"
    assert st("button.hall_bathroom_1_identify").state == "unknown"
    upd = st("update.master_bedroom_lamp_left")
    assert upd.state == "off" and upd.attributes["installed_version"] == "1.1.0"
    assert st("sensor.hall_bathroom_1_wi_fi_rssi").state == "-61"
    assert st("sensor.hall_bathroom_1_wi_fi_rssi").attributes["unit_of_measurement"] == "dBm"
    assert st("sensor.hall_bathroom_1_reboot_count").state == "7"
    assert "T" in st("sensor.hall_bathroom_1_uptime").state  # a boot timestamp


async def test_attribute_events_update_the_state(setup):
    hub, _, server = setup
    await server.set_attr(CT_NODE, "1/8/0", 127)
    await until(lambda: hub.states.get("light.hall_bathroom_1_light").attributes["brightness"] == 127)
    await server.set_attr(CT_NODE, "1/6/0", False)
    await until(lambda: hub.states.get("light.hall_bathroom_1_light").state == "off")


async def test_unreachable_node_is_unavailable_and_comes_back(setup):
    hub, _, server = setup
    node = copy.deepcopy(server.nodes[CT_NODE])
    node["available"] = False
    await server.emit("node_updated", node)
    await until(lambda: hub.states.get("light.hall_bathroom_1_light").state == UNAVAILABLE)
    assert hub.states.get("number.hall_bathroom_1_on_level").state == UNAVAILABLE
    assert hub.states.get("light.master_bedroom_lamp_left").state == "off"  # other node untouched
    entity = hub.entities["light.hall_bathroom_1_light"]
    with pytest.raises(DeviceUnavailable):
        await hub.drivers["entry1"].async_call(entity, "light", "turn_on", {})
    node["available"] = True
    await server.emit("node_updated", node)
    await until(lambda: hub.states.get("light.hall_bathroom_1_light").state == "on")


async def test_node_removed_marks_its_entities_unavailable(setup):
    hub, _, server = setup
    await server.emit("node_removed", COLOR_NODE)
    await until(lambda: hub.states.get("light.master_bedroom_lamp_left").state == UNAVAILABLE)


# -- light services ------------------------------------------------------------

async def test_turn_on_with_colour_temperature_and_brightness(setup):
    hub, _, server = setup
    await hub.call_service("light", "turn_on", {"entity_id": "light.hall_bathroom_1_light",
                                                "color_temp_kelvin": 4000, "brightness_pct": 50, "transition": 2})
    assert device_commands(server) == [
        (768, "MoveToColorTemperature", {"colorTemperatureMireds": 250, "transitionTime": 20,
                                         "optionsMask": 1, "optionsOverride": 1}),
        (8, "MoveToLevelWithOnOff", {"level": 128, "transitionTime": 20, "optionsMask": 0, "optionsOverride": 0}),
    ]
    st = hub.states.get("light.hall_bathroom_1_light")
    assert st.attributes["color_temp_kelvin"] == 4000
    assert st.attributes["brightness"] == 129  # level 128 read back: 1..254 -> 1..255


async def test_colour_temperature_is_clamped_to_the_bulb(setup):
    hub, _, server = setup
    await hub.call_service("light", "turn_on", {"entity_id": "light.hall_bathroom_1_light",
                                                "color_temp_kelvin": 6500})
    assert device_commands(server)[0][2]["colorTemperatureMireds"] == 200  # 5000 K, its coolest
    assert device_commands(server)[1][:2] == (6, "On")


async def test_turn_on_hs_and_rgb_colours(setup):
    hub, _, server = setup
    await hub.call_service("light", "turn_on", {"entity_id": "light.master_bedroom_lamp_left", "hs_color": [180, 50]})
    assert device_commands(server)[0] == (768, "MoveToHueAndSaturation",
                                          {"hue": 127, "saturation": 127, "transitionTime": 0,
                                           "optionsMask": 1, "optionsOverride": 1})
    assert device_commands(server)[1][:2] == (6, "On")
    st = hub.states.get("light.master_bedroom_lamp_left")
    assert st.state == "on" and st.attributes["color_mode"] == "hs"
    assert st.attributes["hs_color"] == (180.0, 50.0)

    server.commands.clear()
    await hub.call_service("light", "turn_on", {"entity_id": "light.master_bedroom_lamp_left",
                                                "rgb_color": [255, 0, 0]})
    name, payload = device_commands(server)[0][1:]
    assert name == "MoveToColor"
    assert (payload["colorX"], payload["colorY"]) == (int(0.7006 * 65536), int(0.2993 * 65536))
    assert hub.states.get("light.master_bedroom_lamp_left").attributes["color_mode"] == "xy"


async def test_turn_off_and_toggle(setup):
    hub, _, server = setup
    await hub.call_service("light", "turn_off", {"entity_id": "light.hall_bathroom_1_light"})
    assert device_commands(server) == [(6, "Off", {})]
    assert hub.states.get("light.hall_bathroom_1_light").state == "off"

    server.commands.clear()
    await hub.call_service("light", "toggle", {"entity_id": "light.hall_bathroom_1_light"})
    assert device_commands(server) == [(6, "On", {})]
    assert hub.states.get("light.hall_bathroom_1_light").state == "on"

    server.commands.clear()
    await hub.call_service("light", "turn_off", {"entity_id": "light.hall_bathroom_1_light", "transition": 1.5})
    assert device_commands(server) == [(8, "MoveToLevelWithOnOff", {"level": 0, "transitionTime": 15,
                                                                    "optionsMask": 0, "optionsOverride": 0})]


async def test_brightness_zero_turns_off(setup):
    hub, _, server = setup
    await hub.call_service("light", "turn_on", {"entity_id": "light.hall_bathroom_1_light", "brightness": 0})
    assert device_commands(server) == [(6, "Off", {})]


async def test_unreachable_error_from_the_server_is_device_unavailable(setup):
    hub, drv, server = setup
    server.errors["device_command"] = {"error_code": 3, "details": "Node 30 is not (yet) available"}
    with pytest.raises(DeviceUnavailable):
        await drv.async_call(hub.entities["light.hall_bathroom_1_light"], "light", "turn_on", {})
    server.errors["device_command"] = {"error_code": 8, "details": "bad payload"}
    with pytest.raises(HubError) as err:
        await drv.async_call(hub.entities["light.hall_bathroom_1_light"], "light", "turn_on", {})
    assert not isinstance(err.value, DeviceUnavailable)


async def test_unsupported_service(setup):
    hub, drv, _ = setup
    with pytest.raises(ServiceNotFound):
        await drv.async_call(hub.entities["update.master_bedroom_lamp_left"], "update", "install", {})


# -- config entities -----------------------------------------------------------

async def test_number_writes_use_device_units(setup):
    hub, _, server = setup
    await hub.call_service("number", "set_value", {"entity_id": "number.hall_bathroom_1_off_transition_time",
                                                   "value": 1.5})
    await hub.call_service("number", "set_value", {"entity_id": "number.hall_bathroom_1_on_level", "value": 255})
    await hub.call_service("number", "set_value", {"entity_id": "number.hall_bathroom_1_power_on_color_temperature",
                                                   "value": 2700})
    writes = [(c["attribute_path"], c["value"]) for c in server.commands if c["command"] == "write_attribute"]
    assert writes == [("1/8/19", 15), ("1/8/17", None), ("1/768/16400", 370)]
    assert hub.states.get("number.hall_bathroom_1_off_transition_time").state == "1.5"
    assert hub.states.get("number.hall_bathroom_1_on_level").state == "255"
    with pytest.raises(HubError):
        await hub.call_service("number", "set_value", {"entity_id": "number.hall_bathroom_1_on_level",
                                                       "value": 300})


async def test_select_power_on_behaviour(setup):
    hub, _, server = setup
    await hub.call_service("select", "select_option", {
        "entity_id": "select.hall_bathroom_1_power_on_behavior_on_startup", "option": "previous"})
    assert server.nodes[CT_NODE]["attributes"]["1/6/16387"] is None
    assert hub.states.get("select.hall_bathroom_1_power_on_behavior_on_startup").state == "previous"
    await hub.call_service("select", "select_option", {
        "entity_id": "select.hall_bathroom_1_power_on_behavior_on_startup", "option": "on"})
    assert server.nodes[CT_NODE]["attributes"]["1/6/16387"] == 1
    with pytest.raises(HubError):
        await hub.call_service("select", "select_option", {
            "entity_id": "select.hall_bathroom_1_power_on_behavior_on_startup", "option": "dim"})


async def test_a_null_startup_value_from_an_event_is_kept(setup):
    hub, _, server = setup
    await server.set_attr(CT_NODE, "1/6/16387", None)
    await until(lambda: hub.states.get("select.hall_bathroom_1_power_on_behavior_on_startup").state == "previous")


async def test_identify_button(setup):
    hub, _, server = setup
    await hub.call_service("button", "press", {"entity_id": "button.hall_bathroom_1_identify"})
    assert device_commands(server) == [(3, "Identify", {"identifyTime": 15})]
    assert "T" in hub.states.get("button.hall_bathroom_1_identify").state


# -- connection ----------------------------------------------------------------

async def test_reconnects_after_the_server_drops(setup, monkeypatch):
    monkeypatch.setattr(matter_mod, "RECONNECT_DELAYS", (0.05,))
    hub, _, server = setup
    await server.drop_clients()
    await until(lambda: hub.states.get("light.hall_bathroom_1_light").state == UNAVAILABLE)
    await until(lambda: hub.states.get("light.hall_bathroom_1_light").state == "on")
    assert server.connections == 2


async def test_a_server_on_another_fabric_is_refused():
    srv = FakeMatterServer(compressed_fabric_id=0x1234)
    await srv.start()
    try:
        _, drv = build(srv.url)
        with pytest.raises(ConnectionError, match="not the imported fabric"):
            await drv.async_setup()
        await drv.async_unload()
    finally:
        await srv.close()


async def test_setup_fails_when_the_server_is_down():
    _, drv = build("ws://127.0.0.1:9/ws")
    with pytest.raises((OSError, HubError, TimeoutError)):
        await drv.async_setup()
    await drv.async_unload()

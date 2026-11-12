"""Tasmota driver: Status 0 to states, Power<n> commands, finding the address, and the HTTP transport."""
from __future__ import annotations

import pytest
from aiohttp import web

from services.homehub.core import DeviceUnavailable, ServiceNotFound
from services.homehub.drivers.tasmota import TasmotaDriver
from services.homehub.tests.conftest import make_hub

MAC = "020000000107"

ENTITIES = [
    {"entity_id": "switch.loft_plug_loft_tv", "unique_id": f"{MAC}_switch_relay_0"},
    {"entity_id": "switch.loft_plug_loft_plug2", "unique_id": f"{MAC}_switch_relay_1"},
    {"entity_id": "switch.loft_plug_loft_plug3", "unique_id": f"{MAC}_switch_relay_2"},
    {"entity_id": "sensor.loft_plug_energy_power", "unique_id": f"{MAC}_sensor_sensor_ENERGY_Power"},
    {"entity_id": "sensor.loft_plug_energy_total", "unique_id": f"{MAC}_sensor_sensor_ENERGY_Total"},
    {"entity_id": "sensor.loft_plug_energy_totalstarttime",
     "unique_id": f"{MAC}_sensor_sensor_ENERGY_TotalStartTime"},
    {"entity_id": "sensor.loft_plug_ssid", "unique_id": f"{MAC}_status_sensor_status_sensor_status_ssid"},
    {"entity_id": "sensor.loft_plug_wifi_connect_count",
     "unique_id": f"{MAC}_status_sensor_status_sensor_status_link_count"},
    {"entity_id": "sensor.loft_plug_restart_reason",
     "unique_id": f"{MAC}_status_sensor_status_sensor_status_restart_reason"},
    {"entity_id": "sensor.loft_plug_last_restart_time",
     "unique_id": f"{MAC}_status_sensor_status_sensor_last_restart_time"},
]


def status0() -> dict:
    return {
        "Status": {"DeviceName": "Loft-Plug", "Power": "1010"},
        "StatusPRM": {"RestartReason": "Software/System restart"},
        "StatusNET": {"IPAddress": "192.0.2.244", "Mac": "02:00:00:00:01:07"},
        "StatusSTS": {"UptimeSec": 3600, "POWER1": "ON", "POWER2": "OFF", "POWER3": "ON", "POWER4": "OFF",
                      "MqttCount": 0, "Wifi": {"SSId": "home", "LinkCount": 3, "RSSI": 80, "Signal": -60}},
        "StatusSNS": {"Time": "2026-11-11T10:00:00",
                      "ENERGY": {"TotalStartTime": "2023-05-01T12:00:00", "Total": 123.456, "Today": 0.4,
                                 "Power": 61, "Voltage": 121, "Current": 0.52}},
    }


class FakePlug:
    def __init__(self) -> None:
        self.status = status0()
        self.online = True
        self.commands: list[str] = []

    async def __call__(self, command: str) -> dict:
        self.commands.append(command)
        if not self.online:
            raise DeviceUnavailable("Tasmota unreachable")
        if command == "Status 0":
            return self.status
        name, verb = command.split(" ")
        n = name[5:]
        sts = self.status["StatusSTS"]
        cur = sts[f"POWER{n}"]
        new = {"On": "ON", "Off": "OFF", "Toggle": "OFF" if cur == "ON" else "ON"}[verb]
        sts[f"POWER{n}"] = new
        return {f"POWER{n}": new}


def make_driver(plug: FakePlug | None = None, data=None, options=None):
    hub, entry = make_hub("tasmota", data if data is not None else {"discovery_prefix": "tasmota/discovery"},
                          ENTITIES, options=options if options is not None else {"host": "192.0.2.244"})
    drv = TasmotaDriver(hub, entry)
    if plug is not None:
        drv._command = plug  # type: ignore[method-assign]
    hub.drivers[entry.entry_id] = drv
    return hub, drv


async def test_setup_publishes_relays_energy_and_status():
    plug = FakePlug()
    hub, drv = make_driver(plug)
    await drv.async_setup()
    assert plug.commands == ["Status 0"]
    assert hub.states.get("switch.loft_plug_loft_tv").state == "on"
    assert hub.states.get("switch.loft_plug_loft_plug2").state == "off"
    assert hub.states.get("switch.loft_plug_loft_plug3").state == "on"
    power = hub.states.get("sensor.loft_plug_energy_power")
    assert power.state == "61" and power.attributes["unit_of_measurement"] == "W"
    total = hub.states.get("sensor.loft_plug_energy_total")
    assert total.state == "123.456" and total.attributes["state_class"] == "total_increasing"
    # Tasmota's naive local time gets the hub's offset (Etc/GMT+7 is -07:00 all year).
    assert hub.states.get("sensor.loft_plug_energy_totalstarttime").state == "2023-05-01T12:00:00-07:00"
    assert hub.states.get("sensor.loft_plug_ssid").state == "home"
    assert hub.states.get("sensor.loft_plug_wifi_connect_count").state == "3"
    assert hub.states.get("sensor.loft_plug_restart_reason").state == "Software/System restart"
    assert hub.states.get("sensor.loft_plug_last_restart_time").attributes["device_class"] == "timestamp"


async def test_relay_commands_use_one_based_power_numbers_and_publish_the_reply():
    plug = FakePlug()
    hub, drv = make_driver(plug)
    await drv.async_setup()
    await hub.call_service("switch", "turn_on", {"entity_id": "switch.loft_plug_loft_plug2"})
    assert plug.commands[-1] == "Power2 On"
    assert hub.states.get("switch.loft_plug_loft_plug2").state == "on"
    await hub.call_service("switch", "toggle", {"entity_id": "switch.loft_plug_loft_tv"})
    assert plug.commands[-1] == "Power1 Toggle"
    assert hub.states.get("switch.loft_plug_loft_tv").state == "off"
    await hub.call_service("switch", "turn_off", {"entity_id": "switch.loft_plug_loft_plug3"})
    assert plug.commands[-1] == "Power3 Off"
    assert hub.states.get("switch.loft_plug_loft_plug3").state == "off"


async def test_a_single_relay_device_reports_plain_power():
    plug = FakePlug()
    sts = plug.status["StatusSTS"]
    for n in range(1, 5):
        sts.pop(f"POWER{n}")
    sts["POWER"] = "ON"
    hub, drv = make_driver(plug)
    await drv.async_setup()
    assert hub.states.get("switch.loft_plug_loft_tv").state == "on"
    assert hub.states.get("switch.loft_plug_loft_plug2").state == "unknown"


async def test_unknown_service_and_sensor_commands_are_refused():
    plug = FakePlug()
    hub, drv = make_driver(plug)
    await drv.async_setup()
    with pytest.raises(ServiceNotFound):
        await drv.async_call(hub.entities["sensor.loft_plug_energy_power"], "sensor", "turn_on", {})


async def test_poll_marks_unavailable_then_recovers(caplog):
    plug = FakePlug()
    hub, drv = make_driver(plug)
    await drv.async_setup()
    plug.online = False
    await drv.async_poll()
    assert hub.states.get("switch.loft_plug_loft_tv").state == "unavailable"
    assert "unreachable" in caplog.text
    plug.online = True
    await drv.async_poll()
    assert hub.states.get("switch.loft_plug_loft_tv").state == "on"


async def test_setup_fails_when_the_plug_does_not_answer():
    plug = FakePlug()
    plug.online = False
    _, drv = make_driver(plug)
    with pytest.raises(DeviceUnavailable):
        await drv.async_setup()


def test_host_comes_from_options_then_data_then_the_device_configuration_url():
    _, drv = make_driver(options={"host": "10.0.0.5"}, data={"host": "10.0.0.6"})
    assert drv.host == "10.0.0.5"
    _, drv = make_driver(options={}, data={"host": "10.0.0.6"})
    assert drv.host == "10.0.0.6"
    _, drv = make_driver(options={}, data={"hosts": {"02:00:00:00:01:07": "10.0.0.7"}})
    assert drv.host == "10.0.0.7"
    hub, entry = make_hub("tasmota", {"discovery_prefix": "tasmota/discovery"}, ENTITIES)
    hub.devices["dev1"].configuration_url = "http://192.0.2.244/"
    assert TasmotaDriver(hub, entry).host == "192.0.2.244"


async def test_no_address_is_a_clear_setup_error():
    _, drv = make_driver(options={}, data={"discovery_prefix": "tasmota/discovery"})
    with pytest.raises(DeviceUnavailable, match="no address"):
        await drv.async_setup()


async def test_http_transport_against_a_local_stand_in():
    seen: list[str] = []

    async def cm(request: web.Request) -> web.Response:
        seen.append(request.query["cmnd"])
        return web.json_response(status0() if request.query["cmnd"] == "Status 0" else {"POWER1": "OFF"})

    app = web.Application()
    app.router.add_get("/cm", cm)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
    try:
        hub, drv = make_driver(options={"host": f"127.0.0.1:{port}"})
        await drv.async_setup()
        await hub.call_service("switch", "turn_off", {"entity_id": "switch.loft_plug_loft_tv"})
        await drv.async_unload()
    finally:
        await runner.cleanup()
    assert seen == ["Status 0", "Power1 Off"]
    assert hub.states.get("switch.loft_plug_loft_tv").state == "off"


async def test_http_transport_maps_a_refused_connection_to_device_unavailable():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()  # nothing listens there now
    _, drv = make_driver(options={"host": f"127.0.0.1:{port}"})
    with pytest.raises(DeviceUnavailable, match="unreachable"):
        await drv.async_setup()
    await drv.async_unload()

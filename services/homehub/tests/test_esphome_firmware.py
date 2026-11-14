"""ESPHome firmware updates through the linked ESPHome Device Builder (drivers/esphome_builder.py).

The node's driver publishes HA's ``Firmware`` update entity (unique_id = the node's MAC): installed is the
ESPHome version the node runs, latest is the builder's, and install compiles and flashes over the air through
the builder's own API. Without a linked builder, or a config for the node, nothing is offered."""
from __future__ import annotations

import asyncio

import pytest
from aioesphomeapi import DeviceInfo

from services.homehub.core import ConfigEntry, HubError
from services.homehub.drivers import esphome
from services.homehub.drivers.esphome import ESPHomeDriver
from services.homehub.drivers.esphome_builder import RELEASE_URL, ESPHomeBuilderDriver, builder_for
from services.homehub.tests.conftest import make_hub
from services.homehub.tests.test_drivers_esphome import DATA, ENTITIES, MAC, FakeClient

FIRMWARE = "update.den_speaker_firmware"
BUILDER_URL = "http://192.0.2.5:6052/"


class VersionedClient(FakeClient):
    async def device_info_and_list_entities(self):
        info, infos, services = await super().device_info_and_list_entities()
        return DeviceInfo(name=info.name, mac_address=MAC, esphome_version="2026.9.0"), infos, services


def make_builder(hub, *, nodes=None, results=None, gate: asyncio.Event | None = None):
    entry = ConfigEntry("builder", "esphome_builder", "ESPHome Device Builder",
                        data={"url": BUILDER_URL, "username": "admin", "password": "not-a-real-password"})
    hub.config_entries[entry.entry_id] = entry
    drv = ESPHomeBuilderDriver(hub, entry)
    jobs: list[tuple] = []
    outcomes = list(results or [(True, ["ok"]), (True, ["ok"])])

    async def check(url, username=None, password=None):
        return {"status": 200, "login": False}

    async def list_nodes(url, username=None, password=None):
        return list(nodes if nodes is not None else [
            {"name": "smart-speaker", "configuration": "smart-speaker.yaml", "current_version": "2026.10.2"}])

    async def run_job(url, username, password, job, configuration, port=None):
        if gate is not None:
            await gate.wait()
        jobs.append((url, username, job, configuration, port))
        return outcomes.pop(0)

    drv.check, drv.list_nodes, drv.run_job = check, list_nodes, run_job
    hub.drivers[entry.entry_id] = drv
    return drv, jobs


@pytest.fixture
def node(monkeypatch):
    hub, entry = make_hub("esphome", dict(DATA), [*ENTITIES, {"entity_id": FIRMWARE, "unique_id": MAC}])
    drv = ESPHomeDriver(hub, entry)
    drv._make_client = lambda host: VersionedClient()  # type: ignore[method-assign]
    hub.drivers[entry.entry_id] = drv
    monkeypatch.setattr(esphome, "COMMAND_SETTLE", 0.05)
    return hub, drv


async def test_without_a_builder_the_version_is_shown_and_nothing_is_offered(node):
    hub, drv = node
    await drv.async_setup()
    st = hub.states.get(FIRMWARE)
    assert st.state == "unknown" and st.attributes["installed_version"] == "2026.9.0"
    assert st.attributes["latest_version"] is None and st.attributes["supported_features"] == 0
    assert "not linked" in st.attributes["release_summary"]
    with pytest.raises(HubError, match="cannot be installed"):
        await hub.call_service("update", "install", {"entity_id": FIRMWARE})


async def test_with_the_builder_latest_comes_from_it_and_install_compiles_then_flashes(node):
    hub, drv = node
    gate = asyncio.Event()
    builder, jobs = make_builder(hub, gate=gate)
    await builder.async_setup()
    await drv.async_setup()
    assert builder_for(hub) is builder
    st = hub.states.get(FIRMWARE)
    assert st.state == "on" and st.attributes["latest_version"] == "2026.10.2"
    assert st.attributes["supported_features"] == 1 and st.attributes["release_url"] == RELEASE_URL
    await hub.call_service("update", "install", {"entity_id": FIRMWARE})
    assert hub.states.get(FIRMWARE).attributes["in_progress"] is True
    with pytest.raises(HubError, match="already being updated"):
        await hub.call_service("update", "install", {"entity_id": FIRMWARE})
    gate.set()
    await asyncio.gather(*builder._jobs)
    assert [(j[2], j[3], j[4]) for j in jobs] == [("compile", "smart-speaker.yaml", None),
                                                   ("upload", "smart-speaker.yaml", "OTA")]
    assert jobs[0][:2] == (BUILDER_URL, "admin")
    assert hub.states.get(FIRMWARE).attributes["in_progress"] is False


async def test_a_failed_compile_does_not_flash_and_is_reported(node, caplog):
    hub, drv = node
    builder, jobs = make_builder(hub, results=[(False, ["Compiling...", "error: missing component"])])
    await builder.async_setup()
    await drv.async_setup()
    failures = []
    hub.bus.subscribe(lambda _t, ev: failures.append(ev), "homehub_firmware_update_failed")
    await hub.call_service("update", "install", {"entity_id": FIRMWARE})
    await asyncio.gather(*builder._jobs)
    assert [j[2] for j in jobs] == ["compile"]
    assert "missing component" in caplog.text
    assert failures and failures[0]["step"] == "compile"


async def test_a_node_the_builder_has_no_config_for_is_not_offered(node):
    hub, drv = node
    builder, _jobs = make_builder(hub, nodes=[{"name": "other-node", "configuration": "other-node.yaml",
                                               "current_version": "2026.10.2"}])
    await builder.async_setup()
    await drv.async_setup()
    st = hub.states.get(FIRMWARE)
    assert st.attributes["supported_features"] == 0 and "no config named smart-speaker" in st.attributes[
        "release_summary"]


async def test_the_builder_going_away_withdraws_the_offer(node):
    hub, drv = node
    builder, _jobs = make_builder(hub)
    await builder.async_setup()
    await drv.async_setup()
    assert hub.states.get(FIRMWARE).attributes["supported_features"] == 1

    async def down(url, username=None, password=None):
        from services.homehub.core import DeviceUnavailable
        raise DeviceUnavailable("the ESPHome builder did not answer")

    builder.check = down
    await builder.async_poll()
    assert builder.entry.state == "setup_retry"
    assert hub.states.get(FIRMWARE).attributes["supported_features"] == 0


async def test_a_failed_device_list_keeps_the_link_but_offers_no_install(node, caplog):
    hub, drv = node
    builder, _jobs = make_builder(hub)

    async def broken(url, username=None, password=None):
        from services.homehub.core import DeviceUnavailable
        raise DeviceUnavailable("the ESPHome builder's device list answered HTTP 404")

    builder.list_nodes = broken
    await builder.async_poll()
    await drv.async_setup()
    assert builder.entry.state == "loaded"
    assert hub.states.get(FIRMWARE).attributes["supported_features"] == 0
    assert "firmware updates cannot be offered" in caplog.text


async def test_the_dashboard_api_is_spoken_as_the_builder_speaks_it():
    """``GET /devices`` and the ``/compile`` / ``/upload`` websockets, with the login, against a stand-in."""
    from aiohttp import web
    from aiohttp.test_utils import TestServer

    from services.homehub.drivers.esphome_builder import list_nodes, run_job

    seen: list[tuple[str, dict, str | None]] = []

    async def devices(request):
        seen.append(("devices", {}, request.headers.get("Authorization")))
        return web.json_response({"configured": [
            {"name": "smart-speaker", "configuration": "smart-speaker.yaml", "current_version": "2026.10.2"},
            {"name": "", "configuration": "broken.yaml"}], "importable": []})

    async def job(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        msg = await ws.receive_json()
        seen.append((request.path, msg, request.headers.get("Authorization")))
        await ws.send_json({"event": "line", "data": "INFO Reading configuration..."})
        await ws.send_json({"event": "exit", "code": 0 if request.path == "/compile" else 1})
        await ws.close()
        return ws

    app = web.Application()
    app.router.add_get("/devices", devices)
    app.router.add_get("/compile", job)
    app.router.add_get("/upload", job)
    async with TestServer(app) as server:
        base = str(server.make_url("/"))
        nodes = await list_nodes(base, "admin", "not-a-real-password")
        assert [n["name"] for n in nodes] == ["smart-speaker"]
        assert await run_job(base, "admin", "not-a-real-password", "compile", "smart-speaker.yaml") == (
            True, ["INFO Reading configuration..."])
        ok, _tail = await run_job(base, None, None, "upload", "smart-speaker.yaml", "OTA")
        assert ok is False
    assert seen[0][2] and seen[0][2].startswith("Basic ")
    assert seen[1][1] == {"type": "spawn", "configuration": "smart-speaker.yaml"}
    assert seen[2][1] == {"type": "spawn", "configuration": "smart-speaker.yaml", "port": "OTA"}
    assert seen[2][2] is None


async def test_the_firmware_entity_is_not_reported_as_missing_from_the_node(node, caplog):
    _hub, drv = node
    await drv.async_setup()
    assert FIRMWARE not in caplog.text

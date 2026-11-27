"""ESPHome restarts and drops (esphome_restarts.py) against a stand-in APIClient.

Restart versus dropped connection from the node's uptime, the debug component's reset reason and memory, the
log lines kept around a drop (capped, rate-limited, secrets redacted), what the hub was doing, one notice for
repeated restarts, the store, the API and /health. Names and addresses are made up.
"""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest
from aioesphomeapi import (
    DeviceInfo,
    LogLevel,
    SensorInfo,
    SensorState,
    SensorStateClass,
    TextSensorInfo,
    TextSensorState,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.homehub import esphome_restarts, restarts_api
from services.homehub.auth import Auth
from services.homehub.drivers import esphome
from services.homehub.drivers.esphome import ESPHomeDriver
from services.homehub.esphome_restarts import RestartMonitor, clean_line, sensor_role
from services.homehub.tests.conftest import make_hub
from services.homehub.tests.test_drivers_esphome import DATA, ENTITIES, INFOS, INITIAL_STATES, MAC, FakeClient

SECRET = "made-up-internal-secret"

UPTIME = SensorInfo(object_id="uptime", key=101, name="Uptime", unit_of_measurement="s",
                    state_class=SensorStateClass.TOTAL_INCREASING, device_class="duration")
RESET = TextSensorInfo(object_id="reset_reason", key=102, name="Reset reason")
HEAP = SensorInfo(object_id="heap_free", key=103, name="Heap free", unit_of_measurement="B")
PSRAM = SensorInfo(object_id="psram_free", key=104, name="PSRAM free", unit_of_measurement="B")
DEBUG_INFOS = [UPTIME, RESET, HEAP, PSRAM]


class Store:
    def __init__(self) -> None:
        self.kv: dict[str, object] = {}
        self.entries: list[object] = []

    def get_kv(self, key, default=None):
        return self.kv.get(key, default)

    def set_kv(self, key, value):
        self.kv[key] = value

    def save_config_entry(self, entry):
        self.entries.append(entry)


class Node:
    """What the stand-in node reports on its next connection."""

    def __init__(self, extra_infos=(), states=()):
        self.infos = list(INFOS) + list(extra_infos)
        self.states = list(states)
        self.clients: list[LogClient] = []


class LogClient(FakeClient):
    def __init__(self, node: Node):
        super().__init__()
        self.node = node
        self.on_log = None
        self.log_levels: list[int] = []
        self.closed_callbacks: list = []
        node.clients.append(self)

    async def device_info_and_list_entities(self):
        return DeviceInfo(name="smart-speaker", mac_address=MAC), list(self.node.infos), []

    def subscribe_home_assistant_states_and_services(self, on_state, on_service_call, on_state_sub,
                                                     on_state_request=None):
        self.on_state = on_state
        for s in [*INITIAL_STATES, *self.node.states]:
            on_state(s)

    def subscribe_logs(self, on_log, log_level=None, dump_config=None):
        self.on_log = on_log
        self.log_levels.append(int(log_level))

        def unsub():
            if self.on_log is on_log:
                self.on_log = None
        return unsub

    def add_connection_closed_callback(self, cb):
        self.closed_callbacks.append(cb)
        return lambda: None

    def log(self, text: str, level: int = 2):
        self.on_log(SimpleNamespace(level=level, message=text.encode()))

    async def drop(self, error: Exception | None = None, expected: bool = False):
        for cb in self.closed_callbacks:
            cb(SimpleNamespace(expected_disconnect=expected, reason=None, error=error))
        await self.on_stop(expected)


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setattr(esphome, "RECONNECT_DELAYS", (0,))
    monkeypatch.setattr(esphome_restarts, "SETTLE_S", 0.05)
    notices: list[dict] = []

    async def notifier(payload):
        notices.append(payload)
        return True

    def build(node: Node, options=None):
        hub, entry = make_hub("esphome", dict(DATA), ENTITIES, options=options or {})
        hub.store = Store()
        drv = ESPHomeDriver(hub, entry)
        drv._make_client = lambda host, port=None: LogClient(node)  # type: ignore[method-assign]
        drv.restarts.notifier = notifier
        hub.drivers[entry.entry_id] = drv
        return hub, drv

    return SimpleNamespace(build=build, notices=notices)


async def _reconnect_and_settle(drv, client, **kw):
    await client.drop(**kw)
    await asyncio.wait_for(drv._reconnect_task, 2)
    for _ in range(50):
        if drv.restarts._pending is None:
            break
        await asyncio.sleep(0.02)
    assert drv.restarts._pending is None
    return drv.restarts.events[0]


def _states(uptime=None, reset="Power on", heap=180_000.0, psram=1_500_000.0, uptime_missing=False):
    out = [TextSensorState(key=102, state=reset), SensorState(key=103, state=heap),
           SensorState(key=104, state=psram)]
    if uptime_missing:
        out.append(SensorState(key=101, state=0.0, missing_state=True))
    elif uptime is not None:
        out.append(SensorState(key=101, state=float(uptime)))
    return out


def test_sensor_roles_come_from_what_the_entity_is():
    assert [sensor_role(i) for i in DEBUG_INFOS] == ["uptime", "reset_reason", "heap_free", "psram_free"]
    assert sensor_role(SensorInfo(object_id="uptime_ts", key=1, name="Uptime", device_class="timestamp")) == "boot_time"
    assert sensor_role(SensorInfo(object_id="heap_min", key=1, name="Heap min free", unit_of_measurement="B")) \
        == "heap_min_free"
    assert sensor_role(SensorInfo(object_id="wifi_signal", key=1, name="WiFi Signal", unit_of_measurement="dBm")) \
        is None
    assert sensor_role(TextSensorInfo(object_id="ip", key=1, name="IP Address")) is None


def test_log_lines_lose_colours_and_secrets_and_are_cut():
    line = clean_line(b"\x1b[0;33m[W][api:1]: password: made-up-pw token=abc123 Authorization: Bearer x.y.z\x1b[0m")
    assert line == "[W][api:1]: password: [redacted] token=[redacted] Authorization: Bearer [redacted]"
    assert clean_line("noise_psk = 'bm90LWEtcmVhbC1rZXktYXQtYWxs'") == "noise_psk = '[redacted]'"
    assert clean_line("key press counted, auth failed") == "key press counted, auth failed"
    long = clean_line("word " * 100)
    assert len(long) == esphome_restarts.LINE_MAX_CHARS and long.endswith("…")


async def test_a_dropped_connection_is_told_apart_by_the_uptime_that_kept_counting(env):
    node = Node(DEBUG_INFOS, _states(uptime=5000))
    hub, drv = env.build(node)
    await drv.async_setup()
    first = node.clients[-1]
    assert first.log_levels == [int(LogLevel.LOG_LEVEL_WARN)] and drv.restarts.subscribed
    first.log("[W][wifi:123]: WiFi connection lost; reconnecting")
    node.states = _states(uptime=5012)
    ev = await _reconnect_and_settle(drv, first, error=ConnectionResetError("Connection reset by peer"))
    assert ev["kind"] == "drop" and "kept counting" in ev["evidence"]
    assert ev["reset_reason"] is None and ev["last_reset_reason"] == "Power on"
    assert ev["summary"].startswith("Connection dropped")
    assert ev["lines_before"][-1][2] == "[W][wifi:123]: WiFi connection lost; reconnecting"
    assert "Connection reset by peer" in ev["error"]
    assert hub.states.get("switch.den_den_speaker_mute").state == "off"  # the node itself still works
    assert env.notices == []
    await drv.async_unload()


async def test_a_restart_carries_the_reset_reason_memory_and_what_the_hub_was_doing(env):
    node = Node(DEBUG_INFOS, _states(uptime=5000, heap=60_000.0, psram=900_000.0))
    _hub, drv = env.build(node)
    await drv.async_setup()
    first = node.clients[-1]
    first.on_state(SensorState(key=103, state=12_000.0))  # the heap ran low before it went
    drv.voice.last_run = SimpleNamespace(marks={"ended": time.monotonic() - 24}, outcome="ok")
    drv.restarts.note("announcement", True)
    first.log("[E][ring_buffer:62]: Failed to allocate 32768 bytes")
    node.states = _states(uptime=4, reset="Exception panic", heap=200_000.0, psram=1_900_000.0)
    ev = await _reconnect_and_settle(drv, first)
    assert ev["kind"] == "restart" and "went back" in ev["evidence"]
    assert ev["reset_reason"] == "Exception panic" and ev["summary"].startswith("Restarted (Exception panic)")
    assert ev["cause"] == "Reset reason: Exception panic"
    assert ev["memory"]["heap_free"] == {"before": 12_000.0, "lowest": 12_000.0, "after": 200_000.0}
    assert ev["memory"]["psram_free"]["before"] == 900_000.0
    assert ev["context"]["voice_run"]["running"] is False and 23 <= ev["context"]["voice_run"]["ended_s_before"] < 30
    assert ev["context"]["announcement"]["running"] is True
    assert any(t.startswith("A voice run ended") for t in ev["context_text"])
    assert any(t.startswith("An announcement was playing") for t in ev["context_text"])
    assert drv.restarts.guidance() is None  # it has every sensor the monitor reads
    await drv.async_unload()


async def test_an_uptime_with_no_reading_after_the_reconnect_is_a_restart(env):
    node = Node(DEBUG_INFOS, _states(uptime=800))
    _hub, drv = env.build(node)
    await drv.async_setup()
    node.states = _states(uptime_missing=True)
    ev = await _reconnect_and_settle(drv, node.clients[-1])
    assert ev["kind"] == "restart" and "no reading yet" in ev["evidence"]
    await drv.async_unload()


async def test_without_debug_sensors_the_cause_is_unknown_and_the_yaml_is_offered(env):
    node = Node()
    _hub, drv = env.build(node)
    await drv.async_setup()
    first = node.clients[-1]
    first.log("[W][speaker_media_player:301]: Buffer underrun")
    ev = await _reconnect_and_settle(drv, first, error=TimeoutError("Ping response not received after 90.0 seconds"))
    assert ev["kind"] == "unknown" and "no uptime sensor" in ev["evidence"]
    assert ev["summary"].startswith("Disconnected; restart or dropped connection not known")
    assert ev["downtime_s"] is not None and "stopped answering" in ev["hint"]
    guide = drv.restarts.guidance()
    assert guide is not None and "reset reason (debug component)" in guide["missing"]
    assert "platform: debug" in guide["yaml"] and "reset_reason:" in guide["yaml"] and "psram:" in guide["yaml"]
    assert "smart-speaker" not in guide["yaml"]  # generic, for every node
    # A boot message soon after an undecided event decides it.
    node.clients[-1].log("[I][app:100]: Running through setup()", level=3)
    assert drv.restarts.events[0]["kind"] == "restart"
    await drv.async_unload()


async def test_a_node_that_says_goodbye_first_restarted_on_purpose(env):
    node = Node()
    _hub, drv = env.build(node)
    await drv.async_setup()
    ev = await _reconnect_and_settle(drv, node.clients[-1], expected=True)
    assert ev["kind"] == "restart" and ev["expected"] and "planned reboot" in ev["evidence"]
    await drv.async_unload()


async def test_the_ring_buffer_is_capped_rate_limited_and_kept_with_the_event(env, monkeypatch):
    monkeypatch.setattr(esphome_restarts, "RATE_LINES_PER_S", 0.0)
    monkeypatch.setattr(esphome_restarts, "RATE_BURST", 1000)
    node = Node()
    _hub, drv = env.build(node)
    await drv.async_setup()
    mon, client = drv.restarts, node.clients[-1]
    mon._tokens = 1000.0
    for i in range(esphome_restarts.RING_LINES + 50):
        client.log(f"[W][test:1]: line {i} password=made-up-pw")
    assert len(mon.ring) == esphome_restarts.RING_LINES and mon.ring[0][2].startswith("[W][test:1]: line 50 ")
    assert all("made-up-pw" not in text for _at, _lvl, text in mon.ring)
    mon._tokens = 2.0
    for i in range(10):
        client.log(f"[W][flood:1]: {i}")
    assert mon.dropped == 8
    ev = await _reconnect_and_settle(drv, client)
    assert len(ev["lines_before"]) <= esphome_restarts.EVENT_LINES_BEFORE and ev["dropped_lines"] == 8
    assert sum(len(r[2]) + 30 for r in ev["lines_before"]) <= esphome_restarts.EVENT_CHARS_MAX
    await drv.async_unload()


async def test_an_event_never_holds_more_than_its_character_cap(env, monkeypatch):
    monkeypatch.setattr(esphome_restarts, "EVENT_CHARS_MAX", 2000)
    node = Node()
    _hub, drv = env.build(node)
    await drv.async_setup()
    drv.restarts._tokens = 1000.0
    for i in range(60):
        node.clients[-1].log(f"[W][test:1]: {i} " + "a b " * 60)
    ev = await _reconnect_and_settle(drv, node.clients[-1])
    assert sum(len(r[2]) + 30 for r in ev["lines_before"]) <= 2000
    assert ev["lines_before"][-1][2].startswith("[W][test:1]: 59 ")  # the newest lines are the ones kept
    await drv.async_unload()


async def test_repeated_restarts_send_one_admin_notice(env):
    node = Node()
    _hub, drv = env.build(node)
    await drv.async_setup()
    for _ in range(5):
        await _reconnect_and_settle(drv, node.clients[-1])
    for _ in range(5):
        await asyncio.sleep(0)
    assert len(env.notices) == 1
    notice = env.notices[0]
    assert notice["user"] == "@admins" and notice["audience"] == "parents"
    assert notice["data"]["link"]["hash"] == "setup" and notice["data"]["link"]["focus"].startswith("entry-")
    assert "went offline 3 times" in notice["body"] and "Restarts" in notice["body"]
    assert drv.restarts.counts(3600)["unknown"] == 5
    await drv.async_unload()


async def test_drops_never_send_the_notice(env):
    node = Node(DEBUG_INFOS, _states(uptime=100))
    _hub, drv = env.build(node)
    await drv.async_setup()
    for i in range(4):
        node.states = _states(uptime=200 + i * 50)
        await _reconnect_and_settle(drv, node.clients[-1])
    await asyncio.sleep(0)
    assert env.notices == [] and drv.restarts.counts(3600)["drop"] == 4
    await drv.async_unload()


async def test_events_are_kept_in_the_store_and_capped(env, monkeypatch):
    monkeypatch.setattr(esphome_restarts, "EVENTS_KEEP", 3)
    node = Node()
    hub, drv = env.build(node)
    await drv.async_setup()
    for _ in range(4):
        await _reconnect_and_settle(drv, node.clients[-1])
    saved = hub.store.kv[f"esphome_restarts:{drv.entry.entry_id}"]
    assert len(saved["events"]) == 3 and saved["notified_at"]
    assert all(not k.startswith("_") for ev in saved["events"] for k in ev)
    again = RestartMonitor(drv)
    assert [e["id"] for e in again.events] == [e["id"] for e in drv.restarts.events]
    await drv.async_unload()


async def test_a_node_without_log_support_still_connects(env):
    class NoLogs(LogClient):
        subscribe_logs = None  # type: ignore[assignment]

    node = Node()
    _hub, drv = env.build(node)
    drv._make_client = lambda host, port=None: NoLogs(node)  # type: ignore[method-assign]
    await drv.async_setup()
    assert drv.connected and not drv.restarts.subscribed and drv.restarts.subscribe_error
    await drv.async_unload()


async def test_the_api_shows_events_and_changes_the_log_level_live(env):
    node = Node()
    hub, drv = env.build(node)
    await drv.async_setup()
    await _reconnect_and_settle(drv, node.clients[-1])
    runtime = SimpleNamespace(hub=hub, store=hub.store)
    app = FastAPI()
    app.include_router(restarts_api.build_router(runtime, Auth(hub, SECRET)))
    client = TestClient(app)
    h = {"Authorization": f"Bearer {SECRET}"}
    assert client.get("/hub/devices/dev1/restarts").status_code == 401
    body = client.get("/hub/devices/dev1/restarts", headers=h).json()
    assert body["supported"] and body["log_level"] == "warning" and len(body["events"]) == 1
    assert body["guidance"]["yaml"].startswith("debug:") and body["connected"]
    assert client.get("/hub/devices/nope/restarts", headers=h).status_code == 404
    assert client.patch("/hub/devices/dev1/restarts", headers=h, json={"log_level": "loud"}).status_code == 400
    live = node.clients[-1]
    changed = client.patch("/hub/devices/dev1/restarts", headers=h, json={"log_level": "debug"}).json()
    assert changed["log_level"] == "debug" and drv.entry.options["restart_log_level"] == "debug"
    assert live.log_levels[-1] == int(LogLevel.LOG_LEVEL_DEBUG) and hub.store.entries
    client.patch("/hub/devices/dev1/restarts", headers=h, json={"log_level": "warning"})
    assert "restart_log_level" not in drv.entry.options and live.log_levels[-1] == int(LogLevel.LOG_LEVEL_WARN)
    hub.devices["dev1"].config_entries = ["other"]
    assert client.get("/hub/devices/dev1/restarts", headers=h).json() == {"supported": False, "device_id": "dev1"}
    await drv.async_unload()


async def test_the_option_sets_the_level_the_node_is_subscribed_at(env):
    node = Node()
    _hub, drv = env.build(node, options={"restart_log_level": "debug"})
    await drv.async_setup()
    assert node.clients[-1].log_levels == [int(LogLevel.LOG_LEVEL_DEBUG)]
    await drv.async_unload()


async def test_health_summary_counts_every_watched_node(env):
    node = Node()
    hub, drv = env.build(node)
    await drv.async_setup()
    await _reconnect_and_settle(drv, node.clients[-1])
    summary = esphome_restarts.health_summary(hub)
    assert summary["watched"] == 1 and summary["logs_subscribed"] == 1 and summary["without_debug"] == 1
    assert summary["last_24h"]["unknown"] == 1 and summary["last"]["kind"] == "unknown"
    await drv.async_unload()


async def test_the_hub_unloading_the_node_is_not_an_event(env):
    node = Node()
    _hub, drv = env.build(node)
    await drv.async_setup()
    client = node.clients[-1]
    await drv.async_unload()
    await client.on_stop(True)
    assert drv.restarts.events == [] and drv.restarts._pending is None

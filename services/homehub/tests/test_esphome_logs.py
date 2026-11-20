"""ESPHome live logs (esphome_logs.py, esphome_logs_api.py) against the stand-in node of test_esphome_restarts.

The buffer (bounded, parsed, redacted, rate-limited), the node's subscription level rising while someone watches
and dropping back when the last viewer leaves, the offline/reconnected/restart markers, the config dump, the API's
admin-only answers, the NDJSON stream and the WebSocket subscription. Names, addresses and tokens are made up.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from aioesphomeapi import LogLevel
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.homehub import api as hub_api
from services.homehub import esphome_logs, esphome_logs_api, esphome_restarts
from services.homehub.auth import Auth, Principal
from services.homehub.esphome_logs import DeviceLogs, parse, view_level
from services.homehub.esphome_restarts import clean_line
from services.homehub.tests.test_esphome_restarts import SECRET, LogClient, Node, _reconnect_and_settle
from services.homehub.tests.test_esphome_restarts import env as env  # the fixture

WARN, INFO, CONFIG, DEBUG, VERBOSE = (int(LogLevel.LOG_LEVEL_WARN), int(LogLevel.LOG_LEVEL_INFO),
                                      int(LogLevel.LOG_LEVEL_CONFIG), int(LogLevel.LOG_LEVEL_DEBUG),
                                      int(LogLevel.LOG_LEVEL_VERBOSE))
USER_TOKEN = "made-up-user-token"


class DumpClient(LogClient):
    def __init__(self, node):
        super().__init__(node)
        self.dumps: list[bool] = []

    def subscribe_logs(self, on_log, log_level=None, dump_config=None):
        self.dumps.append(bool(dump_config))
        return super().subscribe_logs(on_log, log_level=log_level, dump_config=dump_config)


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


async def _node(env, options=None):
    node = Node()
    hub, drv = env.build(node, options=options)
    drv._make_client = lambda host: DumpClient(node)  # type: ignore[method-assign]
    await drv.async_setup()
    return node, hub, drv


def _app(hub):
    class TestAuth(Auth):
        def authenticate(self, token):
            if token == USER_TOKEN:
                return Principal(user_id="u1", name="A user", is_admin=False)
            return super().authenticate(token)

    app = FastAPI()
    app.include_router(esphome_logs_api.build_router(SimpleNamespace(hub=hub, store=hub.store), TestAuth(hub, SECRET)))
    return TestClient(app)


# -- the buffer ----------------------------------------------------------------------------------------------------

def test_lines_are_parsed_into_tag_and_message_without_colour():
    text = clean_line(b"\x1b[0;36m[D][sensor:093]: 'Kitchen temp': Sending state 21.50000\x1b[0m", 2000)
    assert parse(text) == ("sensor", "'Kitchen temp': Sending state 21.50000")
    assert parse("[12:01:02][I][app:100]: Running through setup()") == ("app", "Running through setup()")
    assert parse("[C][wifi:600]:   SSID: [redacted]") == ("wifi", "  SSID: [redacted]")
    assert parse("plain text from somewhere") == ("", "plain text from somewhere")
    assert view_level("warn") == "warning" and view_level(None) == "debug" and view_level("loud") is None


def test_secrets_and_proxy_tokens_never_reach_the_buffer():
    logs = DeviceLogs()
    for raw in (
        "[D][media_player:1]: Playing http://192.0.2.10:8123/api/esphome/ffmpeg_proxy/abc123/def456.flac",
        "[D][speaker:1]: url http://hub.example:8123/api/tts_proxy/Zm9vYmFyLXNlY3JldA.mp3 started",
        "[D][http:1]: GET /stream?key=made-up-key-value&authSig=made.up.sig&room=den",
        "[W][api:1]: password=hunter-made-up api_key: notreal",
    ):
        logs.add_line(DEBUG, raw)
    text = json.dumps(logs.history())
    for secret in ("abc123", "def456", "Zm9vYmFyLXNlY3JldA", "made-up-key-value", "made.up.sig", "hunter-made-up",
                   "notreal"):
        assert secret not in text
    assert "/api/esphome/ffmpeg_proxy/[redacted]" in text and "/api/tts_proxy/[redacted]" in text
    assert "room=den" in text  # only what looks secret goes


def test_the_buffer_is_bounded_and_read_by_sequence_level_and_limit():
    clock = Clock()
    logs = DeviceLogs(clock=clock)
    for i in range(esphome_logs.BUFFER_LINES + 50):
        clock.now += 0.01  # under the rate limit
        logs.add_line(DEBUG if i % 2 else WARN, f"[D][t:1]: line {i}")
    assert len(logs.lines) == esphome_logs.BUFFER_LINES and logs.seq == esphome_logs.BUFFER_LINES + 50
    assert logs.history()[0]["message"] == "line 50"
    warn_only = logs.history(level="warning")
    assert warn_only and all(r["level"] == "warning" for r in warn_only)
    tail = logs.history(since=logs.seq - 3)
    assert [r["seq"] for r in tail] == [logs.seq - 2, logs.seq - 1, logs.seq]
    assert len(logs.history(limit=5)) == 5 and logs.history(limit=0) == []
    marker = logs.add_marker("offline", "Device offline")
    assert logs.history(since=marker["seq"] - 1, level="error") == [
        {"seq": marker["seq"], "at": marker["at"], "type": "marker", "event": "offline", "message": "Device offline"}]
    assert "_lvl" not in logs.history()[-2]


def test_a_flood_is_rate_limited_and_counted():
    clock = Clock()
    logs = DeviceLogs(clock=clock)
    for i in range(esphome_logs.RATE_BURST + 100):
        logs.add_line(VERBOSE, f"[V][x:1]: {i}")
    assert len(logs.lines) == esphome_logs.RATE_BURST and logs.dropped == 100
    clock.now += 1.0
    logs.add_line(VERBOSE, "[V][x:1]: later")
    assert logs.history(level="verbose")[-1]["message"] == "later"


async def test_a_stream_sends_history_then_live_lines_and_reports_skipped_ones(monkeypatch):
    monkeypatch.setattr(esphome_logs, "QUEUE_MAX", 3)
    logs = DeviceLogs()
    logs.add_line(WARN, "[W][a:1]: before")
    it = logs.stream("debug", hello={"device_id": "d"}).__aiter__()
    assert (await it.__anext__())["type"] == "hello"
    assert (await it.__anext__())["message"] == "before"
    nxt = asyncio.ensure_future(it.__anext__())
    await asyncio.sleep(0)
    logs.add_line(DEBUG, "[D][a:1]: live")
    assert (await asyncio.wait_for(nxt, 1))["message"] == "live"
    logs.add_line(VERBOSE, "[V][a:1]: too detailed for this viewer")
    for i in range(6):
        logs.add_line(WARN, f"[W][a:1]: burst {i}")
    skipped = await asyncio.wait_for(it.__anext__(), 1)
    assert skipped["event"] == "skipped" and skipped["message"].startswith("3 lines")
    assert (await it.__anext__())["message"] == "burst 0"
    logs.close()
    rest = [x async for x in it]
    assert rest[-1] == {"type": "end", "reason": "removed", "seq": logs.seq}
    assert logs.viewers == set()


# -- the node's subscription level ---------------------------------------------------------------------------------

async def test_viewers_raise_the_level_and_the_last_one_leaving_drops_it(env):
    node, _hub, drv = await _node(env)
    client, mon = node.clients[-1], drv.restarts
    assert client.log_levels == [WARN]
    debug = mon.logs.open_viewer("debug")
    assert client.log_levels[-1] == DEBUG and mon.subscribed_level == DEBUG
    verbose = mon.logs.open_viewer("verbose")
    assert client.log_levels[-1] == VERBOSE
    mon.logs.open_viewer("error")  # lower than what is already asked for: no new request
    calls = len(client.log_levels)
    mon.logs.close_viewer(verbose)
    assert client.log_levels[-1] == DEBUG and len(client.log_levels) == calls + 1
    mon.logs.close_viewer(debug)
    assert client.log_levels[-1] == WARN
    await drv.async_unload()


async def test_a_device_set_to_debug_for_the_restart_monitor_stays_there(env):
    node, _hub, drv = await _node(env, options={"restart_log_level": "debug"})
    client, mon = node.clients[-1], drv.restarts
    viewer = mon.logs.open_viewer("info")
    mon.logs.close_viewer(viewer)
    assert client.log_levels == [DEBUG]
    await drv.async_unload()


async def test_lines_sent_for_a_viewer_stay_out_of_the_restart_history(env):
    node, _hub, drv = await _node(env)
    client, mon = node.clients[-1], drv.restarts
    mon.logs.open_viewer("debug")
    client.log("[D][sensor:1]: chatty", level=DEBUG)
    client.log("[W][wifi:1]: weak signal", level=WARN)
    assert [r[2] for r in mon.ring] == ["[W][wifi:1]: weak signal"]
    assert [r["message"] for r in mon.logs.history() if r["type"] == "line"] == ["chatty", "weak signal"]
    await drv.async_unload()


async def test_offline_reconnected_and_the_restart_verdict_are_in_the_stream(env):
    node, _hub, drv = await _node(env)
    ev = await _reconnect_and_settle(drv, node.clients[-1], error=TimeoutError("Ping response not received"))
    markers = [(r["event"], r["message"]) for r in drv.restarts.logs.history() if r["type"] == "marker"]
    assert markers[0] == ("online", "Connected")
    assert markers[1][0] == "offline" and "Ping response not received" in markers[1][1]
    assert markers[2] == ("online", "Reconnected")
    assert markers[3] == (ev["kind"], ev["summary"])
    await drv.async_unload()


async def test_a_config_dump_resubscribes_with_dump_config_and_then_drops_back(env, monkeypatch):
    monkeypatch.setattr(esphome_logs, "DUMP_HOLD_S", 0.05)
    node, _hub, drv = await _node(env)
    client, mon = node.clients[-1], drv.restarts
    mon.request_dump_config()
    assert client.dumps[-1] is True and client.log_levels[-1] == CONFIG
    assert mon.logs.history()[-1]["event"] == "dump_config"
    await asyncio.sleep(0.3)
    assert client.log_levels[-1] == WARN and client.dumps[-1] is False
    await node.clients[-1].drop()
    with pytest.raises(RuntimeError):
        mon.request_dump_config()
    await drv.async_unload()


# -- the API -------------------------------------------------------------------------------------------------------

async def test_the_api_is_for_admins_only(env):
    _, hub, drv = await _node(env)
    client = _app(hub)
    for method, path in (("GET", "/hub/devices/dev1/logs"), ("GET", "/hub/devices/dev1/logs/stream"),
                         ("POST", "/hub/devices/dev1/logs/dump_config")):
        assert client.request(method, path).status_code == 401
        assert client.request(method, path, headers={"Authorization": "Bearer nonsense"}).status_code == 401
        assert client.request(method, path, headers={"Authorization": f"Bearer {USER_TOKEN}"}).status_code == 403
    assert client.get("/hub/devices/dev1/logs", headers={"X-Internal-Secret": SECRET}).status_code == 200
    await drv.async_unload()


async def test_the_api_answers_history_text_and_a_stream(env):
    node, hub, drv = await _node(env)
    node.clients[-1].log("[W][wifi:1]: weak signal token=made-up-token", level=WARN)
    client, h = _app(hub), {"Authorization": f"Bearer {SECRET}"}
    body = client.get("/hub/devices/dev1/logs", headers=h, params={"level": "warning"}).json()
    assert body["supported"] and body["connected"] and body["level"] == "warning"
    line = [r for r in body["lines"] if r["type"] == "line"][-1]
    assert line["tag"] == "wifi" and line["message"] == "weak signal token=[redacted]" and line["level"] == "warning"
    assert body["max"] == esphome_logs.BUFFER_LINES and body["configured_level"] == "warning"
    assert client.get("/hub/devices/dev1/logs", headers=h, params={"level": "loud"}).status_code == 400
    assert client.get("/hub/devices/nope/logs", headers=h).status_code == 404
    text = client.get("/hub/devices/dev1/logs", headers=h, params={"format": "text"})
    assert text.headers["content-type"].startswith("text/plain") and "[WARNING] [wifi] weak signal" in text.text
    assert "made-up-token" not in text.text

    resp = client.get("/hub/devices/dev1/logs/stream", headers=h, params={"level": "verbose", "max_s": 0.2})
    items = [json.loads(x) for x in resp.text.splitlines()]
    assert resp.headers["content-type"].startswith("application/x-ndjson")
    assert items[0]["type"] == "hello" and items[0]["connected"] and items[-1]["type"] == "end"
    assert any(i.get("message") == "weak signal token=[redacted]" for i in items)
    assert drv.restarts.logs.viewers == set() and node.clients[-1].log_levels[-1] == WARN

    assert client.post("/hub/devices/dev1/logs/dump_config", headers=h).json() == {"ok": True, "device_id": "dev1"}
    assert node.clients[-1].dumps[-1] is True
    await node.clients[-1].drop()
    assert client.post("/hub/devices/dev1/logs/dump_config", headers=h).status_code == 409
    hub.devices["dev1"].config_entries = ["other"]
    assert client.get("/hub/devices/dev1/logs", headers=h).json() == {"supported": False, "device_id": "dev1"}
    await drv.async_unload()


class FakeConn:
    def __init__(self, hub, admin=True):
        self.hub = hub
        self.principal = Principal(user_id="u", name="U", is_admin=admin)
        self.subs: dict = {}
        self._tasks: set = set()
        self.sent: list[dict] = []

    async def send(self, msg):
        self.sent.append(msg)

    async def result(self, mid, result=None):
        self.sent.append({"id": mid, "type": "result", "success": True})

    async def error(self, mid, code, message):
        self.sent.append({"id": mid, "type": "result", "success": False, "error": {"code": code}})


async def test_the_websocket_subscription_streams_and_unsubscribing_drops_the_level(env):
    node, hub, drv = await _node(env)
    _app(hub)  # registers the commands
    subscribe = hub_api.WS_COMMANDS["homehub/esphome/logs/subscribe"]
    refused = FakeConn(hub, admin=False)
    await subscribe(refused, 1, {"device_id": "dev1"})
    assert refused.sent[-1]["error"]["code"] == "unauthorized" and not refused.subs

    conn = FakeConn(hub)
    await subscribe(conn, 7, {"device_id": "dev1", "level": "debug"})
    assert conn.sent[0] == {"id": 7, "type": "result", "success": True}
    for _ in range(20):
        await asyncio.sleep(0.01)
        if drv.restarts.logs.viewers:
            break
    assert node.clients[-1].log_levels[-1] == DEBUG
    node.clients[-1].log("[D][sensor:5]: reading", level=DEBUG)
    await asyncio.sleep(0.05)
    events = [m["event"] for m in conn.sent if m["type"] == "event"]
    assert events[0]["type"] == "hello" and events[-1]["message"] == "reading"
    conn.subs.pop(7)()
    await asyncio.sleep(0.05)
    assert drv.restarts.logs.viewers == set() and node.clients[-1].log_levels[-1] == WARN

    dump = hub_api.WS_COMMANDS["homehub/esphome/logs/dump_config"]
    await dump(conn, 8, {"device_id": "dev1"})
    assert conn.sent[-1]["success"] and node.clients[-1].dumps[-1] is True
    await dump(conn, 9, {"device_id": "nope"})
    assert conn.sent[-1]["error"]["code"] == "not_found"
    await drv.async_unload()


def test_the_restart_monitor_keeps_its_own_short_line_cut():
    assert len(clean_line("x " * 400)) == esphome_restarts.LINE_MAX_CHARS
    assert len(clean_line("x " * 400, 2000)) == 799

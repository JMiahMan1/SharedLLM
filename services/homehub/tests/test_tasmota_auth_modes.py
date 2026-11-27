"""A right Tasmota web password must never be reported as wrong.

Newer Tasmota firmware has "HTTP API Enable" (Configuration, Configure Other): off, ``/cm`` hangs up on every
request while the web page still logs in. Other builds take the password on ``/cm`` only one way (query
arguments or HTTP Basic) and hang up on the other. The flow checks the password against the device's own login
page, tries ``/cm`` each way, records the way that worked as ``auth_mode``, and the driver sends it that way.
Every password, address and MAC here is made up.
"""
from __future__ import annotations

import asyncio
import base64
import json
from urllib.parse import parse_qs, unquote, urlparse

import pytest
from cryptography.fernet import Fernet

from services.homehub import flows, manual_flows
from services.homehub.core import AuthRequired, ConfigEntry, DeviceUnavailable, Hub
from services.homehub.drivers import tasmota
from services.homehub.drivers.tasmota import TasmotaDriver
from services.homehub.runtime import Runtime
from services.homehub.store import Store

PASSWORD = "made-up-web-pass"
MAC = "02:00:00:00:0c:01"
STATUS0 = {"Status": {"DeviceName": "Test Lamp", "FriendlyName": ["Test Lamp"]},
           "StatusNET": {"Mac": MAC.upper()}, "StatusFWR": {"Version": "15.6.0"},
           "StatusSTS": {"POWER": "ON"}}


class Device:
    """A tiny Tasmota on 127.0.0.1. ``/`` is behind HTTP Basic admin:PASSWORD; ``/cm`` answers only the
    request shapes in ``accepts`` ("both", "params", "basic") and hangs up on every other one."""

    def __init__(self, accepts: set[str], *, refuse_401: bool = False, password: str = PASSWORD) -> None:
        self.accepts = accepts
        self.refuse_401 = refuse_401  # answer a refused /cm as Tasmota 15 does (401 + WARNING), not by hanging up
        self.password = password
        self.cm_shapes: list[str] = []
        self.cm_targets: list[str] = []
        self.page_auths: list[bool] = []
        self.server: asyncio.AbstractServer | None = None
        self.host = ""

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        head = (await reader.readuntil(b"\r\n\r\n")).decode()
        target = head.split("\r\n", 1)[0].split(" ")[1]
        auth = next((h.split(":", 1)[1].strip() for h in head.split("\r\n")
                     if h.lower().startswith("authorization:")), "")
        good_basic = auth == "Basic " + base64.b64encode(f"admin:{self.password}".encode()).decode()
        if target.startswith("/cm"):
            self.cm_targets.append(target)
            query = parse_qs(urlparse(target).query)
            good_params = query.get("user") == ["admin"] and query.get("password") == [self.password]
            shape = {(True, True): "both", (True, False): "params", (False, True): "basic"}.get(
                (good_params, good_basic), "none")
            self.cm_shapes.append(shape)
            if shape not in self.accepts:
                if not self.refuse_401:
                    writer.close()  # no reply at all
                    return
                warn = b'{"WARNING":"Need user=<username>&password=<password>"}'
                writer.write(b"HTTP/1.1 401 Unauthorized\r\nContent-Type: application/json\r\n"
                             b"Content-Length: %d\r\n\r\n" % len(warn) + warn)
                await writer.drain()
                writer.close()
                return
            body = json.dumps(STATUS0 if query.get("cmnd") == ["Status 0"] else {"POWER": "OFF"}).encode()
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: %d\r\n\r\n"
                         % len(body) + body)
        elif good_basic:
            self.page_auths.append(True)
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: 2\r\n\r\nok")
        else:
            self.page_auths.append(bool(auth))
            writer.write(b'HTTP/1.1 401 Unauthorized\r\nWWW-Authenticate: Basic realm="Login Required"\r\n'
                         b"Content-Length: 0\r\n\r\n")
        await writer.drain()
        writer.close()

    async def __aenter__(self) -> Device:
        self.server = await asyncio.start_server(self.handle, "127.0.0.1", 0)
        self.host = f"127.0.0.1:{self.server.sockets[0].getsockname()[1]}"
        return self

    async def __aexit__(self, *exc: object) -> None:
        assert self.server is not None
        self.server.close()
        await self.server.wait_closed()


def tried(dev: Device) -> list[str]:
    """The ways ``/cm`` was asked, in order (aiohttp itself retries a GET the server hung up on once)."""
    return list(dict.fromkeys(dev.cm_shapes))


class FakeRuntime(Runtime):
    def start_entry(self, entry):
        pass

    async def stop_entry(self, entry_id):
        pass


@pytest.fixture
def rt(tmp_path):
    store = Store(str(tmp_path / "hub.db"), Fernet.generate_key().decode())
    hub = Hub(time_zone="UTC")
    hub.store = store
    return FakeRuntime(hub, store)


def finding(host: str) -> dict:
    return {"id": "tasmota:test-lamp", "integration": "tasmota", "host": host, "ids": [], "props": {"locked": True}}


# -- the flow ---------------------------------------------------------------------------------------

async def test_a_right_password_cm_takes_only_as_arguments_adopts_and_records_params(rt):
    async with Device({"params"}) as dev:
        result = await flows.adopt(rt, finding(dev.host), {"input": {"password": PASSWORD}})
    entry = rt.hub.config_entries[result["entry_id"]]
    assert entry.data["auth_mode"] == "params" and entry.data["password"] == PASSWORD
    assert tried(dev) == ["both", "params"]


async def test_a_right_password_cm_takes_only_as_basic_records_basic(rt):
    async with Device({"basic"}) as dev:
        result = await flows.adopt(rt, finding(dev.host), {"input": {"password": PASSWORD}})
    assert rt.hub.config_entries[result["entry_id"]].data["auth_mode"] == "basic"
    assert tried(dev) == ["both", "params", "basic"]


async def test_a_device_that_takes_both_keeps_the_entry_as_it_always_was(rt):
    async with Device({"both"}) as dev:
        result = await flows.adopt(rt, finding(dev.host), {"input": {"password": PASSWORD}})
    assert "auth_mode" not in rt.hub.config_entries[result["entry_id"]].data
    assert dev.cm_shapes == ["both"] and dev.page_auths == []  # nothing extra asked


async def test_a_right_password_with_the_http_api_off_says_so_and_not_that_it_is_wrong():
    async with Device(set()) as dev:
        with pytest.raises(flows.FlowError) as err:
            await flows.tasmota_status(dev.host, PASSWORD)
    msg = str(err.value)
    assert msg == flows.TASMOTA_HTTP_API_OFF
    assert "HTTP API Enable" in msg and "not accepted" not in msg and PASSWORD not in msg
    assert tried(dev) == ["both", "params", "basic"]


async def test_a_wrong_password_is_not_accepted_because_the_web_page_refuses_it_too():
    async with Device({"both", "params", "basic"}) as dev:
        with pytest.raises(flows.NeedsInput) as need:
            await flows.tasmota_status(dev.host, "not-the-password")
    assert need.value.error == "The password was not accepted."
    assert need.value.needs == [flows.TASMOTA_PASSWORD_REQUIRED]
    assert tried(dev) == ["none"]  # refused at the page, /cm is not asked again


async def test_no_password_asks_for_one():
    async with Device({"both"}) as dev:
        with pytest.raises(flows.NeedsInput) as need:
            await flows.tasmota_status(dev.host, None)
    assert need.value.needs == [flows.TASMOTA_PASSWORD_REQUIRED] and need.value.error is None


async def test_a_reconfigured_password_records_the_way_cm_takes_it():
    async with Device({"basic"}) as dev:
        data = {"host": dev.host, "username": "admin", "password": PASSWORD}
        await manual_flows.check_tasmota(data)
    assert data["auth_mode"] == "basic"
    async with Device({"both"}) as dev:
        data = {"host": dev.host, "username": "admin", "password": PASSWORD, "auth_mode": "basic"}
        await manual_flows.check_tasmota(data)
    assert "auth_mode" not in data


# -- the driver -------------------------------------------------------------------------------------

def test_cm_request_sends_the_password_the_recorded_way():
    both = tasmota.cm_request("Power1 On", PASSWORD)
    assert both[0]["password"] == PASSWORD and both[1] is not None
    params = tasmota.cm_request("Power1 On", PASSWORD, "params")
    assert params[0]["password"] == PASSWORD and params[1] is None
    basic = tasmota.cm_request("Power1 On", PASSWORD, "basic")
    assert "password" not in basic[0] and basic[1] is not None
    assert tasmota.cm_request("Power1 On", PASSWORD, "bogus") == both
    assert tasmota.cm_request("Power1 On", None, "basic") == ({"cmnd": "Power1 On"}, None)


def driver(host: str, data_extra: dict) -> TasmotaDriver:
    hub = Hub(time_zone="UTC")
    entry = ConfigEntry("t1", "tasmota", "Test Lamp",
                        {"host": host, "mac": MAC, "username": "admin", "password": PASSWORD, **data_extra})
    hub.config_entries["t1"] = entry
    return TasmotaDriver(hub, entry)


@pytest.mark.parametrize("extra,accepts,shape", [
    ({}, {"both"}, "both"),  # an existing entry: unchanged
    ({"auth_mode": "params"}, {"params"}, "params"),
    ({"auth_mode": "basic"}, {"basic"}, "basic"),
])
async def test_the_driver_sends_the_password_the_entry_says(extra, accepts, shape):
    async with Device(accepts) as dev:
        drv = driver(dev.host, extra)
        try:
            assert await drv._command("Status 0") == STATUS0
        finally:
            await drv.async_unload()
    assert dev.cm_shapes == [shape]


async def test_the_driver_tells_an_http_api_that_is_off_from_a_wrong_password():
    async with Device(set()) as dev:
        drv = driver(dev.host, {})
        try:
            with pytest.raises(DeviceUnavailable) as err:
                await drv._command("Status 0")
        finally:
            await drv.async_unload()
    assert "HTTP API Enable" in str(err.value) and PASSWORD not in str(err.value)
    assert not isinstance(err.value, AuthRequired)


async def test_the_driver_still_reports_a_wrong_password_as_a_password():
    async with Device({"both"}) as dev:
        drv = driver(dev.host, {"password": "not-the-password"})
        try:
            with pytest.raises(AuthRequired):
                await drv._command("Status 0")
        finally:
            await drv.async_unload()


# -- Tasmota 15 with its HTTP API on: a refused /cm is 401 + WARNING ------------------------------------

async def test_a_401_warning_for_a_right_password_tries_the_other_ways(rt):
    async with Device({"params"}, refuse_401=True) as dev:
        result = await flows.adopt(rt, finding(dev.host), {"input": {"password": PASSWORD}})
    assert rt.hub.config_entries[result["entry_id"]].data["auth_mode"] == "params"


async def test_a_refused_password_is_logged_once_with_what_the_device_said_and_never_the_password(caplog):
    wrong = "Wr0ng#&+ pw"
    async with Device(set(), refuse_401=True) as dev:
        with caplog.at_level("WARNING", logger="homehub.flows"), pytest.raises(flows.NeedsInput) as need:
            await flows.tasmota_status(dev.host, wrong)
    assert need.value.error == "The password was not accepted."
    records = [r for r in caplog.records if r.name == "homehub.flows" and r.levelname == "WARNING"]
    assert len(records) == 1
    text = records[0].getMessage()
    assert wrong not in text and "Wr0ng" not in text
    assert "'mode': 'both'" in text and "'status': 401" in text and "Need user=" in text
    assert "'length': 11" in text and "'has_hash': True" in text and "'has_plus': True" in text
    assert "'has_space': True" in text and "'has_question': False" in text and "'has_non_ascii': False" in text


async def test_an_accepted_password_logs_no_warning(caplog):
    async with Device({"both"}) as dev:
        with caplog.at_level("WARNING", logger="homehub.flows"):
            await flows.tasmota_status(dev.host, PASSWORD)
    assert not [r for r in caplog.records if r.levelname == "WARNING"]


# -- what the device's URL decoder reads back -----------------------------------------------------------

AWKWARD = "a#b&c+d%e f=g?h"


def test_cm_query_percent_encodes_every_reserved_character():
    query = tasmota.cm_query(tasmota.cm_params("Status 0", AWKWARD))
    assert "password=a%23b%26c%2Bd%25e%20f%3Dg%3Fh" in query and "cmnd=Status%200" in query
    assert "+" not in query and " " not in query and "#" not in query  # nothing a decoder could read two ways
    assert parse_qs(query)["password"] == [AWKWARD]


@pytest.mark.parametrize("password", [AWKWARD, "plus+only", "sp ace", "100%", "x=y", "caf\u00e9"])
async def test_an_awkward_password_reaches_the_device_exactly(password):
    async with Device({"both"}, password=password) as dev:
        status = await flows.tasmota_status(dev.host, password)
    assert status["Status"]["DeviceName"] == "Test Lamp" and dev.cm_shapes == ["both"]
    raw = urlparse(dev.cm_targets[0]).query
    assert "+" not in raw and " " not in raw  # a literal + is %2B, a space %20
    assert unquote(raw.split("password=", 1)[1].split("&", 1)[0]) == password


def test_a_reply_that_echoed_the_password_is_redacted_in_the_log():
    assert tasmota.redact("got a#b%26c and a%23b%26c", "a#b&c") == "got a#b%26c and ***"
    warning = '{"WARNING":"Need user=<username>&password=<password>"}'
    assert tasmota.redact(warning, "made-up") == warning  # Tasmota's own warning is kept whole


def test_password_traits_are_booleans_and_a_length_only():
    traits = tasmota.password_traits("caf\u00e9 =?%")
    assert traits["length"] == 8 and traits["has_non_ascii"] and traits["has_space"] and traits["has_equals"]
    assert all(isinstance(v, bool) for k, v in traits.items() if k != "length")

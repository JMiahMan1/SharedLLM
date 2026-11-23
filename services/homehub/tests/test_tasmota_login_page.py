"""A Tasmota whose firmware closes /cm without its web password and answers its pages with a
Basic login challenge (the "Piano-Lamp" case): adoption must ask for the password, not report
the device as not answering, and the password must also go as HTTP Basic credentials."""
from __future__ import annotations

import asyncio
import base64
import json

import aiohttp
import pytest

from services.homehub import flows
from services.homehub.drivers import tasmota

PASSWORD = "test-web-pass"  # made up


async def _device(require_basic: bool):
    """A tiny HTTP device on 127.0.0.1: /cm needs Basic admin:PASSWORD, / asks for a login."""
    good = "Basic " + base64.b64encode(f"admin:{PASSWORD}".encode()).decode()

    async def handle(reader, writer):
        head = (await reader.readuntil(b"\r\n\r\n")).decode()
        line = head.split("\r\n", 1)[0]
        auth = next((h.split(":", 1)[1].strip() for h in head.split("\r\n") if h.lower().startswith("authorization:")), "")
        if line.startswith("GET /cm"):
            if require_basic and auth != good:
                writer.close()  # no reply at all, like the lamp
                return
            body = json.dumps({"Status": {"DeviceName": "Piano-Lamp"}}).encode()
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: %d\r\n\r\n" % len(body) + body)
        else:
            writer.write(b'HTTP/1.1 401 Unauthorized\r\nWWW-Authenticate: Basic realm="Login Required"\r\nContent-Length: 0\r\n\r\n')
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    return server, f"127.0.0.1:{port}"


@pytest.mark.asyncio
async def test_a_dropped_cm_behind_a_login_page_asks_for_the_password():
    server, host = await _device(require_basic=True)
    async with server:
        with pytest.raises(flows.NeedsInput) as need:
            await flows.tasmota_status(host, None)
        assert need.value.needs == [flows.TASMOTA_PASSWORD_REQUIRED] and need.value.error is None
        status = await flows.tasmota_status(host, PASSWORD)
    assert status["Status"]["DeviceName"] == "Piano-Lamp"


@pytest.mark.asyncio
async def test_a_wrong_password_is_reported_as_not_accepted():
    server, host = await _device(require_basic=True)
    async with server:
        with pytest.raises(flows.NeedsInput) as need:
            await flows.tasmota_status(host, "wrong")
    assert need.value.error == "The password was not accepted."


@pytest.mark.asyncio
async def test_a_device_that_hangs_up_with_no_login_page_still_did_not_answer():
    async def handle(reader, writer):
        await reader.readuntil(b"\r\n\r\n")
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    host = f"127.0.0.1:{server.sockets[0].getsockname()[1]}"
    async with server:
        with pytest.raises(flows.FlowError) as err:
            await flows.tasmota_status(host, None)
    assert "did not answer" in str(err.value)


def test_basic_auth_is_admin_with_the_web_password():
    assert tasmota.basic_auth(None) is None
    assert tasmota.basic_auth(PASSWORD) == aiohttp.BasicAuth("admin", PASSWORD)

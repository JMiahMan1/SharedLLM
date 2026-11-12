"""Adding a new Wi-Fi Matter device over Bluetooth (services/homehub/matter_ble.py).

A fake Matter Server answers the WebSocket commands the matter.js server 1.5.5
has for this: ``server_info`` (``bluetooth_enabled``), ``set_wifi_credentials``
``{id, ssid, credentials}``, ``commission_with_code`` with ``network_only:
false`` and ``wifi_credentials_id``, and ``remove_wifi_credentials {id}``;
failures carry ``error_code``, ``details`` and a ``reason``. The network name and
password are made up.
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import sqlite3

import httpx
import pytest
from cryptography.fernet import Fernet

from services.homehub import flows, manual_flows, matter_ble
from services.homehub.api import build_app
from services.homehub.auth import Auth
from services.homehub.drivers.matter import MatterCommandError, MatterDriver
from services.homehub.store import Store
from services.homehub.tests.conftest import make_hub
from services.homehub.tests.test_drivers_matter import ENTITIES, FakeMatterServer
from services.homehub.tests.test_flows import NEW_NODE, FakeRuntime

SECRET = "internal-secret"
H = {"Authorization": f"Bearer {SECRET}"}
SSID = "ExampleNet"
PASSWORD = "example-passphrase-1"  # made up
CODE = "34970112332"


class FakeBleMatterServer(FakeMatterServer):
    """The fake server plus Bluetooth commissioning, with credentials kept the way the real one keeps them."""

    def __init__(self) -> None:
        super().__init__()
        self.info["bluetooth_enabled"] = True
        self.wifi: dict[str, dict] = {}   # credential id -> {"ssid", "credentials"}
        self.calls: list[tuple[str, dict]] = []
        self.outcome: dict = {"node": NEW_NODE}  # or {"error_code", "details", "reason"?}
        self.gate: asyncio.Event | None = None    # set() lets a waiting commission finish
        self.tasks: set[asyncio.Task] = set()

    async def answer(self, ws, req: dict) -> None:
        mid, cmd, args = req["message_id"], req["command"], req.get("args") or {}
        if cmd in self.errors or cmd not in ("server_info", "set_wifi_credentials", "remove_wifi_credentials",
                                             "commission_with_code"):
            await super().answer(ws, req)
            return
        self.calls.append((cmd, dict(args)))
        result: object = {}
        if cmd == "server_info":
            result = dict(self.info)
        elif cmd == "set_wifi_credentials":
            self.wifi[args["id"]] = {"ssid": args["ssid"], "credentials": args["credentials"]}
        elif cmd == "remove_wifi_credentials":
            self.wifi.pop(args["id"], None)
        else:  # commission_with_code: answered in the background so the socket keeps reading
            task = asyncio.ensure_future(self._commission(ws, mid, args))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)
            return
        await ws.send_str(json.dumps({"message_id": mid, "result": result}))

    async def _commission(self, ws, mid: str, args: dict) -> None:
        # What the real server does: a stored credential is only used over Bluetooth.
        assert args["network_only"] is False and args["wifi_credentials_id"] in self.wifi
        if self.gate is not None:
            await self.gate.wait()
        if "node" in self.outcome:
            node = copy.deepcopy(self.outcome["node"])
            self.nodes[node["node_id"]] = node
            await ws.send_str(json.dumps({"message_id": mid, "result": node}))
        else:
            await ws.send_str(json.dumps({"message_id": mid, **self.outcome}))


@pytest.fixture
async def server():
    srv = FakeBleMatterServer()
    await srv.start()
    yield srv
    await srv.close()


@pytest.fixture
async def rt(server, tmp_path):
    hub, entry = make_hub("matter", data={"url": server.url}, entities=ENTITIES)
    store = Store(str(tmp_path / "hub.db"), Fernet.generate_key().decode())
    hub.store = store
    runtime = FakeRuntime(hub, store)
    drv = MatterDriver(hub, entry)
    await drv.async_setup()
    hub.drivers[entry.entry_id] = drv
    matter_ble.JOBS.clear()
    yield runtime
    for job in matter_ble.JOBS.values():
        if job.task is not None and not job.task.done():
            job.task.cancel()
    matter_ble.JOBS.clear()
    await drv.async_unload()


@pytest.fixture
async def client(rt):
    app = build_app(rt, Auth(rt.store, SECRET))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://hub") as c:
        yield c


def finding() -> dict:
    return manual_flows.manual_finding("matter", {})


async def until_done(rt, user_input: dict, **body) -> dict:
    """Adopt, then poll with the session the way the dialog does, until it is not pending."""
    try:
        return await flows.adopt(rt, finding(), {**body, "input": user_input})
    except flows.Pending as wait:
        session = wait.session
    for _ in range(500):
        await asyncio.sleep(0.01)
        try:
            return await flows.adopt(rt, finding(), {"input": {"method": "ble", "session": session}})
        except flows.Pending:
            continue
    raise AssertionError("still pending")


# -- the setting --------------------------------------------------------------------------

def test_the_household_wifi_is_sealed_and_only_its_name_is_shown(rt, tmp_path):
    assert matter_ble.wifi_status(rt) == {"configured": False, "ssid": None, "updated_at": None}
    status = matter_ble.set_wifi(rt, SSID, PASSWORD)
    assert status["configured"] is True and status["ssid"] == SSID and status["updated_at"]
    assert PASSWORD not in json.dumps(status)
    raw = sqlite3.connect(str(tmp_path / "hub.db")).execute("SELECT secret FROM blobs WHERE name=?",
                                                            (matter_ble.WIFI_BLOB,)).fetchone()[0]
    assert PASSWORD.encode() not in raw and SSID.encode() not in raw
    assert matter_ble.clear_wifi(rt)["configured"] is False


@pytest.mark.parametrize(("ssid", "password", "words"), [
    ("", PASSWORD, "network's name"),
    ("x" * 33, PASSWORD, "32 bytes"),
    ("Bad\nName", PASSWORD, "control"),
    (SSID, "", "password"),
    (SSID, "short", "8 to 63"),
    (SSID, "x" * 64, "8 to 63"),
])
def test_bad_wifi_is_refused_with_what_is_wrong(rt, ssid, password, words):
    with pytest.raises(flows.FlowError) as err:
        matter_ble.set_wifi(rt, ssid, password)
    assert err.value.status == 400 and words in str(err.value)


def test_a_64_digit_hex_key_is_a_valid_password(rt):
    assert matter_ble.set_wifi(rt, SSID, "ab" * 32)["configured"] is True


def test_without_a_fernet_key_the_password_is_not_kept(tmp_path):
    hub, _ = make_hub("matter")
    store = Store(str(tmp_path / "plain.db"), None)
    runtime = FakeRuntime(hub, store)
    with pytest.raises(flows.FlowError) as err:
        matter_ble.set_wifi(runtime, SSID, PASSWORD)
    assert err.value.status == 503 and "FERNET_KEY" in str(err.value)


async def test_the_routes_are_admin_only_and_never_return_the_password(client, server):
    assert (await client.get("/hub/matter/settings")).status_code == 401
    assert (await client.put("/hub/matter/wifi", json={"ssid": SSID, "password": PASSWORD})).status_code == 401
    assert (await client.delete("/hub/matter/wifi")).status_code == 401

    r = await client.get("/hub/matter/settings", headers=H)
    assert r.status_code == 200
    assert r.json() == {"bluetooth": {"enabled": True, "setting": "MATTER_BLUETOOTH_ADAPTER"},
                        "wifi": {"configured": False, "ssid": None, "updated_at": None}}
    r = await client.put("/hub/matter/wifi", headers=H, json={"ssid": SSID, "password": PASSWORD})
    assert r.status_code == 200 and r.json()["ssid"] == SSID and PASSWORD not in r.text
    r = await client.get("/hub/matter/settings", headers=H)
    assert r.json()["wifi"]["configured"] is True and PASSWORD not in r.text
    r = await client.put("/hub/matter/wifi", headers=H, json={"ssid": SSID, "password": "short"})
    assert r.status_code == 400 and "8 to 63" in r.json()["message"]
    assert (await client.put("/hub/matter/wifi", headers=H, json=["x"])).status_code == 400
    r = await client.delete("/hub/matter/wifi", headers=H)
    assert r.status_code == 200 and r.json()["configured"] is False

    server.info["bluetooth_enabled"] = False
    r = await client.get("/hub/matter/settings", headers=H)
    assert r.json()["bluetooth"]["enabled"] is False


# -- the flow -------------------------------------------------------------------------------

async def test_a_new_device_is_added_over_bluetooth_with_progress(client, rt, server, caplog):
    caplog.set_level(logging.DEBUG)
    matter_ble.set_wifi(rt, SSID, PASSWORD)
    server.gate = asyncio.Event()
    body = {"code": "3497-011-2332", "method": "ble", "name": "Desk lamp"}
    r = await client.post("/hub/matter/commission", headers=H, json=body)
    assert r.status_code == 200
    first = r.json()
    assert first["status"] == "pending" and first["session"] and first["poll_ms"] == matter_ble.POLL_MS
    assert first["step"].startswith("matter_ble_")

    # Still finding the device: the poll says so, with the time so far.
    for _ in range(100):
        r = await client.post("/hub/matter/commission", headers=H, json={**body, "session": first["session"]})
        if r.json()["step"] == "matter_ble_commission":
            break
        await asyncio.sleep(0.01)
    assert r.json()["status"] == "pending" and "Bluetooth" in r.json()["message"] and "so far" in r.json()["message"]
    assert server.wifi == {matter_ble.SERVER_CRED_ID: {"ssid": SSID, "credentials": PASSWORD}}

    server.gate.set()
    for _ in range(200):
        await asyncio.sleep(0.01)
        r = await client.post("/hub/matter/commission", headers=H, json={**body, "session": first["session"]})
        if r.json()["status"] != "pending":
            break
    result = r.json()
    assert r.status_code == 200 and result["status"] == "adopted" and result["node_id"] == 80

    cmds = [c for c, _ in server.calls]
    assert cmds == ["server_info", "set_wifi_credentials", "commission_with_code", "remove_wifi_credentials"]
    commission = dict(server.calls[2][1])
    assert commission == {"code": CODE, "network_only": False, "wifi_credentials_id": matter_ble.SERVER_CRED_ID,
                          "discovery_timeout_s": 90}
    assert server.wifi == {}  # the password was on the server only while the device was being added
    # Registered from the node's own clusters, like every Matter node.
    dev = rt.hub.devices[result["device_id"]]
    assert dev.name == "Smart Bulb" and dev.name_by_user == "Desk lamp" and dev.serial_number == "SN-0001"
    light = rt.hub.states.get("light.desk_lamp")
    assert light is not None and light.attributes["supported_color_modes"] == ["color_temp", "hs", "xy"]
    # The session is used up.
    r = await client.post("/hub/matter/commission", headers=H, json={**body, "session": first["session"]})
    assert r.status_code == 410
    assert all(PASSWORD not in rec.getMessage() for rec in caplog.records)


async def test_bluetooth_off_fails_fast_naming_the_setting(rt, server):
    matter_ble.set_wifi(rt, SSID, PASSWORD)
    server.info["bluetooth_enabled"] = False
    with pytest.raises(flows.FlowError) as err:
        await flows.adopt(rt, finding(), {"input": {"method": "ble", "code": CODE}})
    assert err.value.status == 503 and "MATTER_BLUETOOTH_ADAPTER" in str(err.value)
    assert [c for c, _ in server.calls] == ["server_info"]
    assert server.wifi == {}


async def test_no_household_wifi_fails_fast_naming_the_setting(client, server):
    r = await client.post("/hub/matter/commission", headers=H, json={"code": CODE, "method": "ble"})
    assert r.status_code == 409
    assert "Wi-Fi for new devices" in r.json()["message"]
    assert [c for c, _ in server.calls] == ["server_info"]


async def test_a_network_for_this_device_overrides_the_household_one(rt, server):
    matter_ble.set_wifi(rt, SSID, PASSWORD)
    result = await until_done(rt, {"method": "ble", "code": CODE, "wifi_ssid": "OtherNet",
                                   "wifi_password": "other-passphrase"})
    assert result["status"] == "adopted"
    sent = next(a for c, a in server.calls if c == "set_wifi_credentials")
    assert sent == {"id": matter_ble.SERVER_CRED_ID, "ssid": "OtherNet", "credentials": "other-passphrase"}
    with pytest.raises(flows.FlowError) as err:
        await flows.adopt(rt, finding(), {"input": {"method": "ble", "code": CODE, "wifi_ssid": "OtherNet"}})
    assert err.value.status == 400 and "both" in str(err.value)


@pytest.mark.parametrize(("outcome", "status", "words"), [
    ({"error_code": 1, "reason": "not-discovered",
      "details": "Commission failed: discovery of node with discriminator 15 failed"}, 404, "not found over Bluetooth"),
    ({"error_code": 1, "reason": "other",
      "details": 'Commission failed: Commissionee failed to connect to WiFi network "ExampleNet": auth failed'},
     400, "Check the password"),
    ({"error_code": 1, "reason": "passcode-mismatch", "details": "The pairing code does not match the device."},
     400, "setup code does not match"),
    ({"error_code": 1, "reason": "attestation-rejected", "details": "Device attestation refused: paa: unknown"},
     502, "certification check"),
    ({"error_code": 1, "reason": "timeout", "details": "Commission failed: timed out"}, 502, "stopped answering"),
    ({"error_code": 0, "details": "something else"}, 502, "something else"),
])
async def test_common_failures_have_clear_messages_and_the_password_is_removed(rt, server, caplog, outcome, status,
                                                                              words):
    caplog.set_level(logging.DEBUG)
    matter_ble.set_wifi(rt, SSID, PASSWORD)
    server.outcome = outcome
    with pytest.raises(flows.FlowError) as err:
        await until_done(rt, {"method": "ble", "code": CODE})
    assert err.value.status == status and words in str(err.value)
    assert [c for c, _ in server.calls][-1] == "remove_wifi_credentials" and server.wifi == {}
    assert all(PASSWORD not in rec.getMessage() for rec in caplog.records)


async def test_one_bluetooth_commissioning_at_a_time(rt, server):
    matter_ble.set_wifi(rt, SSID, PASSWORD)
    server.gate = asyncio.Event()
    with pytest.raises(flows.Pending):
        await flows.adopt(rt, finding(), {"input": {"method": "ble", "code": CODE}})
    with pytest.raises(flows.FlowError) as err:
        await flows.adopt(rt, finding(), {"input": {"method": "ble", "code": CODE}})
    assert err.value.status == 409 and "Another device" in str(err.value)
    server.gate.set()


async def test_a_missing_or_bad_code_asks_for_it_and_an_unknown_session_is_gone(rt):
    with pytest.raises(flows.NeedsInput) as need:
        await flows.adopt(rt, finding(), {"input": {"method": "ble"}})
    assert [n["key"] for n in need.value.needs] == ["method", "code"]
    with pytest.raises(flows.NeedsInput) as need:
        await flows.adopt(rt, finding(), {"input": {"method": "ble", "code": "12"}})
    assert need.value.error
    with pytest.raises(flows.FlowError) as err:
        await flows.adopt(rt, finding(), {"input": {"method": "ble", "session": "nope"}})
    assert err.value.status == 410


async def test_the_share_code_path_is_unchanged(rt, server):
    """``method`` absent or ``network``: the device is already on the network; no Wi-Fi is sent."""
    server.errors["commission_with_code"] = {"error_code": 1, "details": "x", "reason": "not-discovered"}
    with pytest.raises(MatterCommandError) as err:
        await flows.adopt(rt, finding(), {"input": {"method": "network", "code": CODE}})
    assert err.value.reason == "not-discovered" and err.value.code == 1
    assert not any(c == "set_wifi_credentials" for c, _ in server.calls)


def test_the_add_by_hand_form_offers_both_ways():
    fields = manual_flows.fields_for(None, "matter")
    method = next(f for f in fields if f["key"] == "method")
    assert {o["value"] for o in method["options"]} == {"network", "ble"} and method.get("optional")


async def test_the_driver_keeps_server_info_updates(rt, server):
    drv = flows.matter_driver(rt)
    assert drv.server_info["bluetooth_enabled"] is True
    await server.emit("server_info_updated", {**server.info, "bluetooth_enabled": False})
    for _ in range(100):
        if drv.server_info["bluetooth_enabled"] is False:
            break
        await asyncio.sleep(0.01)
    assert drv.server_info["bluetooth_enabled"] is False

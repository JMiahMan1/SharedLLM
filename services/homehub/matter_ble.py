"""Adding a new Wi-Fi Matter device over Bluetooth, and the household Wi-Fi it is given.

A Matter device that is new (or freshly reset) is not on the network yet: it
advertises over Bluetooth LE, and the controller has to give it the Wi-Fi
network's name and password before it can join. The Matter Server does the
Bluetooth part itself (``BLUETOOTH_ADAPTER``, set from ``MATTER_BLUETOOTH_ADAPTER``
in ``.env``; see ``MATTER.md``). The hub's part is:

1. **The household Wi-Fi for new devices**, a hub setting: the SSID and the
   password, sealed with Fernet in the store (``WIFI_BLOB``). The password is
   never logged and never returned by any route; reads say only whether it is
   set, and the SSID.
2. **The flow**, ``adopt_over_ble``: check that the server has Bluetooth, check
   that there is a Wi-Fi to give (the setting, or a per-device override), then
   in the background: ``set_wifi_credentials`` under our own credential id,
   ``commission_with_code`` with ``network_only: false``, and
   ``remove_wifi_credentials`` whatever happened, so the password is on the
   server only while a device is being added. The caller sees ``Pending`` with a
   ``session`` and the current stage, and polls with ``input.session``.
3. The new node is registered by ``flows.register_matter_node``: its device and
   entities come from the node's own clusters, as for every Matter node.

| Route (admins) | What |
|---|---|
| ``GET /hub/matter/settings`` | ``{"bluetooth": {"enabled", "setting"}, "wifi": {"configured", "ssid", "updated_at"}}`` |
| ``PUT /hub/matter/wifi`` | ``{"ssid", "password"}``: set the household Wi-Fi for new devices |
| ``DELETE /hub/matter/wifi`` | forget it |

``POST /hub/matter/commission`` (``discovery_api.py``) with ``"method": "ble"``
answers through ``commission_response`` here.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import secrets
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from services.homehub.auth import Auth, bearer
from services.homehub.core import DeviceUnavailable, HubError
from services.homehub.flows import (
    SETUP_CODE,
    FlowError,
    NeedsInput,
    Pending,
    matter_driver,
    normalize_matter_code,
    register_matter_node,
)

log = logging.getLogger("homehub.matter_ble")

#: The store blob holding ``{"ssid", "password", "updated_at"}`` (sealed).
WIFI_BLOB = "matter_commissioning_wifi"
#: The credential id the hub uses on the Matter Server; the server's own default is left alone.
SERVER_CRED_ID = "homehub-commissioning"
#: The .env setting that gives the Matter Server its Bluetooth adapter.
ADAPTER_SETTING = "MATTER_BLUETOOTH_ADAPTER"
#: A finished job is kept this long for the caller's next poll.
JOB_TTL_S = 900.0
POLL_MS = 2000

BLE_OFF = (f"The Matter Server has no Bluetooth adapter, so new devices cannot be added over Bluetooth. "
           f"Set {ADAPTER_SETTING} in .env to the adapter's number (1 for hci1) and recreate the matter-server "
           "container (see services/homehub/MATTER.md).")
NO_WIFI = ("No household Wi-Fi is set for new Matter devices. An admin sets it under Devices > New devices > "
           "Wi-Fi for new devices, or enter a network for this device.")
NOT_STORED = "FERNET_KEY is not set, so the hub cannot keep a Wi-Fi password."

METHOD = {"key": "method", "label": "How is the device connected?", "type": "select", "optional": True,
          "options": [{"value": "network", "label": "Already on the network (share code)"},
                      {"value": "ble", "label": "New device, over Bluetooth"}]}
WIFI_SSID = {"key": "wifi_ssid", "label": "Wi-Fi network (optional)", "type": "text", "optional": True,
             "help": "Only to use another network than the household Wi-Fi for this device."}
WIFI_PASSWORD = {"key": "wifi_password", "label": "Wi-Fi password", "type": "password", "optional": True}

STAGES = {
    "prepare": "Getting the Matter Server ready.",
    "commission": ("Finding the device over Bluetooth, giving it the Wi-Fi details and waiting for it to join. "
                   "Keep it powered and close to the hub; this can take a few minutes"),
    "register": "The device joined the network; adding its controls.",
}

_SSID_MAX_BYTES = 32
_HEX64 = re.compile(r"^[0-9A-Fa-f]{64}$")


# -- the setting --------------------------------------------------------------------------

def clean_wifi(ssid: Any, password: Any) -> tuple[str, str]:
    """``(ssid, password)`` as a WPA network accepts them; FlowError (400) naming what is wrong."""
    if not isinstance(ssid, str) or not ssid:
        raise FlowError("Enter the Wi-Fi network's name (SSID).", 400)
    if len(ssid.encode()) > _SSID_MAX_BYTES or any(ord(c) < 32 for c in ssid):
        raise FlowError("A Wi-Fi network name is at most 32 bytes, without control characters.", 400)
    if not isinstance(password, str) or not password:
        raise FlowError("Enter the Wi-Fi password.", 400)
    printable = all(32 <= ord(c) <= 126 for c in password)
    if not (_HEX64.match(password) or (printable and 8 <= len(password) <= 63)):
        raise FlowError("A Wi-Fi password is 8 to 63 printable characters (or a 64 digit hex key).", 400)
    return ssid, password


def _store(runtime: Any) -> Any:
    store = getattr(runtime, "store", None)
    if store is None:
        raise FlowError("The hub's store is not open.", 503)
    return store


def _load(runtime: Any) -> dict[str, Any] | None:
    try:
        blob = _store(runtime).load_blob(WIFI_BLOB, None)
    except RuntimeError as exc:
        raise FlowError(NOT_STORED, 503) from exc
    if isinstance(blob, dict) and blob.get("ssid") and blob.get("password"):
        return blob
    return None


def wifi_status(runtime: Any) -> dict[str, Any]:
    """Whether the household Wi-Fi is set, and its SSID. Never the password."""
    blob = _load(runtime)
    if blob is None:
        return {"configured": False, "ssid": None, "updated_at": None}
    return {"configured": True, "ssid": blob["ssid"], "updated_at": blob.get("updated_at")}


def set_wifi(runtime: Any, ssid: Any, password: Any) -> dict[str, Any]:
    ssid, password = clean_wifi(ssid, password)
    blob = {"ssid": ssid, "password": password, "updated_at": datetime.now(UTC).isoformat(timespec="seconds")}
    try:
        _store(runtime).save_blob(WIFI_BLOB, blob)
    except RuntimeError as exc:
        raise FlowError(NOT_STORED, 503) from exc
    log.info("household Wi-Fi for new Matter devices set")
    return wifi_status(runtime)


def clear_wifi(runtime: Any) -> dict[str, Any]:
    try:
        _store(runtime).save_blob(WIFI_BLOB, {})
    except RuntimeError as exc:
        raise FlowError(NOT_STORED, 503) from exc
    log.info("household Wi-Fi for new Matter devices cleared")
    return wifi_status(runtime)


def _wifi_for(runtime: Any, user_input: dict[str, Any]) -> tuple[str, str]:
    """The per-device override when one is given (both fields), else the setting; FlowError when neither."""
    ssid, password = user_input.get("wifi_ssid"), user_input.get("wifi_password")
    if ssid or password:
        if not (ssid and password):
            raise FlowError("Enter both the Wi-Fi network and its password, or neither to use the household Wi-Fi.",
                            400)
        return clean_wifi(ssid, password)
    blob = _load(runtime)
    if blob is None:
        raise FlowError(NO_WIFI, 409)
    return str(blob["ssid"]), str(blob["password"])


# -- the flow -------------------------------------------------------------------------------

@dataclass
class Job:
    id: str
    finding_id: str
    started: float = field(default_factory=time.monotonic)
    stage: str = "prepare"
    task: asyncio.Task | None = None
    finished: float | None = None


JOBS: dict[str, Job] = {}


def _expire() -> None:
    now = time.monotonic()
    for jid, job in list(JOBS.items()):
        if job.finished is not None and now - job.finished > JOB_TTL_S:
            JOBS.pop(jid, None)


def _pending(job: Job) -> Pending:
    message = STAGES.get(job.stage, "")
    if job.stage == "commission":
        elapsed = int(time.monotonic() - job.started)
        message = f"{message} ({elapsed // 60}:{elapsed % 60:02d} so far)."
    return Pending(job.id, message, step=f"matter_ble_{job.stage}", poll_ms=POLL_MS)


def commission_error(exc: Exception, ssid: str) -> FlowError:
    """A clear message for what went wrong, from the server's ``reason`` and details."""
    from services.homehub.drivers.matter import MatterCommandError

    if isinstance(exc, DeviceUnavailable):
        return FlowError(f"The Matter Server stopped answering while the device was being added: {exc}", 502)
    if not isinstance(exc, MatterCommandError):
        return FlowError(f"Adding the device failed: {exc}", 502)
    details, reason = exc.details, exc.reason
    if "WiFi network" in details or "Wi-Fi network" in details:
        return FlowError(f"The device could not join the Wi-Fi network {ssid!r}. Check the password, and that the "
                         "network is 2.4 GHz and reaches the device. "
                         f"(The device said: {details})", 400)
    if reason == "passcode-mismatch":
        return FlowError("The setup code does not match the device. Check the code and try again.", 400)
    if reason == "invalid-payload":
        return FlowError("That setup code could not be read. Check the code and try again.", 400)
    if reason == "not-discovered":
        return FlowError("The device was not found over Bluetooth. Check that it is powered, new or freshly reset "
                         "(in pairing mode), and within a few metres of the hub, then try again.", 404)
    if reason == "attestation-rejected":
        return FlowError(f"The device failed Matter's certification check (device attestation), so it was not "
                         f"added. {details}", 502)
    if reason == "timeout":
        return FlowError("The device stopped answering while it was being set up. Reset it, keep it close to the "
                         "hub and try again.", 502)
    return FlowError(f"Adding the device failed: {details}", 502)


async def _run(job: Job, runtime: Any, driver: Any, code: str, ssid: str, password: str, name: str | None,
               area_id: str | None) -> dict[str, Any]:
    from services.homehub.drivers.matter import BLE_COMMISSION_TIMEOUT, BLE_DISCOVERY_TIMEOUT_S

    try:
        try:
            await driver.set_wifi_credentials(SERVER_CRED_ID, ssid, password)
        except (HubError, DeviceUnavailable) as exc:
            raise FlowError(f"The Matter Server did not take the Wi-Fi details: {exc}", 502) from exc
        try:
            job.stage = "commission"
            node = await driver.commission_with_code(
                code, network_only=False, timeout=BLE_COMMISSION_TIMEOUT, wifi_credentials_id=SERVER_CRED_ID,
                discovery_timeout_s=BLE_DISCOVERY_TIMEOUT_S)
        except (HubError, DeviceUnavailable) as exc:
            err = commission_error(exc, ssid)
            log.warning("adding a Matter device over Bluetooth failed: %s", err)
            raise err from exc
        finally:
            # The password lives on the server only while a device is being added.
            try:
                await driver.remove_wifi_credentials(SERVER_CRED_ID)
            except Exception as exc:  # the commission's outcome matters more; say so in the log
                log.warning("could not remove the commissioning Wi-Fi from the Matter Server: %s",
                            type(exc).__name__)
        job.stage = "register"
        result = await register_matter_node(runtime, driver, node, name=name, area_id=area_id)
        log.info("Matter node %s added over Bluetooth", result.get("node_id"))
        return result
    finally:
        job.finished = time.monotonic()


async def adopt_over_ble(runtime: Any, finding: dict[str, Any], name: str | None, area_id: str | None,
                         user_input: dict[str, Any]) -> dict[str, Any]:
    """Start (or poll) commissioning a new Wi-Fi Matter device over Bluetooth."""
    _expire()
    finding_id = str(finding.get("id") or "")
    sid = str(user_input.get("session") or "")
    if sid:
        job = JOBS.get(sid)
        if job is None or job.finding_id != finding_id or job.task is None:
            raise FlowError("That commissioning has expired or was cancelled. Start again.", 410)
        if not job.task.done():
            raise _pending(job)
        JOBS.pop(sid, None)
        return job.task.result()  # the registered node, or the job's FlowError

    code = user_input.get("code")
    if not code:
        raise NeedsInput([METHOD, SETUP_CODE], "Enter the device's Matter setup code.")
    try:
        clean = normalize_matter_code(code)
    except FlowError as exc:
        raise NeedsInput([METHOD, SETUP_CODE], "Enter the device's Matter setup code.", str(exc)) from exc
    driver = matter_driver(runtime)
    if driver.compressed_fabric_id is None:
        raise FlowError("The Matter Server has not reported its fabric yet; try again in a minute.", 503)
    try:
        info = await driver.refresh_server_info()
    except (HubError, DeviceUnavailable) as exc:
        raise FlowError(f"The Matter Server did not answer: {exc}", 503) from exc
    if not info.get("bluetooth_enabled"):
        raise FlowError(BLE_OFF, 503)
    ssid, password = _wifi_for(runtime, user_input)
    if any(j.task is not None and not j.task.done() for j in JOBS.values()):
        raise FlowError("Another device is being added over Bluetooth. Wait for it to finish, then try again.", 409)

    job = Job(id=secrets.token_urlsafe(12), finding_id=finding_id)
    job.task = asyncio.ensure_future(_run(job, runtime, driver, clean, ssid, password, name, area_id))
    # Read the outcome so a failure nobody polls for is not logged as "never retrieved".
    job.task.add_done_callback(lambda t: t.cancelled() or t.exception())
    JOBS[job.id] = job
    log.info("adding a Matter device over Bluetooth (%s Wi-Fi)",
             "a per-device" if user_input.get("wifi_ssid") else "the household")
    raise _pending(job)


async def commission_response(runtime: Any, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """``POST /hub/matter/commission`` with ``method: ble``: answers like adopting a discovered device."""
    from services.homehub import flows, manual_flows

    user_input = {k: body[k] for k in ("code", "session", "wifi_ssid", "wifi_password")
                  if isinstance(body.get(k), str) and body.get(k)}
    user_input["method"] = "ble"
    finding = manual_flows.manual_finding("matter", {})
    return await flows.adopt_response(runtime, finding, {"name": body.get("name"), "area_id": body.get("area_id"),
                                                         "input": user_input})


# -- routes ---------------------------------------------------------------------------------

def build_router(runtime: Any, auth: Auth) -> APIRouter:
    router = APIRouter()

    def admin(request: Request) -> None:
        token = bearer(request.headers.get("authorization")) or request.headers.get("x-internal-secret")
        p = auth.authenticate(token)
        if p is None or not p.is_admin:
            raise HTTPException(401, detail="Unauthorized")

    def failure(exc: HubError) -> JSONResponse:
        status = int(getattr(exc, "status", 400) or 400)
        return JSONResponse({"status": "error", "message": str(exc)}, status_code=status)

    async def bluetooth() -> dict[str, Any]:
        out: dict[str, Any] = {"enabled": None, "setting": ADAPTER_SETTING}
        with contextlib.suppress(HubError, DeviceUnavailable):
            out["enabled"] = bool((await matter_driver(runtime).refresh_server_info()).get("bluetooth_enabled"))
        return out

    @router.get("/hub/matter/settings")
    async def settings(request: Request) -> Any:
        admin(request)
        try:
            return {"bluetooth": await bluetooth(), "wifi": wifi_status(runtime)}
        except HubError as exc:
            return failure(exc)

    @router.put("/hub/matter/wifi")
    async def put_wifi(request: Request) -> Any:
        admin(request)
        try:
            body = await request.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            return JSONResponse({"status": "error", "message": "The body must be a JSON object"}, status_code=400)
        try:
            return set_wifi(runtime, body.get("ssid"), body.get("password"))
        except HubError as exc:
            return failure(exc)

    @router.delete("/hub/matter/wifi")
    async def delete_wifi(request: Request) -> Any:
        admin(request)
        try:
            return clear_wifi(runtime)
        except HubError as exc:
            return failure(exc)

    return router

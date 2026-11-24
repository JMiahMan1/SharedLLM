"""Devices > New devices, Integrations and Hardware: adding devices (discovered or by hand), pairing
them, managing what is set up (reconfigure, rename, move, disable, reload, sync, remove/unpair), the
device list built from the hub's registry, and the hub's capability index.

Admins only. Everything is homehub's discovery API (``services/homehub/discovery_api.py``),
reached with the stack's internal secret at ``HOMEHUB_URL`` (no default: blank turns the
HomeHub integration off and these pages answer 503). The secret never leaves
the server; the hub's answers (including "needs input" and "not supported yet")
are passed through with their status.
"""
from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator
from typing import Any

import aiohttp
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from services.gateway.config import INTERNAL_SECRET

log = logging.getLogger("gateway.discovery")

router = APIRouter(prefix="/api/homehub")


def _homehub_base() -> str:
    """HOMEHUB_URL (resolved once at startup); 503 while the HomeHub integration is off."""
    from services.common.hub_login import NO_HOMEHUB_MESSAGE, homehub_url

    base = homehub_url()
    if not base:
        raise HTTPException(status_code=503, detail=NO_HOMEHUB_MESSAGE)
    return base

_TIMEOUT = aiohttp.ClientTimeout(total=30, connect=5)
#: A scan listens for replies for several seconds; commissioning a Matter device takes minutes.
_SCAN_TIMEOUT = aiohttp.ClientTimeout(total=60, connect=5)
_ADOPT_TIMEOUT = aiohttp.ClientTimeout(total=240, connect=5)
#: Statuses whose hub body is for people ("needs a key", "already set up", "not supported yet",
#: "the pairing expired", "could not unpair").
_PASS_THROUGH = {200, 400, 404, 409, 410, 501, 502, 503}
_FINDING_ID = re.compile(r"^[a-z0-9_.:-]{3,200}$")
_REGISTRY_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_DOMAIN = re.compile(r"^[a-z0-9_]{2,40}$")


async def _admin(request: Request) -> str:
    from services.gateway import main as gateway

    return await gateway._require_admin(request)


async def _hub(method: str, path: str, *, params: Any = None, json: Any = None,
               timeout: aiohttp.ClientTimeout = _TIMEOUT) -> tuple[int, Any]:
    """One call to the hub; ``(status, body)``. 502 when it does not answer."""
    from services.gateway.main import shared_http_client

    headers = {"Authorization": f"Bearer {INTERNAL_SECRET}"}
    try:
        async with shared_http_client() as client, client.request(
            method, f"{_homehub_base()}{path}", params=params, json=json, headers=headers, timeout=timeout
        ) as resp:
            try:
                body = await resp.json(content_type=None)
            except Exception:
                body = None
            return resp.status, body
    except (TimeoutError, aiohttp.ClientError) as exc:
        log.warning("[discovery] hub %s %s: %s", method, path, type(exc).__name__)
        raise HTTPException(status_code=502, detail="The device hub did not answer") from exc


def _answer(status: int, body: Any) -> Any:
    if status == 200:
        return body
    if status in _PASS_THROUGH and isinstance(body, dict):
        message = str(body.get("message") or body.get("detail") or "")
        return JSONResponse({**body, "detail": message} if message else body, status_code=status)
    log.warning("[discovery] hub answered HTTP %s", status)
    raise HTTPException(status_code=502, detail="The device hub did not answer")


def _finding(finding_id: str) -> str:
    if not _FINDING_ID.match(finding_id or ""):
        raise HTTPException(status_code=404, detail="No such discovered device")
    return finding_id


@router.get("/discovered")
async def discovered(request: Request, new: bool = False):
    """Devices the hub has seen on the LAN; ``new=true`` for only those not set up and not ignored."""
    await _admin(request)
    return _answer(*await _hub("GET", "/hub/discovered", params={"new": "1"} if new else None))


@router.post("/discovered/scan")
async def scan(request: Request):
    """Look for devices now (SSDP and WiZ; mDNS is always on)."""
    await _admin(request)
    return _answer(*await _hub("POST", "/hub/discovered/scan", timeout=_SCAN_TIMEOUT))


@router.post("/discovered/{finding_id}/adopt")
async def adopt(finding_id: str, request: Request):
    """Add a discovered device: ``{"name"?, "area_id"?, "input": {...}}``."""
    await _admin(request)
    body = await _json(request)
    return _answer(*await _hub("POST", f"/hub/discovered/{_finding(finding_id)}/adopt", json=body,
                               timeout=_ADOPT_TIMEOUT))


@router.post("/discovered/{finding_id}/ignore")
async def ignore(finding_id: str, request: Request):
    """Ignore a discovered device (``{"ignored": false}`` takes it back)."""
    await _admin(request)
    body = await _json(request)
    return _answer(*await _hub("POST", f"/hub/discovered/{_finding(finding_id)}/ignore",
                               json={"ignored": bool(body.get("ignored", True))}))


@router.post("/matter/commission")
async def commission(request: Request):
    """Add a Matter device by its setup code: ``{"code", "name"?, "area_id"?}``; a new Wi-Fi device over
    Bluetooth with ``"method": "ble"`` (+ ``"session"`` while it runs, ``"wifi_ssid"``/``"wifi_password"``
    for a network other than the household one)."""
    await _admin(request)
    body = await _json(request)
    if not str(body.get("code") or "").strip():
        raise HTTPException(status_code=400, detail="Enter the device's Matter setup code")
    keys = ("code", "name", "area_id", "method", "session", "wifi_ssid", "wifi_password")
    payload = {k: body[k] for k in keys if body.get(k)}
    return _answer(*await _hub("POST", "/hub/matter/commission", json=payload, timeout=_ADOPT_TIMEOUT))


@router.get("/matter/settings")
async def matter_settings(request: Request):
    """Whether the Matter Server has Bluetooth, and the household Wi-Fi for new devices (its name, never the password)."""
    await _admin(request)
    return _answer(*await _hub("GET", "/hub/matter/settings"))


@router.put("/matter/wifi")
async def set_matter_wifi(request: Request):
    """Set the household Wi-Fi new Matter devices are given: ``{"ssid", "password"}``."""
    await _admin(request)
    body = await _json(request)
    return _answer(*await _hub("PUT", "/hub/matter/wifi", json={k: body.get(k) for k in ("ssid", "password")}))


@router.delete("/matter/wifi")
async def clear_matter_wifi(request: Request):
    await _admin(request)
    return _answer(*await _hub("DELETE", "/hub/matter/wifi"))


async def _json(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception:
        return {}
    if body is None:
        return {}
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="The body must be a JSON object")
    return body


# -- Integrations: what is set up -------------------------------------------------------------

def _rid(value: str, what: str) -> str:
    if not _REGISTRY_ID.match(value or ""):
        raise HTTPException(status_code=404, detail=f"No such {what}")
    return value


def _force(force: bool) -> dict[str, str] | None:
    return {"force": "1"} if force else None


@router.get("/entries")
async def list_entries(request: Request):
    """Every integration entry with its devices, entities and reconfigurable fields (never secrets)."""
    await _admin(request)
    return _answer(*await _hub("GET", "/hub/entries"))


@router.patch("/entries/{entry_id}")
async def reconfigure_entry(entry_id: str, request: Request):
    """Change an entry's name, address or login: ``{"title"?, "data"?: {...}}``."""
    await _admin(request)
    body = await _json(request)
    payload = {k: body[k] for k in ("title", "data") if k in body}
    return _answer(*await _hub("PATCH", f"/hub/entries/{_rid(entry_id, 'integration')}", json=payload,
                               timeout=_SCAN_TIMEOUT))


@router.delete("/entries/{entry_id}")
async def remove_entry(entry_id: str, request: Request, force: bool = False):
    """Remove (and unpair) an integration entry; ``force`` when the device cannot be reached."""
    await _admin(request)
    return _answer(*await _hub("DELETE", f"/hub/entries/{_rid(entry_id, 'integration')}", params=_force(force),
                               timeout=_SCAN_TIMEOUT))


@router.get("/devices/duplicates")
async def duplicate_devices(request: Request):
    """Devices two integrations both register (homehub duplicates.py), grouped, with which copy is hidden."""
    await _admin(request)
    return _answer(*await _hub("GET", "/hub/devices/duplicates"))


@router.post("/devices/duplicates/hide")
async def hide_duplicates(request: Request):
    """Hide one integration's copies of duplicate devices: ``{"hide_domain"?, "keep_domain"?}``."""
    await _admin(request)
    body = await _json(request)
    payload = {k: body[k] for k in ("hide_domain", "keep_domain") if k in body}
    return _answer(*await _hub("POST", "/hub/devices/duplicates/hide", json=payload))


@router.post("/devices/duplicates/unhide")
async def unhide_duplicates(request: Request):
    """Show one integration's hidden copies again: ``{"domain"}``."""
    await _admin(request)
    body = await _json(request)
    payload = {k: body[k] for k in ("domain",) if k in body}
    return _answer(*await _hub("POST", "/hub/devices/duplicates/unhide", json=payload))


# -- archived devices (homehub device_archive.py) ----------------------------------------------

_ARCHIVE_KINDS = ("entry", "device")


def _archive_target(body: dict[str, Any]) -> dict[str, str]:
    kind, item_id = body.get("kind"), body.get("id")
    if kind not in _ARCHIVE_KINDS or not isinstance(item_id, str) or not _REGISTRY_ID.match(item_id):
        raise HTTPException(status_code=400, detail='Say what to act on: {"kind": "entry" or "device", "id"}')
    return {"kind": kind, "id": item_id}


@router.get("/archive")
async def archived_devices(request: Request):
    """Devices the hub archived after going without contact, with the settings and the audit trail."""
    await _admin(request)
    return _answer(*await _hub("GET", "/hub/archive"))


@router.get("/archive/preview")
async def archive_preview(request: Request):
    """The dry run: what the hub would archive and delete next. Changes nothing."""
    await _admin(request)
    return _answer(*await _hub("GET", "/hub/archive/preview"))


@router.put("/archive/settings")
async def archive_settings(request: Request):
    """``{"enabled"?, "archive_after_days"?, "delete_after_days"?}``."""
    await _admin(request)
    body = await _json(request)
    payload = {k: body[k] for k in ("enabled", "archive_after_days", "delete_after_days") if k in body}
    return _answer(*await _hub("PUT", "/hub/archive/settings", json=payload))


@router.post("/archive/restore")
async def archive_restore(request: Request):
    """``{"kind", "id"}``: bring an archived device back as it was."""
    await _admin(request)
    return _answer(*await _hub("POST", "/hub/archive/restore", json=_archive_target(await _json(request))))


@router.post("/archive/delete")
async def archive_delete(request: Request):
    """``{"kind", "id"}``: delete one archived device now, with its history and stored logins."""
    await _admin(request)
    return _answer(*await _hub("POST", "/hub/archive/delete", json=_archive_target(await _json(request)),
                               timeout=_SCAN_TIMEOUT))


#: Combining duplicates into one device with a main and a fallback (homehub duplicates.py): action -> fields.
_COMBINE_FIELDS = {
    "combine": ("main", "fallback", "group", "main_domain", "fallback_domain"),
    "uncombine": ("device",),
    "swap": ("device",),
    "rule": ("main_domain", "fallback_domain", "clear"),
}


@router.post("/devices/duplicates/{action}")
async def combine_duplicates(action: str, request: Request):
    """Combine two copies (``combine``), undo it (``uncombine``), swap main and fallback (``swap``) or set the
    household's main/fallback rule for two integrations (``rule``). Only each action's own fields go through."""
    fields = _COMBINE_FIELDS.get(action)
    if fields is None:
        raise HTTPException(status_code=404, detail="Not found")
    await _admin(request)
    body = await _json(request)
    payload = {k: body[k] for k in fields if k in body}
    return _answer(*await _hub("POST", f"/hub/devices/duplicates/{action}", json=payload))


@router.patch("/devices/{device_id}")
async def update_device(device_id: str, request: Request):
    """Rename, move, label or hide a device: ``{"name_by_user"?, "area_id"?, "labels"?, "hidden"?}``.

    Hidden keeps it working; only what people and Jarvis browse leaves it out (homehub duplicates.py).
    """
    await _admin(request)
    body = await _json(request)
    payload = {k: body[k] for k in ("name_by_user", "area_id", "labels", "hidden") if k in body}
    return _answer(*await _hub("PATCH", f"/hub/devices/{_rid(device_id, 'device')}", json=payload))


@router.get("/devices/{device_id}/restarts")
async def device_restarts(device_id: str, request: Request):
    """An ESPHome node's restarts and drops with their likely cause (homehub esphome_restarts.py)."""
    await _admin(request)
    return _answer(*await _hub("GET", f"/hub/devices/{_rid(device_id, 'device')}/restarts"))


@router.patch("/devices/{device_id}/restarts")
async def set_device_restart_logs(device_id: str, request: Request):
    """The level of the node's log lines the hub keeps for its restarts: ``{"log_level"}``."""
    await _admin(request)
    body = await _json(request)
    payload = {k: body[k] for k in ("log_level",) if k in body}
    return _answer(*await _hub("PATCH", f"/hub/devices/{_rid(device_id, 'device')}/restarts", json=payload))


_LOG_LEVEL = re.compile(r"^[a-z_]{3,16}$")
#: A log stream stays open; the hub sends a line at least every 15 s, so a minute of silence is a dead hub.
_LOG_STREAM_TIMEOUT = aiohttp.ClientTimeout(total=None, connect=5, sock_read=60)


def _log_params(since: int | None, level: str | None) -> dict[str, str]:
    params: dict[str, str] = {}
    if since is not None:
        params["since"] = str(max(0, since))
    if level:
        if not _LOG_LEVEL.match(level):
            raise HTTPException(status_code=400, detail="Unknown log level")
        params["level"] = level
    return params


async def _hub_lines(path: str, params: dict[str, str]) -> AsyncIterator[bytes]:
    """The hub's NDJSON stream, line by line. Raises an HTTPException (before the first line) when it refuses."""
    from services.gateway.main import shared_http_client

    headers = {"Authorization": f"Bearer {INTERNAL_SECRET}"}
    try:
        async with shared_http_client() as client, client.get(
            f"{_homehub_base()}{path}", params=params, headers=headers, timeout=_LOG_STREAM_TIMEOUT
        ) as resp:
            if resp.status != 200:
                try:
                    body = await resp.json(content_type=None)
                except Exception:
                    body = None
                detail = str(body.get("detail") or "") if isinstance(body, dict) else ""
                status = resp.status if resp.status in _PASS_THROUGH else 502
                raise HTTPException(status_code=status, detail=detail or "The device hub did not answer")
            async for line in resp.content:
                if line.strip():
                    yield line
    except (TimeoutError, aiohttp.ClientError) as exc:
        log.warning("[discovery] hub log stream %s: %s", path, type(exc).__name__)
        raise HTTPException(status_code=502, detail="The device hub did not answer") from exc


@router.get("/devices/{device_id}/logs")
async def device_logs(device_id: str, request: Request, since: int | None = None, level: str | None = None,
                      limit: int = 500):
    """An ESPHome node's kept log lines and markers (homehub esphome_logs.py), oldest first."""
    await _admin(request)
    params = {**_log_params(since, level), "limit": str(max(0, min(limit, 2000)))}
    return _answer(*await _hub("GET", f"/hub/devices/{_rid(device_id, 'device')}/logs", params=params))


@router.get("/devices/{device_id}/logs/stream")
async def device_log_stream(device_id: str, request: Request, since: int | None = None, level: str | None = None):
    """NDJSON: the node's kept lines after ``since``, then each new one while the reader stays. While it is open
    the hub asks the node for at least ``level``; it drops back when the last reader leaves."""
    await _admin(request)
    lines = _hub_lines(f"/hub/devices/{_rid(device_id, 'device')}/logs/stream", _log_params(since, level))
    try:
        first = await lines.__anext__()  # the hub's hello, or its refusal as an HTTP status
    except StopAsyncIteration:
        raise HTTPException(status_code=502, detail="The device hub did not answer") from None

    async def relay() -> AsyncIterator[bytes]:
        try:
            yield first
            async for line in lines:
                yield line
        except HTTPException:  # the hub went away mid-stream: end it; the reader reopens from its last line
            return
        finally:
            await lines.aclose()

    return StreamingResponse(relay(), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/devices/{device_id}/logs/dump_config")
async def device_dump_config(device_id: str, request: Request):
    """Ask the node to log its configuration again (409 while it is offline)."""
    await _admin(request)
    return _answer(*await _hub("POST", f"/hub/devices/{_rid(device_id, 'device')}/logs/dump_config"))


@router.delete("/devices/{device_id}")
async def remove_device(device_id: str, request: Request, force: bool = False):
    await _admin(request)
    return _answer(*await _hub("DELETE", f"/hub/devices/{_rid(device_id, 'device')}", params=_force(force),
                               timeout=_SCAN_TIMEOUT))


@router.get("/flows")
async def manual_flows(request: Request):
    """Integrations that can be added by address, with the fields each needs."""
    await _admin(request)
    return _answer(*await _hub("GET", "/hub/flows"))


@router.post("/flows/{domain}")
async def manual_add(domain: str, request: Request):
    """Add a device by address: ``{"host"? | "url"?, "name"?, "area_id"?, "input"?}``; answers like adopt."""
    await _admin(request)
    if not _DOMAIN.match(domain or ""):
        raise HTTPException(status_code=404, detail="No such integration")
    body = await _json(request)
    payload = {k: body[k] for k in ("host", "url", "name", "area_id", "input") if k in body}
    return _answer(*await _hub("POST", f"/hub/flows/{domain}", json=payload, timeout=_ADOPT_TIMEOUT))


@router.get("/esphome-builder")
async def esphome_builder(request: Request):
    """Where admins open the ESPHome Device Builder: ``{"configured", "url"?, "state"?}`` (never its login)."""
    await _admin(request)
    status, body = await _hub("GET", "/hub/esphome-builder")
    if status != 200 or not isinstance(body, dict):
        return _answer(status, body)
    return {k: body[k] for k in ("configured", "url", "state", "reason", "title", "entry_id", "login") if k in body}


# -- The hardware list: integrations, devices, entities (homehub's hardware_api.py) ------------

_ENTITY_ID = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")


def _flag(body: dict[str, Any]) -> dict[str, bool]:
    if not isinstance(body.get("disabled"), bool):
        raise HTTPException(status_code=400, detail="disabled must be true or false")
    return {"disabled": body["disabled"]}


@router.get("/integrations")
async def integrations(request: Request):
    """One row per integration: entries with state and error, counts, how it is discovered and added."""
    await _admin(request)
    return _answer(*await _hub("GET", "/hub/integrations"))


@router.get("/hardware")
async def hardware(request: Request):
    """Every device in the hub's registry with its connections, availability and abilities."""
    await _admin(request)
    return _answer(*await _hub("GET", "/hub/hardware"))


@router.get("/hardware/{device_id}")
async def hardware_device(device_id: str, request: Request):
    """One device with its entities, their states and capabilities."""
    await _admin(request)
    return _answer(*await _hub("GET", f"/hub/hardware/{_rid(device_id, 'device')}"))


@router.post("/hardware/{device_id}/disable")
async def disable_device(device_id: str, request: Request):
    await _admin(request)
    body = _flag(await _json(request))
    return _answer(*await _hub("POST", f"/hub/hardware/{_rid(device_id, 'device')}/disable", json=body,
                               timeout=_SCAN_TIMEOUT))


@router.patch("/entities/{entity_id}")
async def update_entity(entity_id: str, request: Request):
    """Rename, move, hide or disable an entity, or turn a camera's picture:
    ``{"name"?, "area_id"?, "hidden"?, "disabled"?, "rotation"?, "labels"?}``."""
    await _admin(request)
    if not _ENTITY_ID.match(entity_id or ""):
        raise HTTPException(status_code=404, detail="No such entity")
    body = await _json(request)
    payload = {k: body[k] for k in ("name", "area_id", "hidden", "disabled", "rotation", "labels") if k in body}
    return _answer(*await _hub("PATCH", f"/hub/entities/{entity_id}", json=payload, timeout=_SCAN_TIMEOUT))


@router.post("/entries/{entry_id}/disable")
async def disable_entry(entry_id: str, request: Request):
    await _admin(request)
    body = _flag(await _json(request))
    return _answer(*await _hub("POST", f"/hub/entries/{_rid(entry_id, 'integration')}/disable", json=body,
                               timeout=_SCAN_TIMEOUT))


@router.post("/entries/{entry_id}/reload")
async def reload_entry(entry_id: str, request: Request):
    await _admin(request)
    return _answer(*await _hub("POST", f"/hub/entries/{_rid(entry_id, 'integration')}/reload"))


@router.post("/entries/{entry_id}/sync")
async def sync_entry(entry_id: str, request: Request):
    """Register the devices a connected integration found that are not in the registry yet."""
    await _admin(request)
    return _answer(*await _hub("POST", f"/hub/entries/{_rid(entry_id, 'integration')}/sync", timeout=_SCAN_TIMEOUT))


@router.post("/discovered/{finding_id}/update-address")
async def update_address(finding_id: str, request: Request):
    """A set-up device found at a new address: move its entry there."""
    await _admin(request)
    return _answer(*await _hub("POST", f"/hub/discovered/{_finding(finding_id)}/update-address",
                               timeout=_SCAN_TIMEOUT))


@router.get("/capabilities")
async def capabilities(request: Request):
    """The hub's capability index (which devices can do what); filters as the hub's."""
    await _admin(request)
    allowed = ("ability", "area", "domain", "device_id", "platform", "available")
    params = [(k, v) for k, v in request.query_params.multi_items() if k in allowed]
    return _answer(*await _hub("GET", "/api/homehub/capabilities", params=params or None))


# -- Tasmota templates (homehub tasmota_templates.py) -----------------------------------------------

#: Loading a template restarts the device; the hub waits up to 30 s for it and reads it back.
_TEMPLATE_TIMEOUT = aiohttp.ClientTimeout(total=120, connect=5)
_TEMPLATE_KIND = re.compile(r"^[a-z]{3,10}$")


@router.get("/tasmota/templates")
async def tasmota_templates(request: Request, kind: str | None = None, q: str | None = None):
    """The catalog of Tasmota templates, by kind and words."""
    await _admin(request)
    params: dict[str, str] = {}
    if kind:
        if not _TEMPLATE_KIND.match(kind):
            raise HTTPException(status_code=400, detail="Unknown kind")
        params["kind"] = kind
    if q:
        params["q"] = q[:100]
    return _answer(*await _hub("GET", "/hub/tasmota/templates", params=params or None))


@router.get("/tasmota/{entry_id}/template")
async def tasmota_template(entry_id: str, request: Request):
    """A Tasmota device's template now, the catalog entries it equals, and up to three suggestions."""
    await _admin(request)
    return _answer(*await _hub("GET", f"/hub/tasmota/{_rid(entry_id, 'integration')}/template",
                               timeout=_SCAN_TIMEOUT))


@router.post("/tasmota/{entry_id}/template")
async def tasmota_apply_template(entry_id: str, request: Request):
    """Load a template: ``{"template"? or "catalog_id"?, "confirm": true}``; the device restarts."""
    await _admin(request)
    body = await _json(request)
    payload = {k: body[k] for k in ("template", "catalog_id", "confirm") if k in body}
    return _answer(*await _hub("POST", f"/hub/tasmota/{_rid(entry_id, 'integration')}/template", json=payload,
                               timeout=_TEMPLATE_TIMEOUT))


@router.post("/tasmota/{entry_id}/template/restore")
async def tasmota_restore_template(entry_id: str, request: Request):
    """Send back the template the last load replaced: ``{"confirm": true}``."""
    await _admin(request)
    body = await _json(request)
    payload = {k: body[k] for k in ("confirm",) if k in body}
    return _answer(*await _hub("POST", f"/hub/tasmota/{_rid(entry_id, 'integration')}/template/restore",
                               json=payload, timeout=_TEMPLATE_TIMEOUT))

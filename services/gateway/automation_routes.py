"""Automations, schedules and thermostat programs (the Automations and Schedules pages).

Everything is homehub's automation, scheduler and climate API, at the same
paths under ``/api/homehub/`` so the page calls the gateway exactly as it would
call the hub.

Two ways of reaching the hub, chosen per route:

* Reads (lists, an item, its traces, the catalog, schedules, the climate
  overview and settings) go to the caller's own Home Assistant URL with their
  own HA token, as the Cameras page does. Identity redirects that URL to the
  hub, and neither the URL nor the token reaches the browser.
* Writes (create, update, delete, enable, run, import, export, validate, the
  remap, schedule writes, programs, settings) are admins only and go to
  ``HOMEHUB_URL`` with the stack's internal secret, as the New devices panel
  does. A family member's HA token is never what decides whether an
  automation may be changed: the gateway's admin check is.

Because of that split, ``can_edit`` in every read answer is the gateway's
answer (is the caller a Jarvis admin), not the hub's guess from the HA token.

Hold and resume on a thermostat are for anyone who may control that thermostat
(``EntityPermissions.may_control``, the same check every device control path
uses), and go to the hub with the caller's own token.

What a family member sees follows the device permissions
(``services.execution.entity_access``). The rule is deliberately simple:
a thermostat they may not view is not listed, and an automation, schedule or
program is hidden when it names entities and the caller may view none of them.
One that names no entity at all (a script that only sends a notification) is
shown. Admins see everything.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any

import aiohttp
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from services.execution.entity_access import EntityPermissions, can_view, load_entity_permissions
from services.gateway.config import INTERNAL_SECRET

log = logging.getLogger("gateway.automations")

router = APIRouter(prefix="/api/homehub")

HOMEHUB_URL = os.getenv("HOMEHUB_URL", "http://host.docker.internal:8123").rstrip("/")
_TIMEOUT = aiohttp.ClientTimeout(total=30, connect=5)
#: Running a script waits for it to be started, not finished; an import parses a whole file.
_LONG_TIMEOUT = aiohttp.ClientTimeout(total=90, connect=5)
#: Statuses whose hub body is meant for people ("this trigger is not supported", "no such schedule").
_PASS_THROUGH = {200, 400, 403, 404, 409, 422, 501, 503}

#: Ids go into the hub's URL path, so only these shapes pass.
_KIND = re.compile(r"^(automation|script|scene)$")
_ITEM_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,200}$")
_CLIMATE_ID = re.compile(r"^climate\.[a-z0-9_]+$")


# --------------------------------------------------------------------------- callers


async def _caller(request: Request) -> dict[str, Any]:
    """The signed-in caller's identity (with their HA URL and token), or 401."""
    from services.gateway import main as gateway

    creds = gateway._identity_cred_dict(await gateway._resolve_identity_from_request(request))
    if not creds.get("user"):
        raise HTTPException(status_code=401, detail="Authentication required")
    return creds


async def _admin(request: Request) -> str:
    from services.gateway import main as gateway

    return await gateway._require_admin(request)


async def _permissions(creds: dict[str, Any]) -> EntityPermissions | None:
    return await load_entity_permissions(str(creds.get("user") or ""), bool(creds.get("is_admin")))


def _unfiltered(perms: EntityPermissions | None) -> bool:
    # None means Identity could not answer and has no cached answer; like can_view, do not filter then.
    return perms is None or perms.unrestricted


def _hidden(perms: EntityPermissions | None, entity_ids: list[str]) -> bool:
    """True when the thing names entities and the caller may view none of them."""
    if _unfiltered(perms):
        return False
    named = [e for e in entity_ids if e]
    return bool(named) and not any(can_view(perms, e) for e in named)


# --------------------------------------------------------------------------- the hub


async def _hub(method: str, path: str, *, creds: dict[str, Any] | None = None,
               params: dict[str, str] | None = None, json: Any = None,
               timeout: aiohttp.ClientTimeout = _TIMEOUT) -> tuple[int, Any]:
    """One call to the hub; ``(status, body)``.

    ``creds`` set: the caller's own HA URL and token (reads, hold, resume).
    ``creds`` None: ``HOMEHUB_URL`` with the internal secret (admin writes).
    502 when the hub does not answer.
    """
    from services.gateway.main import shared_http_client

    if creds is None:
        base, token = HOMEHUB_URL, INTERNAL_SECRET
    else:
        base, token = str(creds.get("ha_url") or "").rstrip("/"), str(creds.get("ha_token") or "")
        if not base or not token:
            raise HTTPException(status_code=503, detail="Home Assistant is not set up for your account")
    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with shared_http_client() as client, client.request(
            method, f"{base}{path}", params=params, json=json, headers=headers, timeout=timeout
        ) as resp:
            try:
                body = await resp.json(content_type=None)
            except Exception:
                body = None
            return resp.status, body
    except (TimeoutError, aiohttp.ClientError) as exc:
        log.warning("[automations] hub %s %s: %s", method, path, type(exc).__name__)
        raise HTTPException(status_code=502, detail="The device hub did not answer") from exc


def _answer(status: int, body: Any) -> Any:
    if status == 200:
        return body
    if status in _PASS_THROUGH and isinstance(body, dict):
        message = str(body.get("message") or body.get("detail") or "")
        return JSONResponse({**body, "detail": message} if message else body, status_code=status)
    log.warning("[automations] hub answered HTTP %s", status)
    raise HTTPException(status_code=502, detail="The device hub did not answer")


def _ok(status: int, body: Any) -> dict[str, Any] | None:
    """The hub's body when it is a 200 JSON object; None when ``_answer`` should speak instead."""
    return body if status == 200 and isinstance(body, dict) else None


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


def _kind(kind: str) -> str:
    if not _KIND.match(kind or ""):
        raise HTTPException(status_code=404, detail="Unknown kind; use automation, script or scene")
    return kind


def _item_id(item_id: str) -> str:
    if not _ITEM_ID.match(item_id or ""):
        raise HTTPException(status_code=404, detail="No such item")
    return item_id


def _climate_id(entity_id: Any) -> str:
    if not isinstance(entity_id, str) or not _CLIMATE_ID.match(entity_id):
        raise HTTPException(status_code=400, detail="entity_id must be a climate entity")
    return entity_id


def _schedule_entities(schedule: dict[str, Any]) -> list[str]:
    """Every entity a scheduler schedule acts on or checks."""
    out: list[str] = []
    for slot in schedule.get("timeslots") or []:
        if not isinstance(slot, dict):
            continue
        for row in (slot.get("actions") or []) + (slot.get("conditions") or []):
            if isinstance(row, dict) and isinstance(row.get("entity_id"), str):
                out.append(row["entity_id"])
    return out


def _strings(value: Any) -> list[str]:
    return [str(v) for v in value] if isinstance(value, list) else []


# --------------------------------------------------------------------------- automations: fixed paths first

# FastAPI matches in declaration order, so ``/automations/catalog`` and friends
# must come before ``/automations/{kind}/{item_id}``.


@router.get("/automations")
async def list_automations(request: Request):
    """Automations, scripts and scenes the caller may see."""
    creds = await _caller(request)
    status, body = await _hub("GET", "/api/homehub/automations", creds=creds)
    data = _ok(status, body)
    if data is None:
        return _answer(status, body)
    perms = await _permissions(creds)
    items = [i for i in data.get("items") or [] if isinstance(i, dict)
             and not _hidden(perms, _strings(i.get("entities")))]
    return {**data, "items": items, "can_edit": bool(creds.get("is_admin"))}


@router.get("/automations/catalog")
async def catalog(request: Request):
    """Trigger, condition and action types, services and entities, for the editor's pickers."""
    creds = await _caller(request)
    status, body = await _hub("GET", "/api/homehub/automations/catalog", creds=creds)
    data = _ok(status, body)
    if data is None:
        return _answer(status, body)
    perms = await _permissions(creds)
    if not _unfiltered(perms):
        data = {**data, "entities": [e for e in data.get("entities") or [] if isinstance(e, dict)
                                     and can_view(perms, str(e.get("entity_id") or ""))]}
    return data


@router.post("/automations/validate")
async def validate(request: Request):
    await _admin(request)
    return _answer(*await _hub("POST", "/api/homehub/automations/validate", json=await _json(request)))


@router.get("/automations/export")
async def export(request: Request, kind: str = "automation", id: str | None = None):
    await _admin(request)
    params = {"kind": _kind(kind)}
    if id:
        params["id"] = _item_id(id)
    return _answer(*await _hub("GET", "/api/homehub/automations/export", params=params))


@router.post("/automations/import")
async def import_yaml(request: Request):
    await _admin(request)
    body = await _json(request)
    _kind(str(body.get("kind") or ""))
    return _answer(*await _hub("POST", "/api/homehub/automations/import", json=body, timeout=_LONG_TIMEOUT))


@router.get("/automations/remap")
async def remap(request: Request):
    await _admin(request)
    return _answer(*await _hub("GET", "/api/homehub/automations/remap"))


@router.put("/automations/remap")
async def save_remap(request: Request):
    await _admin(request)
    return _answer(*await _hub("PUT", "/api/homehub/automations/remap", json=await _json(request)))


@router.post("/automations/remap/preview")
async def preview_remap(request: Request):
    await _admin(request)
    return _answer(*await _hub("POST", "/api/homehub/automations/remap/preview", json=await _json(request)))


@router.post("/automations/remap/suggest")
async def suggest_remap(request: Request):
    await _admin(request)
    return _answer(*await _hub("POST", "/api/homehub/automations/remap/suggest", timeout=_LONG_TIMEOUT))


# --------------------------------------------------------------------------- automations: one item


async def _visible_item(request: Request, kind: str, item_id: str) -> tuple[dict[str, Any], int, Any]:
    """The caller, plus the hub's answer for the item; 404 when it is hidden from them."""
    creds = await _caller(request)
    status, body = await _hub("GET", f"/api/homehub/automations/{_kind(kind)}/{_item_id(item_id)}", creds=creds)
    data = _ok(status, body)
    if data is not None:
        item = data.get("item") if isinstance(data.get("item"), dict) else {}
        if _hidden(await _permissions(creds), _strings(item.get("entities"))):
            raise HTTPException(status_code=404, detail="No such item")
    return creds, status, body


@router.get("/automations/{kind}/{item_id}")
async def get_item(kind: str, item_id: str, request: Request):
    """One item with its HA config and the same config as YAML."""
    creds, status, body = await _visible_item(request, kind, item_id)
    data = _ok(status, body)
    if data is None:
        return _answer(status, body)
    return {**data, "can_edit": bool(creds.get("is_admin"))}


@router.get("/automations/{kind}/{item_id}/traces")
async def traces(kind: str, item_id: str, request: Request):
    """The item's recent runs, newest first."""
    creds, status, body = await _visible_item(request, kind, item_id)
    if _ok(status, body) is None:
        return _answer(status, body)
    return _answer(*await _hub("GET", f"/api/homehub/automations/{kind}/{item_id}/traces", creds=creds))


@router.post("/automations/{kind}")
async def create_item(kind: str, request: Request):
    await _admin(request)
    return _answer(*await _hub("POST", f"/api/homehub/automations/{_kind(kind)}", json=await _json(request)))


@router.put("/automations/{kind}/{item_id}")
async def update_item(kind: str, item_id: str, request: Request):
    await _admin(request)
    return _answer(*await _hub("PUT", f"/api/homehub/automations/{_kind(kind)}/{_item_id(item_id)}",
                               json=await _json(request)))


@router.delete("/automations/{kind}/{item_id}")
async def delete_item(kind: str, item_id: str, request: Request):
    await _admin(request)
    return _answer(*await _hub("DELETE", f"/api/homehub/automations/{_kind(kind)}/{_item_id(item_id)}"))


@router.post("/automations/automation/{item_id}/enabled")
async def set_enabled(item_id: str, request: Request):
    await _admin(request)
    body = await _json(request)
    return _answer(*await _hub("POST", f"/api/homehub/automations/automation/{_item_id(item_id)}/enabled",
                               json={"enabled": bool(body.get("enabled", True))}))


@router.post("/automations/{kind}/{item_id}/run")
async def run_item(kind: str, item_id: str, request: Request):
    await _admin(request)
    return _answer(*await _hub("POST", f"/api/homehub/automations/{_kind(kind)}/{_item_id(item_id)}/run",
                               json=await _json(request), timeout=_LONG_TIMEOUT))


# --------------------------------------------------------------------------- scheduler


@router.get("/schedules")
async def list_schedules(request: Request):
    creds = await _caller(request)
    status, body = await _hub("GET", "/api/homehub/schedules", creds=creds)
    data = _ok(status, body)
    if data is None:
        return _answer(status, body)
    perms = await _permissions(creds)
    schedules = [s for s in data.get("schedules") or [] if isinstance(s, dict)
                 and not _hidden(perms, _schedule_entities(s))]
    return {**data, "schedules": schedules, "can_edit": bool(creds.get("is_admin"))}


@router.get("/schedules/{schedule_id}")
async def get_schedule(schedule_id: str, request: Request):
    creds = await _caller(request)
    status, body = await _hub("GET", f"/api/homehub/schedules/{_item_id(schedule_id)}", creds=creds)
    data = _ok(status, body)
    if data is not None and _hidden(await _permissions(creds), _schedule_entities(data)):
        raise HTTPException(status_code=404, detail="No such schedule")
    return _answer(status, body)


@router.post("/schedules")
async def create_schedule(request: Request):
    await _admin(request)
    return _answer(*await _hub("POST", "/api/homehub/schedules", json=await _json(request)))


@router.put("/schedules/{schedule_id}")
async def update_schedule(schedule_id: str, request: Request):
    await _admin(request)
    return _answer(*await _hub("PUT", f"/api/homehub/schedules/{_item_id(schedule_id)}", json=await _json(request)))


@router.delete("/schedules/{schedule_id}")
async def delete_schedule(schedule_id: str, request: Request):
    await _admin(request)
    return _answer(*await _hub("DELETE", f"/api/homehub/schedules/{_item_id(schedule_id)}"))


@router.post("/schedules/{schedule_id}/enabled")
async def set_schedule_enabled(schedule_id: str, request: Request):
    await _admin(request)
    body = await _json(request)
    return _answer(*await _hub("POST", f"/api/homehub/schedules/{_item_id(schedule_id)}/enabled",
                               json={"enabled": bool(body.get("enabled", True))}))


# --------------------------------------------------------------------------- climate


@router.get("/climate")
async def climate_overview(request: Request):
    """Thermostats the caller may view, the programs that touch them, away and energy state."""
    creds = await _caller(request)
    status, body = await _hub("GET", "/api/homehub/climate", creds=creds)
    data = _ok(status, body)
    if data is None:
        return _answer(status, body)
    perms = await _permissions(creds)
    if not _unfiltered(perms):
        thermostats = [t for t in data.get("thermostats") or [] if isinstance(t, dict)
                       and can_view(perms, str(t.get("entity_id") or ""))]
        programs = [p for p in data.get("programs") or [] if isinstance(p, dict)
                    and not _hidden(perms, _strings(p.get("entity_ids")))]
        data = {**data, "thermostats": thermostats, "programs": programs}
        energy = data.get("energy")
        if isinstance(energy, dict):
            data["energy"] = {**energy, **{key: [e for e in _strings(energy.get(key)) if can_view(perms, e)]
                                           for key in ("shed", "comforted") if key in energy}}
    return {**data, "can_edit": bool(creds.get("is_admin"))}


@router.get("/climate/settings")
async def climate_settings(request: Request):
    creds = await _caller(request)
    return _answer(*await _hub("GET", "/api/homehub/climate/settings", creds=creds))


@router.put("/climate/settings")
async def save_climate_settings(request: Request):
    await _admin(request)
    return _answer(*await _hub("PUT", "/api/homehub/climate/settings", json=await _json(request)))


@router.post("/climate/programs")
async def create_program(request: Request):
    await _admin(request)
    return _answer(*await _hub("POST", "/api/homehub/climate/programs", json=await _json(request)))


@router.put("/climate/programs/{program_id}")
async def update_program(program_id: str, request: Request):
    await _admin(request)
    return _answer(*await _hub("PUT", f"/api/homehub/climate/programs/{_item_id(program_id)}",
                               json=await _json(request)))


@router.delete("/climate/programs/{program_id}")
async def delete_program(program_id: str, request: Request):
    await _admin(request)
    return _answer(*await _hub("DELETE", f"/api/homehub/climate/programs/{_item_id(program_id)}"))


async def _controllable(request: Request) -> tuple[dict[str, Any], dict[str, Any]]:
    """The caller and the body, once the caller may control the body's thermostat (else 403)."""
    creds = await _caller(request)
    body = await _json(request)
    entity_id = _climate_id(body.get("entity_id"))
    perms = await _permissions(creds)
    # None: Identity is unreachable with nothing cached; verify_entity_access allows in that case too.
    if perms is not None and not perms.may_control(entity_id):
        raise HTTPException(status_code=403, detail="You may not control this thermostat")
    return creds, body


@router.post("/climate/hold")
async def hold(request: Request):
    """Hold a thermostat at a setpoint until the next slot, a time, or for good."""
    creds, body = await _controllable(request)
    return _answer(*await _hub("POST", "/api/homehub/climate/hold", creds=creds, json=body))


@router.post("/climate/resume")
async def resume(request: Request):
    """End a hold; the thermostat follows its program again."""
    creds, body = await _controllable(request)
    return _answer(*await _hub("POST", "/api/homehub/climate/resume", creds=creds,
                               json={"entity_id": body["entity_id"]}))

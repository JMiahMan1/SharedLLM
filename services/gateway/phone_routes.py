"""Devices > Phones: the family's phones as the hub sees them, and the companion-app settings.

Admins only. Everything is homehub's mobile_app admin API
(``services/homehub/mobile_app_api.py``), reached with the stack's internal
secret at ``HOMEHUB_URL``. Phones register themselves (the HA companion app);
this page lists them with their sensors and where each value comes from (the
companion app or Jarvis), maps a phone to its Jarvis user, adds a
Jarvis-only phone, removes a stale one, and edits the settings (the app's
external URL, the push relay allow-list, the Jarvis fallback).
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any

import aiohttp
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from services.gateway.config import INTERNAL_SECRET

log = logging.getLogger("gateway.phones")

router = APIRouter(prefix="/api/homehub/phones")

HOMEHUB_URL = os.getenv("HOMEHUB_URL", "http://host.docker.internal:8123").rstrip("/")
_TIMEOUT = aiohttp.ClientTimeout(total=30, connect=5)
_PASS_THROUGH = {200, 201, 400, 404}
_ENTRY_ID = re.compile(r"^[A-Za-z0-9_-]{6,64}$")
_SETTINGS = ("external_url", "internal_url", "companion_fresh_minutes", "jarvis_fallback", "auto_jarvis_phones",
             "jarvis_phone_name", "push_url_allowlist")


async def _admin(request: Request) -> str:
    from services.gateway import main as gateway

    return await gateway._require_admin(request)


async def _hub(method: str, path: str, *, json: Any = None) -> tuple[int, Any]:
    """One call to the hub; ``(status, body)``. 502 when it does not answer."""
    from services.gateway.main import shared_http_client

    headers = {"Authorization": f"Bearer {INTERNAL_SECRET}"}
    try:
        async with shared_http_client() as client, client.request(
            method, f"{HOMEHUB_URL}{path}", json=json, headers=headers, timeout=_TIMEOUT
        ) as resp:
            try:
                body = await resp.json(content_type=None)
            except Exception:
                body = None
            return resp.status, body
    except (TimeoutError, aiohttp.ClientError) as exc:
        log.warning("[phones] hub %s %s: %s", method, path, type(exc).__name__)
        raise HTTPException(status_code=502, detail="The device hub did not answer") from exc


def _answer(status: int, body: Any) -> Any:
    if status == 200:
        return body
    if status in _PASS_THROUGH and isinstance(body, dict):
        message = str(body.get("message") or body.get("detail") or "")
        return JSONResponse({**body, "detail": message} if message else body, status_code=status)
    log.warning("[phones] hub answered HTTP %s", status)
    raise HTTPException(status_code=502, detail="The device hub did not answer")


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


def _entry(entry_id: str) -> str:
    if not _ENTRY_ID.match(entry_id or ""):
        raise HTTPException(status_code=404, detail="No such phone")
    return entry_id


@router.get("")
async def phones(request: Request):
    """Every phone with its sensors, plus the settings."""
    await _admin(request)
    listing = _answer(*await _hub("GET", "/hub/mobile_app/phones"))
    if not isinstance(listing, dict) or not isinstance(listing.get("phones"), list):
        return listing
    # Each phone's last room-beacon report from the Jarvis app (room_presence.py).
    from services.gateway import room_presence

    users = sorted({str(p.get("jarvis_user") or "").lower() for p in listing["phones"] if p.get("jarvis_user")})
    try:
        beacons = await room_presence.load_beacons(users)
    except Exception as exc:
        log.warning("[phones] beacon reports unavailable: %s", type(exc).__name__)
        return {**listing, "beacons_error": "The phones' room beacon reports could not be read"}
    return {**listing, "phones": room_presence.attach_beacons(listing["phones"], beacons)}


@router.post("")
async def add_phone(request: Request):
    """A phone fed by Jarvis only: ``{"jarvis_user", "device_name"?}``."""
    await _admin(request)
    body = await _json(request)
    if not str(body.get("jarvis_user") or "").strip():
        raise HTTPException(status_code=400, detail="Choose whose phone it is")
    payload = {k: body[k] for k in ("jarvis_user", "device_name") if body.get(k)}
    return _answer(*await _hub("POST", "/hub/mobile_app/phones", json=payload))


@router.get("/settings")
async def settings(request: Request):
    await _admin(request)
    return _answer(*await _hub("GET", "/hub/mobile_app/settings"))


@router.put("/settings")
async def save_settings(request: Request):
    await _admin(request)
    body = await _json(request)
    return _answer(*await _hub("PUT", "/hub/mobile_app/settings",
                               json={k: v for k, v in body.items() if k in _SETTINGS}))


@router.patch("/{entry_id}")
async def update_phone(entry_id: str, request: Request):
    """``{"jarvis_user"?, "jarvis_fallback"?}``."""
    await _admin(request)
    body = await _json(request)
    payload = {k: body[k] for k in ("jarvis_user", "jarvis_fallback") if k in body}
    return _answer(*await _hub("PATCH", f"/hub/mobile_app/phones/{_entry(entry_id)}", json=payload))


@router.delete("/{entry_id}")
async def remove_phone(entry_id: str, request: Request):
    await _admin(request)
    return _answer(*await _hub("DELETE", f"/hub/mobile_app/phones/{_entry(entry_id)}"))

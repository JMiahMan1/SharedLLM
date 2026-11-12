"""Devices > New devices: devices on the LAN that are not set up yet, and adding them.

Admins only. Everything is homehub's discovery API (``services/homehub/discovery_api.py``),
reached with the stack's internal secret at ``HOMEHUB_URL`` (default the hub on
the Docker host, ``http://host.docker.internal:8123``). The secret never leaves
the server; the hub's answers (including "needs input" and "not supported yet")
are passed through with their status.
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

log = logging.getLogger("gateway.discovery")

router = APIRouter(prefix="/api/homehub")

HOMEHUB_URL = os.getenv("HOMEHUB_URL", "http://host.docker.internal:8123").rstrip("/")
_TIMEOUT = aiohttp.ClientTimeout(total=30, connect=5)
#: A scan listens for replies for several seconds; commissioning a Matter device takes minutes.
_SCAN_TIMEOUT = aiohttp.ClientTimeout(total=60, connect=5)
_ADOPT_TIMEOUT = aiohttp.ClientTimeout(total=240, connect=5)
#: Statuses whose hub body is for people ("needs a key", "already set up", "not supported yet").
_PASS_THROUGH = {200, 400, 404, 409, 501, 503}
_FINDING_ID = re.compile(r"^[a-z0-9_.:-]{3,200}$")


async def _admin(request: Request) -> str:
    from services.gateway import main as gateway

    return await gateway._require_admin(request)


async def _hub(method: str, path: str, *, params: dict[str, str] | None = None, json: Any = None,
               timeout: aiohttp.ClientTimeout = _TIMEOUT) -> tuple[int, Any]:
    """One call to the hub; ``(status, body)``. 502 when it does not answer."""
    from services.gateway.main import shared_http_client

    headers = {"Authorization": f"Bearer {INTERNAL_SECRET}"}
    try:
        async with shared_http_client() as client, client.request(
            method, f"{HOMEHUB_URL}{path}", params=params, json=json, headers=headers, timeout=timeout
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
    """Add a Matter device by its setup code: ``{"code", "name"?, "area_id"?}``."""
    await _admin(request)
    body = await _json(request)
    if not str(body.get("code") or "").strip():
        raise HTTPException(status_code=400, detail="Enter the device's Matter setup code")
    payload = {k: body[k] for k in ("code", "name", "area_id") if body.get(k)}
    return _answer(*await _hub("POST", "/hub/matter/commission", json=payload, timeout=_ADOPT_TIMEOUT))


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

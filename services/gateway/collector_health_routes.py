"""Whether a child's phone is still collecting (services/telemetry/collector_health.py).

* ``POST /api/telemetry/device_health`` - the phone, signed in as its owner,
  reports its permissions, which collecting workers are scheduled and how each
  collector's last run went. The person is always the caller, never the body.
* ``GET /api/guardian/collector-health/{username}`` - the parents' Calls &
  messages view: each device's report with what is wrong on it, and the
  server's own check of when calls, texts and screen time last arrived. Only
  someone identity allows the calls and messages (a parent; every admin).
"""
from __future__ import annotations

import logging
import re
from typing import Any

import aiohttp
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from services.common.http import get_client
from services.gateway.config import INTERNAL_SECRET, TELEMETRY_SVC

log = logging.getLogger("gateway.collector_health")

router = APIRouter()

_TIMEOUT = aiohttp.ClientTimeout(total=15)
_USERNAME = re.compile(r"[A-Za-z0-9_.-]{1,64}")
#: The report's keys the gateway passes on; telemetry checks each value again.
_REPORT_KEYS = ("device_id", "device_name", "app_version", "app_build", "signed_in", "permissions",
                "workers", "collectors", "reported_at")


async def telemetry_call(method: str, path: str, **kwargs: Any) -> tuple[int, Any]:
    async with get_client().request(method, f"{TELEMETRY_SVC}{path}", headers={"X-Internal-Secret": INTERNAL_SECRET},
                                    timeout=_TIMEOUT, **kwargs) as resp:
        try:
            body = await resp.json(content_type=None)
        except Exception:
            body = {"detail": (await resp.text())[:300]}
        return resp.status, body


async def _caller(request: Request) -> str:
    from services.gateway.main import _require_authenticated

    return str(await _require_authenticated(request)).strip().lower()


async def _comms_access(request: Request, subject: str) -> dict:
    from services.gateway.main import _raw_access

    return await _raw_access(request, subject)


@router.post("/api/telemetry/device_health")
async def report_device_health(request: Request):
    user = await _caller(request)
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="Send an object")
    payload = {k: body[k] for k in _REPORT_KEYS if k in body}
    payload["user"] = user
    status, kept = await telemetry_call("POST", "/api/telemetry/device_health", json=payload)
    if status == 422:
        return JSONResponse(status_code=422, content=kept if isinstance(kept, dict) else {"detail": str(kept)})
    if status != 200:
        log.warning("[collector-health] report for %s not kept: HTTP %s", user, status)
        raise HTTPException(status_code=502, detail="The health report could not be kept just now")
    return {"status": "SUCCESS"}


@router.get("/api/guardian/collector-health/{username}")
async def guardian_collector_health(request: Request, username: str):
    if not _USERNAME.fullmatch(username or ""):
        raise HTTPException(status_code=422, detail="A username is required")
    access = await _comms_access(request, username)
    if not access.get("comms"):
        raise HTTPException(status_code=403, detail="Only a parent can see calls and messages")
    status, body = await telemetry_call("GET", f"/api/telemetry/device_health/{username.lower()}")
    if status != 200:
        raise HTTPException(status_code=502, detail="Collection health could not be read just now")
    return body

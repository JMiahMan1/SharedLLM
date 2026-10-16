"""Admin > Network: the home router's status, log and usage (docs/NETWORK.md).

Admins only. Reads come from the control plane's router monitor. Saving the
router's login stores it in Identity (the password encrypted) and checks the
connection straight away; the password never comes back out.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import aiohttp
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from services.gateway.config import CONTROL_PLANE_URL, IDENTITY_SVC, INTERNAL_SECRET

router = APIRouter(prefix="/api/network")

_HEADERS = {"X-Internal-Secret": INTERNAL_SECRET}
_TIMEOUT = aiohttp.ClientTimeout(total=30)


async def _admin(request: Request) -> str:
    from services.gateway import main as gateway

    return await gateway._require_admin(request)


async def _call(method: str, url: str, *, params: dict | None = None, json: dict | None = None) -> Any:
    """One internal call; 502 unless the service answered 200."""
    from services.gateway.main import shared_http_client

    async with shared_http_client() as client:
        async with client.request(method, url, params=params, json=json, headers=_HEADERS, timeout=_TIMEOUT) as resp:
            if resp.status != 200:
                raise HTTPException(status_code=502, detail="The network monitor did not answer")
            return await resp.json()


async def _control_plane(method: str, path: str, params: dict | None = None) -> Any:
    return await _call(method, f"{(CONTROL_PLANE_URL or '').rstrip('/')}/api/network{path}", params=params)


@router.get("/status")
async def network_status(request: Request):
    await _admin(request)
    return await _control_plane("GET", "/status")


@router.get("/log")
async def network_log(request: Request, limit: int = 200, level: str | None = None, q: str | None = None):
    await _admin(request)
    params = {"limit": limit, **({"level": level} if level else {}), **({"q": q} if q else {})}
    return await _control_plane("GET", "/log", params)


@router.get("/usage")
async def network_usage(request: Request, hours: int = 24):
    await _admin(request)
    return await _control_plane("GET", "/usage", {"hours": hours})


class RouterSettings(BaseModel):
    url: str
    username: str
    #: Empty keeps the stored password (the form never receives it back).
    password: str = ""


@router.put("/settings")
async def save_router_settings(request: Request, body: RouterSettings):
    await _admin(request)
    url = body.url.strip().rstrip("/")
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise HTTPException(status_code=400, detail="Use the router's https:// address, e.g. https://192.168.2.1")
    if not body.username.strip():
        raise HTTPException(status_code=400, detail="Enter the router username Jarvis signs in with")
    fields = [("router_url", url), ("router_user", body.username.strip())]
    if body.password:
        fields.append(("router_pass", body.password))
    for field, value in fields:
        await _call("POST", f"{IDENTITY_SVC.rstrip('/')}/api/admin/seed-credential",
                    json={"field": field, "value": value})
    return await _control_plane("POST", "/check")


@router.post("/check")
async def check_network(request: Request):
    await _admin(request)
    return await _control_plane("POST", "/check")

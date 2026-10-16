"""Phone push routes (docs/PUSH.md).

* ``GET /api/push/stream``: ntfy's JSON stream for the person's topic plus
  the calling device's UnifiedPush topics, proxied with ``NTFY_TOKEN`` (phones
  never see it).
* ``POST /api/push/up/register``, ``POST /api/push/up/unregister``,
  ``GET /api/push/up``: UnifiedPush registrations, kept in identity.
* ``POST /api/push/action``: a tapped notification button, run through the
  fixed ``PUSH_CALLBACKS`` registry (push_callbacks.py).
* ``GET/PUT /api/users/me/notification-preferences``: the category switches,
  kept in identity.

Every route acts as the person whose API key is presented, as a bearer token
or in ``X-Api-Key``.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

import aiohttp
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from services.config import JARVIS_HOST, NTFY_TOKEN, NTFY_URL
from services.gateway.config import IDENTITY_SVC, INTERNAL_SECRET
from services.gateway.push_callbacks import PUSH_CALLBACKS

log = logging.getLogger("gateway.push")

router = APIRouter()

#: ntfy sends a keepalive every 30 s (ntfy/server.yml). This long without a
#: line means the upstream is gone: the stream ends and the phone reconnects
#: with ``since`` set to the last message it saw.
STREAM_IDLE_SECONDS = 75.0

_DEVICE_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
#: ntfy's ``since``: a message id, a unix time, a duration (10m), all, latest, none.
_SINCE = re.compile(r"^[A-Za-z0-9]{1,32}$")


async def _caller(request: Request) -> dict[str, Any]:
    """The verified person behind the request's API key, or 401.

    A key sent as ``X-Api-Key`` is also set as the bearer token on the
    request, because the gateway helpers callbacks reuse read only that.
    """
    from services.gateway import main as gateway

    auth = request.headers.get("Authorization") or ""
    bearer = auth.startswith("Bearer ")
    key = auth.split(" ", 1)[1].strip() if bearer else (request.headers.get("X-Api-Key") or "").strip()
    ident = await gateway._resolve_strict_identity(key) if key else None
    if not ident:
        raise HTTPException(status_code=401, detail="Authentication required")
    if not bearer:
        request.scope["headers"] = [
            *[(k, v) for k, v in request.scope["headers"] if k != b"authorization"],
            (b"authorization", f"Bearer {key}".encode()),
        ]
        request.__dict__.pop("_headers", None)
    return {"user": str(ident["user"]), "is_admin": bool(ident.get("is_admin"))}


def _device_id(request: Request, body: dict | None = None, required: bool = True) -> str:
    device = (request.headers.get("X-Device-Id") or str((body or {}).get("device_id") or "")).strip()
    if not device and not required:
        return ""
    if not _DEVICE_ID.match(device):
        raise HTTPException(status_code=400, detail="X-Device-Id header is required")
    return device


async def _body(request: Request) -> dict:
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(status_code=422, detail="Body must be JSON") from None
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="Body must be a JSON object")
    return body


async def _identity(method: str, path: str, *, params: dict | None = None, json: dict | None = None,
                    headers: dict | None = None) -> Any:
    """Call identity; its 4xx answers pass through, anything else is a 502."""
    from services.gateway import main as gateway

    try:
        resp = await gateway.get_http_client().request(
            method,
            f"{IDENTITY_SVC}{path}",
            params=params,
            json=json,
            headers=headers or {"X-Internal-Secret": INTERNAL_SECRET},
            timeout=aiohttp.ClientTimeout(total=10.0),
        )
        data = await resp.json(content_type=None)
    except Exception as e:
        log.warning("[push] identity %s %s failed: %s", method, path, e)
        raise HTTPException(status_code=502, detail="Identity is unreachable") from None
    if resp.status >= 400:
        detail = data.get("detail") if isinstance(data, dict) else None
        raise HTTPException(status_code=resp.status if resp.status < 500 else 502,
                            detail=detail or f"Identity answered {resp.status}")
    return data


def _endpoint(request: Request, topic: str) -> str:
    """The public UnifiedPush endpoint for ``topic`` (Caddy: POST /push/up*)."""
    base = (JARVIS_HOST or "").rstrip("/")
    if not base:
        host = request.headers.get("X-Forwarded-Host") or request.headers.get("Host") or "localhost"
        base = f"https://{host}"
    return f"{base}/push/{topic}"


# The stream

async def _open_ntfy_stream(topics: list[str], since: str | None):
    """Open ntfy's JSON stream for ``topics``. Returns (session, response)."""
    session = aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=None, connect=10.0, sock_read=STREAM_IDLE_SECONDS)
    )
    try:
        resp = await session.get(
            f"{NTFY_URL}/{','.join(topics)}/json",
            params={"since": since} if since else None,
            headers={"Authorization": f"Bearer {NTFY_TOKEN}"},
        )
    except Exception as e:
        await session.close()
        log.warning("[push] ntfy is unreachable: %s", e)
        raise HTTPException(status_code=502, detail="Push server is unreachable") from None
    if resp.status != 200:
        text = await resp.text()
        resp.release()
        await session.close()
        log.warning("[push] ntfy refused the stream: HTTP %s %s", resp.status, text[:200])
        raise HTTPException(status_code=502, detail="Push server refused the stream")
    return session, resp


async def _relay(session: aiohttp.ClientSession, resp: aiohttp.ClientResponse):
    """Pass ntfy's lines through unchanged until either side goes away."""
    try:
        async for line in resp.content:
            yield line
    except (asyncio.TimeoutError, aiohttp.ClientError) as e:
        log.info("[push] ntfy stream ended (%s); the phone reconnects with since=", type(e).__name__)
    finally:
        resp.release()
        await session.close()


@router.get("/api/push/stream")
async def push_stream(request: Request, since: str | None = None):
    """Line-delimited JSON (ntfy's ``open``/``keepalive``/``message`` events)
    for the person's topic and this device's UnifiedPush topics. The topic
    list is fixed when the stream opens; reconnect after a register."""
    caller = await _caller(request)
    device = _device_id(request)
    if since and not _SINCE.match(since):
        raise HTTPException(status_code=422, detail="since must be an ntfy message id, time or duration")
    if not NTFY_TOKEN:
        raise HTTPException(status_code=503, detail="Push is not configured on the server")
    user = caller["user"]
    topic = (await _identity("GET", "/api/internal/push/topic", params={"username": user}))["topic"]
    ups = await _identity("GET", "/api/internal/push/up", params={"username": user, "device_id": device})
    topics = [topic, *(r["topic"] for r in ups.get("registrations") or [])]
    session, resp = await _open_ntfy_stream(topics, since)
    return StreamingResponse(
        _relay(session, resp),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# UnifiedPush registrations

@router.post("/api/push/up/register")
async def up_register(request: Request):
    """``{"app", "token"}`` -> ``{"endpoint", "topic"}`` for one app on this device."""
    caller = await _caller(request)
    body = await _body(request)
    reg = await _identity("POST", "/api/internal/push/up/register", json={
        "username": caller["user"],
        "device_id": _device_id(request, body),
        "app": body.get("app"),
        "token": body.get("token"),
    })
    return {"endpoint": _endpoint(request, reg["topic"]), "topic": reg["topic"]}


@router.post("/api/push/up/unregister")
async def up_unregister(request: Request):
    """``{"app", "token"}``: drop that registration. Without ``X-Device-Id``
    (the web settings page) it is found on any of the person's devices."""
    caller = await _caller(request)
    body = await _body(request)
    result = await _identity("POST", "/api/internal/push/up/unregister", json={
        "username": caller["user"],
        "device_id": _device_id(request, body, required=False),
        "app": body.get("app"),
        "token": body.get("token"),
    })
    return {"status": "SUCCESS", "removed": result.get("removed", 0)}


@router.get("/api/push/up")
async def up_list(request: Request):
    """The person's registrations on all their devices; ``this_device`` marks
    the calling one's."""
    caller = await _caller(request)
    device = _device_id(request, required=False)
    data = await _identity("GET", "/api/internal/push/up", params={"username": caller["user"]})
    registrations = [
        {**r, "endpoint": _endpoint(request, r["topic"]), "this_device": r.get("device_id") == device}
        for r in data.get("registrations") or []
    ]
    return {"status": "SUCCESS", "registrations": registrations}


# Notification buttons

@router.post("/api/push/action")
async def push_action(request: Request):
    """``{"callback", "params", "reply"?}`` from a tapped notification button."""
    await _caller(request)
    body = await _body(request)
    name = str(body.get("callback") or "")
    handler = PUSH_CALLBACKS.get(name)
    if handler is None:
        raise HTTPException(status_code=400, detail=f"Unknown callback: {name or '(none)'}")
    params = body.get("params") if body.get("params") is not None else {}
    if not isinstance(params, dict):
        raise HTTPException(status_code=422, detail="params must be an object")
    reply = body.get("reply")
    if reply is not None and not isinstance(reply, str):
        raise HTTPException(status_code=422, detail="reply must be text")
    return await handler(request, params, reply)


# Category switches

async def _preferences(request: Request, method: str, json: dict | None = None) -> JSONResponse:
    await _caller(request)
    data = await _identity(
        method, "/api/users/me/notification-preferences", json=json,
        headers={"Authorization": request.headers["Authorization"]},
    )
    return JSONResponse(content=data)


@router.get("/api/users/me/notification-preferences")
async def get_notification_preferences(request: Request):
    return await _preferences(request, "GET")


@router.put("/api/users/me/notification-preferences")
async def put_notification_preferences(request: Request):
    return await _preferences(request, "PUT", await _body(request))

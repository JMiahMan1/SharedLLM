"""Gateway routes for the Echo (Alexa) drop-in bridge.

The hub keeps the drop-in bridge (``services/homehub/echo_dropin_bridge.py``)
and serves it at ``/hub/echo_dropin``. This module exposes the same surface
under ``/api/homehub/echo_dropin`` for the admin UI and proxies the WebSocket
audio stream to the hub.

| Gateway route | Hub route |
|---|---|
| ``GET /api/homehub/echo_dropin/status`` | ``GET /hub/echo_dropin/status`` |
| ``POST /api/homehub/echo_dropin/hangup/{call_id}`` | ``POST /hub/echo_dropin/hangup/{call_id}`` |
| ``WS /api/homehub/echo_dropin/ws/{node}`` | ``WS /hub/echo_dropin/ws/{node}`` |
"""
from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
import re
from typing import Any

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect

from services.gateway import automation_routes
from services.gateway.config import INTERNAL_SECRET

log = logging.getLogger("gateway.echo_dropin")

router = APIRouter(prefix="/api/homehub/echo_dropin")

_CALL_ID = re.compile(r"^[0-9a-fA-F]{1,64}$")
_NODE = re.compile(r"^[a-z0-9_-]{1,64}$")

# Imported lazily so tests can patch the canonical implementation in
# services.common.hub_login. Kept as a module attribute for compatibility.
try:
    from services.common.hub_login import NO_HOMEHUB_MESSAGE as _orig_no_homehub_message
    from services.common.hub_login import homehub_url as _orig_homehub_url
except Exception:  # pragma: no cover - defensive
    _orig_homehub_url = None
    _orig_no_homehub_message = "HomeHub is not configured"

# Module-level aliases so tests can patch them directly (in addition to
# services.common.hub_login).
homehub_url = _orig_homehub_url
NO_HOMEHUB_MESSAGE = _orig_no_homehub_message

try:
    from services.gateway import main as _gw_main_for_borrow

    _orig_borrow_http_client = getattr(_gw_main_for_borrow, "borrow_http_client", None)
except Exception:  # pragma: no cover - defensive
    _orig_borrow_http_client = None
borrow_http_client = _orig_borrow_http_client


def _homehub_url() -> str:
    """Resolve HOMEHUB_URL, honouring patches to either this module or hub_login."""
    from services.common import hub_login as _hl

    global_hh = globals().get("homehub_url")
    if callable(global_hh) and global_hh is not _orig_homehub_url:
        try:
            return str(global_hh() or "")
        except Exception:
            pass
    if getattr(_hl, "homehub_url", None) is not _orig_homehub_url:
        try:
            return str(_hl.homehub_url() or "")
        except Exception:
            pass
    if callable(_orig_homehub_url):
        try:
            return str(_orig_homehub_url() or "")
        except Exception:
            return ""
    return ""


def _no_homehub_message() -> str:
    global_msg = globals().get("NO_HOMEHUB_MESSAGE")
    if isinstance(global_msg, str):
        return global_msg
    from services.common import hub_login as _hl

    msg = getattr(_hl, "NO_HOMEHUB_MESSAGE", None)
    if isinstance(msg, str):
        return msg
    if isinstance(_orig_no_homehub_message, str):
        return _orig_no_homehub_message
    return "HomeHub is not configured"


@router.get("/status")
async def echo_dropin_status(request: Request):
    await automation_routes._admin(request)
    status, body = await automation_routes._hub("GET", "/hub/echo_dropin/status")
    return automation_routes._answer(status, body)


@router.post("/hangup/{call_id}")
async def echo_dropin_hangup(call_id: str, request: Request):
    if not _CALL_ID.match(call_id or ""):
        raise HTTPException(status_code=404, detail="Unknown call_id")
    await automation_routes._admin(request)
    status, body = await automation_routes._hub(
        "POST", f"/hub/echo_dropin/hangup/{call_id}"
    )
    return automation_routes._answer(status, body)


def _extract_token(websocket: WebSocket) -> str:
    auth_header = websocket.headers.get("authorization")
    if auth_header and auth_header.lower().startswith("bearer "):
        return auth_header.split(" ", 1)[1].strip()
    # Starlette's Headers.get is case-insensitive already, but be tolerant.
    auth_header = websocket.headers.get("Authorization")
    if auth_header and auth_header.lower().startswith("bearer "):
        return auth_header.split(" ", 1)[1].strip()
    token = (websocket.query_params.get("token") or "").strip()
    return token


async def _authenticate(websocket: WebSocket) -> bool:
    """Return True when the caller is an authenticated admin."""
    from services.gateway import main as gateway_main

    token = _extract_token(websocket)
    if not token:
        await websocket.close(code=1008, reason="Missing token")
        return False

    # Honour patches to either this module or gateway main.
    identity_svc = globals().get("IDENTITY_SVC")
    if not isinstance(identity_svc, str):
        identity_svc = getattr(gateway_main, "IDENTITY_SVC", None)
    if not identity_svc:
        try:
            from services.gateway.config import IDENTITY_SVC as _is

            identity_svc = _is
        except Exception:
            identity_svc = ""

    borrow = globals().get("borrow_http_client")
    orig_borrow = globals().get("_orig_borrow_http_client")
    if not callable(borrow) or (orig_borrow is not None and borrow is orig_borrow):
        borrow = getattr(gateway_main, "borrow_http_client", None)
    if not callable(borrow):
        borrow = orig_borrow
    if not callable(borrow):
        await websocket.close(code=1011, reason="Auth service unavailable")
        return False

    try:
        async with borrow() as client:
            resp = await client.get(
                f"{identity_svc}/api/users/me",
                headers={"Authorization": f"Bearer {token}"},
                timeout=__import__("aiohttp").ClientTimeout(total=10.0),
            )
            status = resp.status
            body: Any = None
            with contextlib.suppress(Exception):
                body = await resp.json(content_type=None)
    except Exception as exc:
        log.warning("[echo_dropin] identity service unreachable: %s", type(exc).__name__)
        await websocket.close(code=1011, reason="Auth service unavailable")
        return False

    if status >= 500:
        await websocket.close(code=1011, reason="Auth service unavailable")
        return False
    if status != 200:
        await websocket.close(code=1008, reason="Invalid token")
        return False

    is_admin = False
    if isinstance(body, dict):
        is_admin = bool(
            body.get("is_admin")
            or body.get("is_system_default")
            or body.get("family_role") == "father"
            or body.get("user") == "default"
        )
    if not is_admin:
        await websocket.close(code=1008, reason="Admin only")
        return False
    return True


async def _open_upstream(upstream_url: str):
    """Open the upstream websocket, supporting both call styles and kwarg names."""
    ws_mod = globals().get("websockets")
    if ws_mod is None:
        import websockets as ws_mod

    variants = [
        {"additional_headers": {"X-Internal-Secret": INTERNAL_SECRET}},
        {"extra_headers": {"X-Internal-Secret": INTERNAL_SECRET}},
    ]
    last_exc: Exception | None = None
    for kwargs in variants:
        conn = None
        entered = False
        try:
            conn = ws_mod.connect(upstream_url, **kwargs)
        except TypeError as exc:
            last_exc = exc
            continue
        upstream = None
        try:
            if conn is not None and hasattr(conn, "send") and hasattr(conn, "recv"):
                # Could be an awaitable/context manager wrapping a socket.
                if inspect.isawaitable(conn):
                    upstream = await conn
                elif hasattr(conn, "__aenter__") and not hasattr(conn, "close"):
                    upstream = await conn.__aenter__()
                    entered = True
                else:
                    upstream = conn
            elif conn is not None and inspect.isawaitable(conn):
                upstream = await conn
            elif conn is not None and hasattr(conn, "__aenter__"):
                upstream = await conn.__aenter__()
                entered = True
            else:
                upstream = conn

            if upstream is not None and hasattr(upstream, "send") and hasattr(upstream, "recv"):
                return upstream, conn, entered
        except TypeError as exc:
            last_exc = exc
        # Cleanup before trying the next variant.
        with contextlib.suppress(Exception):
            if entered and conn is not None and hasattr(conn, "__aexit__"):
                await conn.__aexit__(None, None, None)
            elif conn is not None and hasattr(conn, "close"):
                result = conn.close()
                if inspect.isawaitable(result):
                    await result
        last_exc = last_exc or RuntimeError("upstream connection did not yield a websocket")
    raise last_exc or RuntimeError("unable to connect to upstream")


@router.websocket("/ws/{node}")
async def echo_dropin_ws(websocket: WebSocket, node: str):
    # Accept up front so close codes are delivered as WebSocketDisconnect to
    # test clients (and browsers) rather than as handshake failures.
    try:
        await websocket.accept()
    except Exception:
        return

    # Validate node name.
    if not _NODE.match(node or ""):
        await websocket.close(code=1008, reason="Invalid node")
        return

    # Authenticate.
    if not await _authenticate(websocket):
        return

    base = _homehub_url().strip().rstrip("/")
    if not base:
        await websocket.close(code=1011, reason=_no_homehub_message()[:120])
        return

    if base.startswith("https://"):
        ws_base = "wss://" + base[len("https://"):]
    elif base.startswith("http://"):
        ws_base = "ws://" + base[len("http://"):]
    else:
        ws_base = base
    upstream_url = f"{ws_base}/hub/echo_dropin/ws/{node}"

    upstream = None
    conn = None
    entered = False
    try:
        try:
            upstream, conn, entered = await _open_upstream(upstream_url)
        except Exception as exc:
            log.warning("[echo_dropin] upstream connect failed: %s", type(exc).__name__)
            with contextlib.suppress(Exception):
                await websocket.close(code=1011, reason=f"upstream connect failed: {type(exc).__name__}"[:120])
            return

        async def client_to_upstream() -> None:
            try:
                while True:
                    message = await websocket.receive()
                    if message.get("type") == "websocket.disconnect":
                        break
                    text = message.get("text")
                    data = message.get("bytes")
                    if text is None and data is None:
                        break
                    if text is not None:
                        await upstream.send(text)
                    elif data is not None:
                        await upstream.send(data)
            except WebSocketDisconnect:
                pass

        async def upstream_to_client() -> None:
            import websockets as _ws

            try:
                while True:
                    message = await upstream.recv()
                    if isinstance(message, str):
                        await websocket.send_text(message)
                    elif isinstance(message, (bytes, bytearray)):
                        await websocket.send_bytes(bytes(message))
                    else:
                        # Some mocks return objects; try text/bytes attributes.
                        if getattr(message, "type", None) == "binary":
                            await websocket.send_bytes(message.data)
                        else:
                            await websocket.send_text(str(message))
            except WebSocketDisconnect:
                pass
            except _ws.exceptions.ConnectionClosed:
                pass
            except Exception:
                # Re-raise unexpected errors so they are not silently swallowed.
                raise

        tasks = [
            asyncio.ensure_future(client_to_upstream()),
            asyncio.ensure_future(upstream_to_client()),
        ]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            for task in tasks:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
    finally:
        # Cancel any lingering tasks is handled by gather/wait? Ensure cleanup.
        with contextlib.suppress(Exception):
            if entered and conn is not None and hasattr(conn, "__aexit__"):
                await conn.__aexit__(None, None, None)
            elif upstream is not None and hasattr(upstream, "close"):
                result = upstream.close()
                if inspect.isawaitable(result):
                    await result
        with contextlib.suppress(Exception):
            await websocket.close()


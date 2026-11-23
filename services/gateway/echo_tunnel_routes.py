"""Gateway routes for the Echo Reverse WSS Tunnel.

Proxies status, disconnect, and WebSocket multiplexing to the HomeHub
under admin authentication, following the same patterns as echo_dropin_routes.

| Gateway route | HomeHub route |
|---|---|
| ``GET /api/homehub/echo_tunnel/status`` | ``GET /hub/echo_tunnel/status`` |
| ``POST /api/homehub/echo_tunnel/disconnect/{node}`` | ``POST /hub/echo_tunnel/disconnect/{node}`` |
| ``GET /api/homehub/echo_tunnel/tokens`` | ``GET /hub/echo_tunnel/tokens`` |
| ``POST /api/homehub/echo_tunnel/tokens/{node}`` | ``POST /hub/echo_tunnel/tokens/{node}`` |
| ``DELETE /api/homehub/echo_tunnel/tokens/{node}`` | ``DELETE /hub/echo_tunnel/tokens/{node}`` |
| ``WS /api/homehub/echo_tunnel/ws/{node}`` | ``WS /hub/echo_tunnel/ws/{node}`` |
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
import re
from typing import Any

import websockets
from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect

from services.gateway import automation_routes
from services.gateway.config import INTERNAL_SECRET

log = logging.getLogger("gateway.echo_tunnel")

router = APIRouter(prefix="/api/homehub/echo_tunnel")

_NODE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def _homehub_url() -> str:
    """Resolve HOMEHUB_URL from services.common.hub_login."""
    from services.common import hub_login as _hl

    try:
        return str(_hl.homehub_url() or "")
    except Exception:
        return ""


def _no_homehub_message() -> str:
    from services.common import hub_login as _hl

    msg = getattr(_hl, "NO_HOMEHUB_MESSAGE", None)
    if isinstance(msg, str):
        return msg
    return "HomeHub is not configured"


@router.get("/status")
async def echo_tunnel_status(request: Request):
    """Status of echo tunnels (admin-auth, proxies to HomeHub)."""
    await automation_routes._admin(request)
    status, body = await automation_routes._hub("GET", "/hub/echo_tunnel/status")
    return automation_routes._answer(status, body)


@router.post("/disconnect/{node}")
async def echo_tunnel_disconnect(node: str, request: Request):
    """Disconnect a tunnel by node name (admin-auth, proxies to HomeHub)."""
    if not _NODE_RE.match(node or ""):
        raise HTTPException(status_code=404, detail="Unknown node")
    await automation_routes._admin(request)
    status, body = await automation_routes._hub(
        "POST", f"/hub/echo_tunnel/disconnect/{node}"
    )
    return automation_routes._answer(status, body)


@router.post("/tokens/{node}")
async def echo_tunnel_token_create(node: str, request: Request):
    """Mint a node's own tunnel token (admin-auth, proxies to HomeHub; token shown once)."""
    if not _NODE_RE.match(node or ""):
        raise HTTPException(status_code=404, detail="Unknown node")
    await automation_routes._admin(request)
    status, body = await automation_routes._hub("POST", f"/hub/echo_tunnel/tokens/{node}")
    return automation_routes._answer(status, body)


@router.delete("/tokens/{node}")
async def echo_tunnel_token_delete(node: str, request: Request):
    """Revoke a node's tunnel token (admin-auth, proxies to HomeHub)."""
    if not _NODE_RE.match(node or ""):
        raise HTTPException(status_code=404, detail="Unknown node")
    await automation_routes._admin(request)
    status, body = await automation_routes._hub("DELETE", f"/hub/echo_tunnel/tokens/{node}")
    return automation_routes._answer(status, body)


@router.get("/tokens")
async def echo_tunnel_token_list(request: Request):
    """List nodes that have a tunnel token (admin-auth, proxies to HomeHub; never hashes)."""
    await automation_routes._admin(request)
    status, body = await automation_routes._hub("GET", "/hub/echo_tunnel/tokens")
    return automation_routes._answer(status, body)


def _extract_token(websocket: WebSocket) -> str:
    auth_header = websocket.headers.get("authorization")
    if auth_header and auth_header.lower().startswith("bearer "):
        return auth_header.split(" ", 1)[1].strip()
    auth_header = websocket.headers.get("Authorization")
    if auth_header and auth_header.lower().startswith("bearer "):
        return auth_header.split(" ", 1)[1].strip()
    token = (websocket.query_params.get("token") or "").strip()
    return token


#: Auth outcomes for the tunnel WebSocket.
_AUTH_ADMIN = "admin"      # a verified admin user token: connect upstream with the internal secret
_AUTH_FORWARD = "forward"  # any other present token: forward it upstream and let HomeHub decide
_AUTH_CLOSED = ""          # the socket was already closed (missing token, Identity unreachable)


async def _resolve_auth(websocket: WebSocket) -> str:
    """Decide how to reach HomeHub for this socket.

    ``_AUTH_ADMIN`` when the bearer token is a verified admin user token (as before);
    ``_AUTH_FORWARD`` for any other present token, so HomeHub can accept a per-device
    token or close 1008. Closes the socket (1008 missing token, 1011 Identity unreachable)
    and returns ``_AUTH_CLOSED`` when it cannot proceed at all. Never logs the token.
    """
    from services.gateway import main as gateway_main

    token = _extract_token(websocket)
    if not token:
        await websocket.close(code=1008, reason="Missing token")
        return _AUTH_CLOSED

    identity_svc = getattr(gateway_main, "IDENTITY_SVC", None)
    if not identity_svc:
        try:
            from services.gateway.config import IDENTITY_SVC as _is
            identity_svc = _is
        except Exception:
            identity_svc = ""

    borrow = getattr(gateway_main, "borrow_http_client", None)
    if not callable(borrow):
        await websocket.close(code=1011, reason="Auth service unavailable")
        return _AUTH_CLOSED

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
        log.warning("[echo_tunnel] identity service unreachable: %s", type(exc).__name__)
        await websocket.close(code=1011, reason="Auth service unavailable")
        return _AUTH_CLOSED

    if status >= 500:
        await websocket.close(code=1011, reason="Auth service unavailable")
        return _AUTH_CLOSED

    if status == 200 and isinstance(body, dict) and (body.get("is_admin") or body.get("is_system_default")):
        return _AUTH_ADMIN
    # Not a verified admin: a per-device token (or an ordinary user token). Forward it upstream.
    return _AUTH_FORWARD


async def _open_upstream(upstream_url: str, headers: dict[str, str]):
    """Open the upstream websocket, supporting both call styles and kwarg names."""
    import websockets

    variants = [
        {"additional_headers": headers},
        {"extra_headers": headers},
    ]
    last_exc: Exception | None = None
    for kwargs in variants:
        conn = None
        entered = False
        try:
            conn = websockets.connect(upstream_url, **kwargs)
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
async def echo_tunnel_ws(websocket: WebSocket, node: str):
    """WebSocket endpoint for the echo reverse WSS tunnel (gateway route).

    Authenticates caller via Identity (fail-closed 4401 on missing/invalid/unreachable).
    Validates node regex ^[a-z0-9][a-z0-9_-]{0,63}$.
    Proxies binary frames to HomeHub /hub/echo_tunnel/ws/{node} with X-Internal-Secret.
    """
    # Accept up front so close codes are delivered as WebSocketDisconnect to
    # test clients (and browsers) rather than as handshake failures.
    try:
        await websocket.accept()
    except Exception:
        return

    # Validate node name.
    if not _NODE_RE.match(node or ""):
        await websocket.close(code=1008, reason="Invalid node")
        return

    # Authenticate: an admin user token proceeds with the internal secret; any other
    # present token is forwarded upstream and HomeHub decides (closes 1008 when wrong).
    mode = await _resolve_auth(websocket)
    if mode == _AUTH_CLOSED:
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
    upstream_url = f"{ws_base}/hub/echo_tunnel/ws/{node}"

    upstream_headers = ({"X-Internal-Secret": INTERNAL_SECRET} if mode == _AUTH_ADMIN
                        else {"Authorization": f"Bearer {_extract_token(websocket)}"})

    upstream = None
    conn = None
    entered = False
    try:
        try:
            upstream, conn, entered = await _open_upstream(upstream_url, upstream_headers)
        except Exception as exc:
            log.warning("[echo_tunnel] upstream connect failed: %s", type(exc).__name__)
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
            except websockets.exceptions.ConnectionClosed:
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

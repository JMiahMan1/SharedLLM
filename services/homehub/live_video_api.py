"""Real-time camera video (go2rtc) through the hub: playback for viewers, settings for admins.

| Route | Who | What |
|---|---|---|
| ``POST /api/homehub/cameras/{id}/live/webrtc[?quality=]`` | any user | WebRTC offer in (``application/json`` ``{"type":"offer","sdp"}`` or ``application/sdp``), answer out |
| ``GET /api/homehub/cameras/{id}/live/stream.mp4[?quality=]`` | any user | The live view as one fragmented MP4 (a ``<video>`` plays it) |
| ``WS /api/homehub/cameras/{id}/live/ws[?quality=]`` | any user | go2rtc's WebSocket (MSE, or WebRTC signaling), relayed |
| ``GET /hub/live-video`` | admin | The go2rtc integration: server, options, and every camera's streams |
| ``PUT /hub/live-video`` | admin | Create or change it: ``url``, ``username``, ``password``, ``enabled`` and the options |
| ``POST /hub/live-video/sync`` | admin | Re-register every camera now |

The gateway calls the viewer routes with the person's own HA token after its
entity-permission check, so the hub only checks that the token is valid, as
for its other camera routes. go2rtc's API is reached only from here: it has
no auth of its own and would fetch any URL it is given. The WebRTC media flows
straight between the browser and go2rtc's WebRTC port.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import uuid
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urlsplit

import aiohttp
from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response, StreamingResponse

from services.homehub import live_video
from services.homehub.auth import Auth, Principal, bearer
from services.homehub.core import ConfigEntry

log = logging.getLogger("homehub.live_video_api")

#: An SDP offer is a few kilobytes; anything much larger is not one.
MAX_OFFER_BYTES = 64 * 1024
#: The answer waits for the camera to start streaming; a sleeping battery camera takes about 16 s to wake.
_SIGNAL_TIMEOUT = aiohttp.ClientTimeout(total=45, connect=3)
_STREAM_TIMEOUT = aiohttp.ClientTimeout(total=None, connect=3, sock_read=45)
#: What a browser may say on go2rtc's WebSocket. Everything else (publishing, a microphone) is dropped.
WS_CLIENT_TYPES = frozenset({"mse", "mp4", "webrtc", "webrtc/offer", "webrtc/candidate"})
_WS_MAX_MESSAGE = 16 * 1024 * 1024
_CAMERA_ID = re.compile(r"^camera\.[a-z0-9_]+$")
_OFFER_TYPES = ("application/json", "application/sdp")


def build_router(runtime: Any, auth: Auth) -> APIRouter:
    router = APIRouter()
    hub = runtime.hub

    def principal_of(headers: Any) -> Principal | None:
        token = bearer(headers.get("authorization")) or headers.get("x-internal-secret")
        return auth.authenticate(token)

    def user(request: Request) -> Principal:
        p = principal_of(request.headers)
        if p is None:
            raise HTTPException(401, detail="Unauthorized")
        return p

    def admin(request: Request) -> Principal:
        p = user(request)
        if not p.is_admin:
            raise HTTPException(401, detail="Unauthorized")
        return p

    def running() -> live_video.Go2rtcDriver:
        svc = live_video.service(hub)
        if svc is None:
            raise HTTPException(503, detail="Live video (go2rtc) is not running")
        return svc

    async def stream_for(entity_id: str, quality: str | None) -> tuple[live_video.Go2rtcDriver, str]:
        if not _CAMERA_ID.match(entity_id or ""):
            raise HTTPException(404, detail="Entity not found.")
        svc = running()
        try:
            return svc, await svc.ensure(entity_id, quality)
        except LookupError:
            raise HTTPException(404, detail="Entity not found.") from None
        except PermissionError as exc:
            raise HTTPException(409, detail=str(exc) or "No real-time video for this camera") from None

    # -- viewers ----------------------------------------------------------------
    @router.post("/api/homehub/cameras/{entity_id}/live/webrtc")
    async def webrtc(entity_id: str, request: Request, quality: str | None = None) -> Response:
        user(request)
        svc, name = await stream_for(entity_id, quality)
        ctype = (request.headers.get("content-type") or "application/json").split(";")[0].strip().lower()
        if ctype not in _OFFER_TYPES:
            raise HTTPException(415, detail="Send the offer as application/json or application/sdp")
        body = await request.body()
        if not body or len(body) > MAX_OFFER_BYTES:
            raise HTTPException(400, detail="A WebRTC offer is required")
        if ctype == "application/json":
            try:
                offer = json.loads(body)
            except ValueError:
                offer = None
            if not isinstance(offer, dict) or offer.get("type") != "offer" or not isinstance(offer.get("sdp"), str):
                raise HTTPException(400, detail='The offer must be {"type": "offer", "sdp": "..."}')
        try:
            status, answer, answer_type = await svc.client.request(
                "POST", "/api/webrtc", params={"src": name}, data=body, headers={"Content-Type": ctype},
                timeout=_SIGNAL_TIMEOUT)
        except live_video.Go2rtcError as exc:
            raise HTTPException(502, detail=str(exc)) from None
        if status not in (200, 201) or not answer:
            log.warning("%s: go2rtc refused the WebRTC offer (HTTP %s): %s", entity_id, status,
                        live_video.redact(answer.decode(errors="replace")[:200], svc.client.secrets))
            raise HTTPException(502, detail="The camera's video could not start")
        return Response(answer, status_code=status, media_type=(answer_type or ctype).split(";")[0],
                        headers={"Cache-Control": "no-store"})

    @router.get("/api/homehub/cameras/{entity_id}/live/stream.mp4")
    async def stream_mp4(entity_id: str, request: Request, quality: str | None = None) -> Response:
        user(request)
        svc, name = await stream_for(entity_id, quality)
        try:
            resp = await svc.client.session().get(f"{svc.client.url}/api/stream.mp4", params={"src": name},
                                                  timeout=_STREAM_TIMEOUT)
        except (TimeoutError, aiohttp.ClientError) as exc:
            raise HTTPException(502, detail=f"go2rtc did not answer ({type(exc).__name__})") from None
        if resp.status != 200:
            status = resp.status
            resp.release()
            log.warning("%s: go2rtc answered the MP4 stream with HTTP %s", entity_id, status)
            raise HTTPException(502, detail="The camera's video could not start")

        async def body() -> AsyncIterator[bytes]:
            try:
                async for chunk in resp.content.iter_any():
                    if await request.is_disconnected():
                        break  # the last viewer leaving is what lets go2rtc close the camera
                    yield chunk
            except (TimeoutError, aiohttp.ClientError):
                pass
            finally:
                resp.release()

        return StreamingResponse(body(), media_type=resp.headers.get("Content-Type", "video/mp4"),
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    @router.websocket("/api/homehub/cameras/{entity_id}/live/ws")
    async def live_ws(websocket: WebSocket, entity_id: str) -> None:
        if principal_of(websocket.headers) is None:
            await websocket.close(code=4401, reason="Unauthorized")
            return
        try:
            svc, name = await stream_for(entity_id, websocket.query_params.get("quality"))
        except HTTPException as exc:
            await websocket.close(code=4404 if exc.status_code == 404 else 4409, reason=str(exc.detail)[:120])
            return
        await websocket.accept()
        try:
            async with svc.client.session().ws_connect(
                f"{svc.client.url}/api/ws", params={"src": name}, heartbeat=30, max_msg_size=_WS_MAX_MESSAGE,
                timeout=aiohttp.ClientWSTimeout(ws_receive=None, ws_close=5),
            ) as upstream:
                await relay_ws(websocket, upstream)
        except (TimeoutError, aiohttp.ClientError) as exc:
            log.info("%s: go2rtc WebSocket ended: %s", entity_id, type(exc).__name__)
        finally:
            with contextlib.suppress(Exception):
                await websocket.close()

    # -- admins -----------------------------------------------------------------
    def entry_of() -> ConfigEntry | None:
        return next((e for e in hub.config_entries.values() if e.domain == live_video.DOMAIN), None)

    def settings_payload() -> dict[str, Any]:
        entry = entry_of()
        svc = live_video.service(hub)
        status = svc.status() if svc is not None else {"version": None, "error": None, "cameras": {}}
        opts = dict(entry.options) if entry is not None else {}
        cameras = []
        for ent in sorted((e for e in hub.entities.values() if e.domain == "camera" and not e.disabled_by),
                          key=lambda e: e.entity_id):
            st = hub.states.get(ent.entity_id)
            cameras.append({
                "entity_id": ent.entity_id, "platform": ent.platform,
                "name": (st.attributes.get("friendly_name") if st is not None else None) or hub.friendly_name(ent),
                "settings": live_video.camera_options(opts, ent.entity_id),
                **(status["cameras"].get(ent.entity_id) or {"qualities": [], "registered": [], "note": None}),
            })
        data = entry.data if entry is not None else {}
        return {
            "configured": entry is not None,
            "entry_id": entry.entry_id if entry is not None else None,
            "enabled": entry is not None and not entry.disabled_by,
            "state": entry.state if entry is not None else None,
            "reason": live_video.redact(entry.reason, [str(data.get("password") or "")]) if entry is not None
            and entry.reason else None,
            "url": str(data.get("url") or live_video.DEFAULT_URL),
            "username": str(data.get("username") or ""),
            "has_password": bool(data.get("password")),
            "options": {
                "webrtc_host": str(opts.get("webrtc_host") or ""),
                "webrtc_port": int(opts.get("webrtc_port") or live_video.DEFAULT_WEBRTC_PORT),
                "default_quality": opts.get("default_quality") or "sub",
            },
            "server": {"version": status["version"], "error": status["error"]},
            "cameras": cameras,
        }

    @router.get("/hub/live-video")
    async def get_settings(request: Request) -> dict[str, Any]:
        admin(request)
        return settings_payload()

    @router.put("/hub/live-video")
    async def put_settings(request: Request) -> Any:
        admin(request)
        try:
            body = await request.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            raise HTTPException(400, detail="The body must be a JSON object")
        try:
            change = live_video.validate_options(body)
        except ValueError as exc:
            raise HTTPException(400, detail=str(exc)) from None
        entry = entry_of()
        data = dict(entry.data) if entry is not None else {"url": live_video.DEFAULT_URL}
        if "url" in body:
            url = str(body.get("url") or "").strip() or live_video.DEFAULT_URL
            parts = urlsplit(url)
            if parts.scheme not in ("http", "https") or not parts.hostname or parts.username:
                raise HTTPException(400, detail="The go2rtc URL must be http(s)://host:port, with no user name in it")
            data["url"] = url.rstrip("/")
        if "username" in body:
            data["username"] = str(body.get("username") or "")
        if "password" in body and body["password"] is not None:
            data["password"] = str(body["password"])  # an absent or null password keeps the stored one
        if entry is None:
            entry = ConfigEntry(entry_id=uuid.uuid4().hex, domain=live_video.DOMAIN, title="go2rtc", data=data,
                                source="user", unique_id=live_video.DOMAIN)
        else:
            entry.data = data
        entry.options = live_video.merge_options(entry.options or {}, change)
        if "enabled" in body:
            entry.disabled_by = None if body["enabled"] else "user"
        try:
            runtime.store.save_config_entry(entry)
        except RuntimeError as exc:  # no FERNET_KEY: credentials are never stored in the clear
            raise HTTPException(503, detail=str(exc)) from None
        hub.config_entries[entry.entry_id] = entry
        await runtime.stop_entry(entry.entry_id)
        if not entry.disabled_by:
            runtime.start_entry(entry)
            await wait_loaded(entry.entry_id)
        else:
            entry.state, entry.reason = "not_loaded", "turned off"
        return JSONResponse(settings_payload())

    async def wait_loaded(entry_id: str, timeout: float = 20.0) -> None:
        """Answer with the new state when it is known (setup re-registers every camera)."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            entry = hub.config_entries.get(entry_id)
            if entry is None or entry.state != "not_loaded":
                return
            await asyncio.sleep(0.2)

    @router.post("/hub/live-video/sync")
    async def sync_now(request: Request) -> dict[str, Any]:
        admin(request)
        svc = running()
        try:
            await svc.sync()
        except Exception as exc:
            raise HTTPException(502, detail=live_video.redact(exc, svc.client.secrets)) from None
        return settings_payload()

    return router


async def relay_ws(client: WebSocket, upstream: Any) -> None:
    """Pass go2rtc's messages to the browser and the browser's allowed ones to go2rtc, until either closes."""

    async def down() -> None:
        async for msg in upstream:
            if msg.type == aiohttp.WSMsgType.BINARY:
                await client.send_bytes(msg.data)
            elif msg.type == aiohttp.WSMsgType.TEXT:
                await client.send_text(msg.data)
            else:
                break

    async def up() -> None:
        while True:
            msg = await client.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            text = msg.get("text")
            if text is None:
                continue  # browsers have no reason to send go2rtc binary (that would be a microphone)
            try:
                kind = json.loads(text).get("type")
            except (ValueError, AttributeError):
                continue
            if kind in WS_CLIENT_TYPES:
                await upstream.send_str(text)

    tasks = [asyncio.create_task(down()), asyncio.create_task(up())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in tasks:
            t.cancel()
        for t in tasks:
            with contextlib.suppress(asyncio.CancelledError, WebSocketDisconnect, Exception):
                await t
        with contextlib.suppress(Exception):
            await upstream.close()

"""Routes for the HA companion apps and the admin page that manages phones.

| Route | Auth | What |
|---|---|---|
| ``POST /api/webhook/{webhook_id}`` | the webhook id | HA's mobile_app webhook (plain or encrypted) |
| ``POST /api/mobile_app/registrations`` | bearer (an HA user) | Register a phone; answers 201 ``{webhook_id, secret, ...}`` |
| ``GET /hub/mobile_app/phones`` | admin | Every phone with its sensors, their source and freshness |
| ``POST /hub/mobile_app/phones`` | admin | ``{"jarvis_user", "device_name"?}``: a phone fed by Jarvis only |
| ``PATCH /hub/mobile_app/phones/{entry_id}`` | admin | ``{"jarvis_user"?, "jarvis_fallback"?, "app_data"?: {"ble_beacon": {...} or null}}`` |
| ``DELETE /hub/mobile_app/phones/{entry_id}`` | admin | Remove a phone (its webhook then answers 410) |
| ``GET/PUT /hub/mobile_app/settings`` | admin | URLs, freshness window, relay allow-list, Jarvis fallback |

WebSocket: ``mobile_app/push_notification_channel`` and
``mobile_app/push_notification_confirm`` (local push, as HA), for the phone's own user.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from services.homehub.auth import Auth, bearer
from services.homehub.core import HubError
from services.homehub.mobile_app_webhook import MobileAppManager, PushChannel, response_body, spawn

log = logging.getLogger("homehub.mobile_app_api")

#: A phone's batch of sensor updates is a few kilobytes; nothing legitimate is bigger.
MAX_WEBHOOK_BODY = 256 * 1024


def build_router(runtime: Any, auth: Auth) -> APIRouter:
    router = APIRouter()
    mgr = MobileAppManager(runtime)
    runtime.mobile_app = mgr

    def principal(request: Request) -> Any:
        token = bearer(request.headers.get("authorization")) or request.headers.get("x-internal-secret")
        p = auth.authenticate(token)
        if p is None:
            raise HTTPException(401, detail="Unauthorized")
        return p

    def admin(request: Request) -> Any:
        p = principal(request)
        if not p.is_admin:
            raise HTTPException(401, detail="Unauthorized")
        return p

    async def body_of(request: Request) -> dict[str, Any]:
        try:
            body = await request.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            raise HTTPException(400, detail="The body must be a JSON object")
        return body

    @router.post("/api/webhook/{webhook_id}")
    async def webhook(webhook_id: str, request: Request) -> Response:
        raw = await request.body()
        if len(raw) > MAX_WEBHOOK_BODY:
            return Response(status_code=413)
        try:
            result = await mgr.handle_webhook(webhook_id, raw)
        except Exception:
            log.exception("mobile_app webhook failed")
            return JSONResponse({}, status_code=500)
        if result.status == 410:
            return Response(status_code=410)
        return JSONResponse(response_body(result), status_code=result.status)

    @router.post("/api/mobile_app/registrations")
    async def register(request: Request) -> Response:
        p = principal(request)
        users = {u["id"] for u in runtime.store.get_kv("users", []) or []}
        if p.user_id not in users:
            # A phone belongs to an HA user (its notifications, its person); the
            # stack's service secret is not one.
            return JSONResponse({"message": "Registrations need a user's token"}, status_code=403)
        data = await body_of(request)
        try:
            result = await mgr.register(p.user_id, data)
        except HubError as exc:
            return JSONResponse({"message": str(exc)}, status_code=400)
        return JSONResponse(result, status_code=201)

    @router.get("/hub/mobile_app/phones")
    async def phones(request: Request) -> dict[str, Any]:
        admin(request)
        return {"phones": mgr.phones(), "settings": mgr.settings()}

    @router.post("/hub/mobile_app/phones")
    async def add_phone(request: Request) -> Response:
        admin(request)
        body = await body_of(request)
        try:
            entry = await mgr.create_jarvis_phone(str(body.get("jarvis_user") or ""), body.get("device_name") or None)
        except HubError as exc:
            return JSONResponse({"message": str(exc)}, status_code=400)
        return JSONResponse({"entry_id": entry.entry_id, "name": entry.title}, status_code=201)

    @router.patch("/hub/mobile_app/phones/{entry_id}")
    async def patch_phone(entry_id: str, request: Request) -> Response:
        admin(request)
        body = await body_of(request)
        try:
            entry = await mgr.set_phone_options(entry_id, body)
        except HubError as exc:
            return JSONResponse({"message": str(exc)}, status_code=404 if "No such" in str(exc) else 400)
        return JSONResponse({"entry_id": entry.entry_id, "options": entry.options})

    @router.delete("/hub/mobile_app/phones/{entry_id}")
    async def delete_phone(entry_id: str, request: Request) -> Response:
        admin(request)
        try:
            await mgr.delete_phone(entry_id)
        except HubError as exc:
            return JSONResponse({"message": str(exc)}, status_code=404)
        return JSONResponse({"entry_id": entry_id, "deleted": True})

    @router.get("/hub/mobile_app/settings")
    async def get_settings(request: Request) -> dict[str, Any]:
        admin(request)
        return mgr.settings()

    @router.put("/hub/mobile_app/settings")
    async def put_settings(request: Request) -> Response:
        admin(request)
        body = await body_of(request)
        try:
            return JSONResponse(mgr.update_settings(body))
        except (HubError, ValueError, TypeError) as exc:
            return JSONResponse({"message": str(exc)}, status_code=400)

    _register_ws(mgr)
    return router


def _register_ws(mgr: MobileAppManager) -> None:
    from services.homehub import api as hub_api

    def entry_for(conn: Any, webhook_id: str) -> Any:
        """HA's ``_ensure_webhook_access``: the connection's user must own the registration."""
        entry = mgr.entry_for_webhook(str(webhook_id or ""))
        p = conn.principal
        if entry is None or p is None or entry.data.get("user_id") != p.user_id:
            return None
        return entry

    async def channel(conn: Any, mid: int, msg: dict[str, Any]) -> None:
        entry = entry_for(conn, msg.get("webhook_id"))
        if entry is None:
            await conn.error(mid, "unauthorized", "User not linked to this webhook ID")
            return
        wid = entry.data["webhook_id"]
        old = mgr.channels.get(wid)
        if old is not None:
            await old.teardown()

        async def send(data: dict[str, Any]) -> None:
            await conn.send({"id": mid, "type": "event", "event": data})

        def on_teardown() -> None:
            if mgr.channels.get(wid) is ch:
                mgr.channels.pop(wid, None)

        ch = PushChannel(wid, bool(msg.get("support_confirm", False)), send, on_teardown)
        mgr.channels[wid] = ch
        mgr.touch(entry.entry_id)

        def unsubscribe() -> None:
            spawn(ch.teardown())

        conn.subs[mid] = unsubscribe
        await conn.result(mid)

    async def confirm(conn: Any, mid: int, msg: dict[str, Any]) -> None:
        entry = entry_for(conn, msg.get("webhook_id"))
        if entry is None:
            await conn.error(mid, "unauthorized", "User not linked to this webhook ID")
            return
        ch = mgr.channels.get(entry.data["webhook_id"])
        if ch is None or not ch.confirm(str(msg.get("confirm_id") or "")):
            await conn.error(mid, "not_found", "Push notification channel not found")
            return
        await conn.result(mid)

    hub_api.WS_COMMANDS["mobile_app/push_notification_channel"] = channel
    hub_api.WS_COMMANDS["mobile_app/push_notification_confirm"] = confirm

"""Settings for motion recording (``services/homehub/camera_recording.py``), for admins.

| Route | Who | What |
|---|---|---|
| ``GET /hub/camera-recording`` | admins | The account (never its password), the folder, every camera with its settings and status |
| ``PUT /hub/camera-recording`` | admins | Change them; creates the integration the first time |

The body of a ``PUT`` may carry ``account: "household"`` to use Jarvis's own
Nextcloud account (read when the hub started), or ``url``/``username``/
``password`` for another one (an absent or null password keeps the stored one),
``folder``, ``enabled`` and ``cameras`` (see ``camera_recording.validate_options``).
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from services.homehub import camera_recording as rec
from services.homehub.auth import Auth, Principal, bearer
from services.homehub.core import ConfigEntry


def build_router(runtime: Any, auth: Auth) -> APIRouter:
    router = APIRouter()
    hub = runtime.hub

    def admin(request: Request) -> Principal:
        token = bearer(request.headers.get("authorization")) or request.headers.get("x-internal-secret")
        p = auth.authenticate(token)
        if p is None or not p.is_admin:
            raise HTTPException(401, detail="Unauthorized")
        return p

    def entry_of() -> ConfigEntry | None:
        return next((e for e in hub.config_entries.values() if e.domain == rec.DOMAIN), None)

    def camera_rows(opts: dict[str, Any], status: dict[str, Any]) -> list[dict[str, Any]]:
        rows = []
        for ent in sorted((e for e in hub.entities.values() if e.domain == "camera" and not e.disabled_by),
                          key=lambda e: e.entity_id):
            st = hub.states.get(ent.entity_id)
            motion = next((e.entity_id for e in hub.entities.values()
                           if e.platform == rec.DOMAIN and e.unique_id == f"{ent.entity_id}:motion"), None)
            rows.append({
                "entity_id": ent.entity_id, "platform": ent.platform,
                "name": (st.attributes.get("friendly_name") if st is not None else None) or hub.friendly_name(ent),
                "records_itself": rec.records_itself(hub, ent.entity_id),
                "unavailable_reason": rec.ineligible(hub, ent.entity_id),
                "settings": rec.camera_settings(opts, ent.entity_id),
                "motion_entity": motion,
                "status": status.get(ent.entity_id),
            })
        return rows

    def payload() -> dict[str, Any]:
        entry = entry_of()
        svc = rec.service(hub)
        status = svc.status() if svc is not None else {"error": None, "cameras": {}}
        opts = dict(entry.options) if entry is not None else {}
        data = entry.data if entry is not None else {}
        household = hub.household_nextcloud
        reason = entry.reason if entry is not None else None
        if reason and data.get("password"):
            reason = str(reason).replace(str(data["password"]), "***")
        return {
            "configured": entry is not None,
            "enabled": entry is not None and not entry.disabled_by,
            "state": entry.state if entry is not None else None,
            "reason": reason,
            "account": data.get("account") or ("custom" if data else None),
            "household_account": {"url": household["url"], "username": household["username"]}
            if household else None,
            "url": str(data.get("url") or ""),
            "username": str(data.get("username") or ""),
            "has_password": bool(data.get("password")),
            "folder": str(opts.get("folder") or rec.DEFAULT_FOLDER),
            "defaults": rec.DEFAULTS,
            "limits": {k: list(v) for k, v in rec.LIMITS.items()},
            "error": status["error"],
            "cameras": camera_rows(opts, status["cameras"]),
        }

    @router.get("/hub/camera-recording")
    async def get_settings(request: Request) -> dict[str, Any]:
        admin(request)
        return payload()

    @router.put("/hub/camera-recording")
    async def put_settings(request: Request) -> Any:
        admin(request)
        try:
            body = await request.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            raise HTTPException(400, detail="The body must be a JSON object")
        try:
            change = rec.validate_options(body)
        except ValueError as exc:
            raise HTTPException(400, detail=str(exc)) from None
        entry = entry_of()
        data = dict(entry.data) if entry is not None else {}
        account = body.get("account")
        if account == "household":
            household = hub.household_nextcloud
            if not household:
                raise HTTPException(400, detail="Jarvis has no Nextcloud account set (NEXTCLOUD_URL, NEXTCLOUD_USER "
                                                "and NEXTCLOUD_PASS in its settings); enter one instead")
            data = {"account": "household", **household}
        elif account not in (None, "custom"):
            raise HTTPException(400, detail="account must be household or custom")
        else:
            if account == "custom":
                data["account"] = "custom"
            if "url" in body:
                url = str(body.get("url") or "").strip().rstrip("/")
                parts = urlsplit(url)
                if parts.scheme not in ("http", "https") or not parts.hostname or parts.username:
                    raise HTTPException(400, detail="The Nextcloud address must be http(s)://host, with no user name")
                data["url"] = url
            if "username" in body:
                data["username"] = str(body.get("username") or "").strip()
            if "password" in body and body["password"] is not None:
                data["password"] = str(body["password"])
        if body.get("enabled", True) is not False and not all(data.get(k) for k in ("url", "username", "password")):
            raise HTTPException(400, detail="Choose Jarvis's Nextcloud account or enter an address, user and app "
                                            "password")
        if entry is None:
            entry = ConfigEntry(entry_id=uuid.uuid4().hex, domain=rec.DOMAIN, title="Motion recording", data=data,
                                source="user", unique_id=rec.DOMAIN)
        else:
            entry.data = data
        entry.options = rec.merge_options(entry.options or {}, change)
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
        return JSONResponse(payload())

    async def wait_loaded(entry_id: str, timeout: float = 20.0) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            entry = hub.config_entries.get(entry_id)
            if entry is None or entry.state != "not_loaded":
                return
            await asyncio.sleep(0.2)

    return router

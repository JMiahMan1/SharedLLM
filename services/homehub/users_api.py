"""The hub's users and its login providers, for its admins (Devices > Setup > Users & logins).

| Route | What |
|---|---|
| ``GET /hub/auth/providers`` | Each provider: active, primary, last sync with counts; the hub-login switch |
| ``PUT /hub/auth/providers`` | ``{"primary"?, "local_logins"?}`` (409 when no admin could log in after) |
| ``POST /hub/auth/providers/{type}/sync`` | Sync that provider's users and persons now |
| ``GET /hub/users`` | Every user (no credentials): where they come from, admin, active, a hub password or not |
| ``POST /hub/users`` | ``{"name", "username", "password", "is_admin"?}``: a user of this hub only |
| ``PATCH /hub/users/{id}`` | ``{"name"?, "is_admin"?, "is_active"?, "password"?}``: the hub's own users only |

A provider's users are changed where they live (Jarvis), never here: their rows answer 409. Users are never
deleted (automations, history and the logbook name them); an admin deactivates one instead.
"""
from __future__ import annotations

import logging
import re
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from services.homehub.auth import Auth, bearer
from services.homehub.auth_providers.manager import SettingsError
from services.homehub.auth_providers.sync import is_people_user, login_usernames
from services.homehub.ha_auth import AuthError, normalize_username

log = logging.getLogger("homehub.users_api")

_USERNAME = re.compile(r"^[a-z0-9_.@-]{1,64}$")


def _is_admin(u: dict[str, Any]) -> bool:
    link = u.get("provider")
    if isinstance(link, dict):
        return bool(link.get("is_admin"))
    return bool(u.get("is_owner")) or "system-admin" in (u.get("group_ids") or [])


def user_view(u: dict[str, Any], usernames: dict[str, str], providers: Any) -> dict[str, Any]:
    link = u.get("provider") if isinstance(u.get("provider"), dict) else None
    source = "service" if u.get("service") else "system" if u.get("system_generated") else \
        str(link["type"]) if link else "local"
    managed = bool(link) and providers.is_active(str(link.get("type"))) and not link.get("removed")
    return {
        "id": u.get("id"), "name": u.get("name"), "source": source,
        "username": (link or {}).get("username") or usernames.get(str(u.get("id"))),
        "is_admin": _is_admin(u), "is_owner": bool(u.get("is_owner")), "is_active": u.get("is_active", True),
        "hub_login": str(u.get("id")) in usernames, "editable": source == "local" or (bool(link) and not managed),
        "provider": None if not link else {k: link.get(k) for k in (
            "type", "username", "roles", "avatar", "removed", "disabled", "linked", "synced_at")},
    }


def _local_admins(users: list[dict[str, Any]], usernames: dict[str, str], providers: Any) -> list[str]:
    """Active admins of the hub's own who can log in with a hub password."""
    out = []
    for u in users:
        link = u.get("provider")
        if isinstance(link, dict) and providers.is_active(str(link.get("type"))) and not link.get("removed"):
            continue
        if is_people_user(u) and u.get("is_active", True) and _is_admin(u) and str(u.get("id")) in usernames:
            out.append(str(u.get("id")))
    return out


def build_router(runtime: Any, auth: Auth) -> APIRouter:
    router = APIRouter()

    def admin(request: Request) -> None:
        token = bearer(request.headers.get("authorization")) or request.headers.get("x-internal-secret")
        p = auth.authenticate(token)
        if p is None or not p.is_admin:
            raise HTTPException(401, detail="Unauthorized")

    async def body_of(request: Request) -> dict[str, Any]:
        try:
            body = await request.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            raise HTTPException(400, detail="The body must be a JSON object")
        return body

    def providers() -> Any:
        return runtime.auth_providers

    def users() -> list[dict[str, Any]]:
        return [u for u in runtime.store.get_kv("users", []) or [] if isinstance(u, dict)]

    @router.get("/hub/auth/providers")
    async def get_providers(request: Request) -> dict[str, Any]:
        admin(request)
        return providers().status()

    @router.put("/hub/auth/providers")
    async def put_providers(request: Request) -> Response:
        admin(request)
        try:
            return JSONResponse(providers().update(await body_of(request)))
        except SettingsError as exc:
            return JSONResponse({"message": str(exc)}, status_code=exc.status)

    @router.post("/hub/auth/providers/{kind}/sync")
    async def sync_now(kind: str, request: Request) -> Response:
        admin(request)
        mgr = providers()
        if mgr.provider(kind) is None or kind == "local":
            return JSONResponse({"message": f"{kind} is not a login provider this hub can sync"}, status_code=404)
        if not mgr.is_active(kind):
            return JSONResponse({"message": f"{kind} is not set up: add it in Integrations"}, status_code=409)
        report = await mgr.sync(kind)
        return JSONResponse(report, status_code=200 if report.get("ok") else 502)

    @router.get("/hub/users")
    async def list_users(request: Request) -> dict[str, Any]:
        admin(request)
        names = login_usernames(runtime.ha_auth)
        rows = [user_view(u, names, providers()) for u in users()]
        return {"users": sorted(rows, key=lambda r: (r["source"] in ("service", "system"), str(r["name"]).lower()))}

    @router.post("/hub/users")
    async def create_user(request: Request) -> Response:
        admin(request)
        body = await body_of(request)
        extra = sorted(set(body) - {"name", "username", "password", "is_admin"})
        if extra:
            return JSONResponse({"message": f"A user cannot have {', '.join(extra)}"}, status_code=400)
        name = str(body.get("name") or "").strip()
        username = normalize_username(str(body.get("username") or ""))
        if not name or len(name) > 80:
            return JSONResponse({"message": "A user needs a name (up to 80 characters)"}, status_code=400)
        if not _USERNAME.match(username):
            return JSONResponse({"message": "A username is lower-case letters, digits and . _ - @"}, status_code=400)
        taken = set(login_usernames(runtime.ha_auth).values()) | {
            str(u["provider"].get("username")) for u in users() if isinstance(u.get("provider"), dict)}
        if username in taken:
            return JSONResponse({"message": f"The username {username} is taken (a hub user or a Jarvis account)"},
                                status_code=409)
        uid = uuid.uuid4().hex
        is_admin = body.get("is_admin") is True
        user = {"id": uid, "name": name, "is_owner": False, "is_active": True, "system_generated": False,
                "group_ids": ["system-admin" if is_admin else "system-users"], "local_only": True}
        try:
            runtime.ha_auth.set_password(uid, username, str(body.get("password") or ""))
        except AuthError as exc:
            return JSONResponse({"message": exc.description or exc.code}, status_code=400)
        runtime.store.set_kv("users", [*users(), user])
        runtime.hub.ha_users = {u["id"]: u.get("name") or "" for u in users()}
        log.info("hub user %s added by an admin", uid)
        return JSONResponse(user_view(user, login_usernames(runtime.ha_auth), providers()))

    @router.patch("/hub/users/{user_id}")
    async def update_user(user_id: str, request: Request) -> Response:
        admin(request)
        body = await body_of(request)
        extra = sorted(set(body) - {"name", "is_admin", "is_active", "password"})
        if extra:
            return JSONResponse({"message": f"A user cannot have {', '.join(extra)}"}, status_code=400)
        stored = users()
        user = next((u for u in stored if u.get("id") == user_id), None)
        if user is None:
            return JSONResponse({"message": "No such user"}, status_code=404)
        names = login_usernames(runtime.ha_auth)
        view = user_view(user, names, providers())
        if not view["editable"]:
            where = "Jarvis" if view["source"] == "jarvis_identity" else view["source"]
            return JSONResponse({"message": f"{user.get('name')} comes from {where}; change them there"},
                                status_code=409)
        if "name" in body:
            name = str(body["name"] or "").strip()
            if not name or len(name) > 80:
                return JSONResponse({"message": "A user needs a name (up to 80 characters)"}, status_code=400)
            user["name"] = name
        for key in ("is_admin", "is_active"):
            if key in body and not isinstance(body[key], bool):
                return JSONResponse({"message": f"{key} is true or false"}, status_code=400)
        if "is_admin" in body and not isinstance(user.get("provider"), dict):
            groups = [g for g in user.get("group_ids") or [] if g not in ("system-admin", "system-users")]
            user["group_ids"] = [*groups, "system-admin" if body["is_admin"] else "system-users"]
            if not body["is_admin"]:
                user["is_owner"] = False
        if "is_active" in body:
            user["is_active"] = body["is_active"]
        mgr = providers()
        if not (mgr.external_admins() if not mgr.local_logins() else
                mgr.external_admins() + _local_admins(stored, names, mgr)):
            return JSONResponse({"message": "No admin could log in after that change"}, status_code=409)
        if "password" in body:
            try:
                runtime.ha_auth.set_password(user_id, ha_username(runtime, user_id), str(body["password"] or ""))
            except AuthError as exc:
                return JSONResponse({"message": exc.description or exc.code}, status_code=400)
        runtime.store.set_kv("users", stored)
        runtime.hub.ha_users = {u["id"]: u.get("name") or "" for u in stored}
        cache = getattr(auth, "_cache", None)
        if isinstance(cache, dict):
            cache.clear()
        return JSONResponse(user_view(user, login_usernames(runtime.ha_auth), mgr))

    return router


def ha_username(runtime: Any, user_id: str) -> str | None:
    return runtime.ha_auth.username_of(user_id)

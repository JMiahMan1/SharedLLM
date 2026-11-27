"""Service logins: hub tokens for other services (HA's long-lived access tokens).

A service such as Music Assistant talks to the hub as if it were Home
Assistant, so it needs a login. It gets its own non-admin hub user and a
revocable long-lived token, never a person's token or the stack's
``INTERNAL_SECRET``.

Admin only:

| Route | What |
|---|---|
| ``GET /hub/auth/service_users`` | service users and their tokens (names only, never values) |
| ``POST /hub/auth/long_lived_token`` | ``{"name", "client_name"?, "days"?}``: a new token, shown once |
| ``DELETE /hub/auth/service_users/{user_id}/tokens`` | revoke that user's tokens (``?client_name=`` for one) |
| ``POST /hub/music_assistant/link_ha`` | ``{"hub_url"?}``: point Music Assistant's Home Assistant plugin at the hub with its own login |
"""
from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from services.homehub.auth import Auth, bearer
from services.homehub.ha_auth import AuthError, HAAuth

log = logging.getLogger("homehub.service_logins")

MA_USER = "Music Assistant"
MA_CLIENT = "music_assistant_ha_plugin"
#: Music Assistant's Home Assistant plugin (provider domain), as MA names it.
MA_HASS_DOMAIN = "hass"


def default_hub_url() -> str:
    """Where a service on this host reaches the hub (Music Assistant runs on the host network)."""
    return os.getenv("HOMEHUB_LOCAL_URL") or f"http://127.0.0.1:{os.getenv('HOMEHUB_PORT', '8123')}"


async def link_music_assistant(runtime: Any, ha: HAAuth, hub_url: str) -> dict[str, Any]:
    """Give Music Assistant's HA plugin a fresh hub login and the hub's URL, through MA's own setup flow."""
    hub = runtime.hub
    driver = next((d for d in hub.drivers.values() if getattr(d.entry, "domain", "") == "music_assistant"), None)
    # The driver holds its live connection in ``conn`` (an MAConnection with ``command``).
    driver = getattr(driver, "conn", None) or driver
    if driver is None or not hasattr(driver, "command"):
        raise HTTPException(503, detail="Music Assistant is not connected to the hub")
    providers = await driver.command("config/providers")
    hass = next((p for p in providers or [] if (p.get("domain") if isinstance(p, dict) else None) == MA_HASS_DOMAIN),
                None)
    if hass is None:
        raise HTTPException(404, detail="Music Assistant has no Home Assistant plugin set up")
    instance_id = hass.get("instance_id")

    user_id = ha.ensure_service_user(MA_USER)
    ha.revoke_long_lived(user_id, MA_CLIENT)  # one live login per link: the old one stops working
    token = ha.create_long_lived_token(user_id, MA_CLIENT)

    step = await driver.command("config/providers/reconfigure", instance_id=instance_id)
    if not isinstance(step, dict) or step.get("type") in ("abort", "finish"):
        raise HTTPException(502, detail=f"Music Assistant would not start its setup: {step}")
    result = await driver.command("config/flows/submit", flow_id=step["flow_id"],
                                  values={"url": hub_url, "token": token, "verify_ssl": False})
    errors = (result or {}).get("errors") if isinstance(result, dict) else None
    if errors:
        ha.revoke_long_lived(user_id, MA_CLIENT)
        await driver.command("config/flows/abort", flow_id=step["flow_id"])
        raise HTTPException(502, detail=f"Music Assistant refused the hub: {errors}")
    log.info("linked Music Assistant's Home Assistant plugin (%s) to %s", instance_id, hub_url)
    return {"instance_id": instance_id, "hub_url": hub_url, "user_id": user_id,
            "step": (result or {}).get("type") if isinstance(result, dict) else None}


def build_router(runtime: Any, auth: Auth) -> APIRouter:
    router = APIRouter()
    ha = HAAuth(runtime.store, auth)

    def admin(request: Request) -> None:
        token = bearer(request.headers.get("authorization")) or request.headers.get("x-internal-secret")
        p = auth.authenticate(token)
        if p is None or not p.is_admin:
            raise HTTPException(401, detail="Unauthorized")

    @router.get("/hub/auth/service_users")
    async def service_users(request: Request) -> list[dict[str, Any]]:
        admin(request)
        tokens = ha._blob()["tokens"]
        out = []
        for u in runtime.store.get_kv("users", []) or []:
            if not u.get("service"):
                continue
            out.append({"user_id": u["id"], "name": u.get("name"), "tokens": sorted(
                {m.get("client_name") or "" for m in tokens.values()
                 if m.get("user_id") == u["id"] and m.get("token_type") == "long_lived_access_token"})})
        return out

    @router.post("/hub/auth/long_lived_token")
    async def long_lived_token(request: Request) -> dict[str, Any]:
        admin(request)
        body = await request.json()
        name = str((body or {}).get("name") or "").strip()
        if not name:
            raise HTTPException(400, detail="name is required")
        days = int((body or {}).get("days") or 3650)
        if not 1 <= days <= 3650:
            raise HTTPException(400, detail="days must be 1-3650")
        user_id = ha.ensure_service_user(name)
        try:
            token = ha.create_long_lived_token(user_id, str(body.get("client_name") or name), days)
        except AuthError as exc:
            raise HTTPException(exc.status, detail=exc.description or exc.code) from exc
        return {"user_id": user_id, "token": token}

    @router.delete("/hub/auth/service_users/{user_id}/tokens")
    async def revoke(user_id: str, request: Request, client_name: str | None = None) -> dict[str, int]:
        admin(request)
        return {"revoked": ha.revoke_long_lived(user_id, client_name)}

    @router.post("/hub/music_assistant/link_ha")
    async def link_ha(request: Request) -> dict[str, Any]:
        admin(request)
        raw = await request.body()
        body = await request.json() if raw else {}
        return await link_music_assistant(runtime, ha, str((body or {}).get("hub_url") or default_hub_url()))

    @router.get("/hub/music_assistant/server")
    async def music_assistant_server_info(request: Request) -> dict[str, Any]:
        """Where the household's Music Assistant is, from the hub's entry (never its token)."""
        admin(request)
        from services.homehub import music_assistant_server

        return music_assistant_server.household_server(runtime.hub)

    @router.post("/hub/auth/users")
    async def ensure_person_user(request: Request) -> dict[str, Any]:
        """``{"username", "display_name"?, "password"?, "is_admin"?}``: provision a person hub login.

        Internal-secret only: the response never leaks the password.
        """
        token = request.headers.get("x-internal-secret") or bearer(request.headers.get("authorization"))
        p = auth.authenticate(token)
        if p is None or p.user_id != "internal":
            raise HTTPException(401, detail="Unauthorized")
        body = await request.json() or {}
        try:
            return ha.ensure_person_user(
                str((body or {}).get("username") or ""),
                (body or {}).get("display_name"),
                (body or {}).get("password"),
                bool((body or {}).get("is_admin", False)),
            )
        except AuthError as exc:
            raise HTTPException(exc.status, detail=exc.description or exc.code) from exc

    @router.post("/hub/music_assistant/accounts")
    async def music_assistant_account(request: Request) -> dict[str, Any]:
        """``{"username", "display_name"?, "token_name"?}``: that person's own MA account and a new token.

        Only the stack itself may ask (its internal secret): the answer carries a token.
        """
        token = request.headers.get("x-internal-secret") or bearer(request.headers.get("authorization"))
        p = auth.authenticate(token)
        if p is None or p.user_id != "internal":
            raise HTTPException(401, detail="Unauthorized")
        from services.homehub import music_assistant_server

        body = await request.json()
        try:
            return await music_assistant_server.provision_account(
                runtime.hub, str((body or {}).get("username") or ""), (body or {}).get("display_name"),
                str((body or {}).get("token_name") or "Jarvis"))
        except music_assistant_server.AccountError as exc:
            raise HTTPException(exc.status, detail=str(exc)) from exc

    @router.post("/hub/music_assistant/audiobookshelf")
    async def music_assistant_audiobookshelf(request: Request) -> dict[str, Any]:
        """Provision/sync one person's dedicated Audiobookshelf provider instance and MA provider_filter.

        Internal secret only: the request body may carry the user's Audiobookshelf API token or password.
        """
        token = request.headers.get("x-internal-secret") or bearer(request.headers.get("authorization"))
        p = auth.authenticate(token)
        if p is None or p.user_id != "internal":
            raise HTTPException(401, detail="Unauthorized")
        from services.homehub import music_assistant_server

        body = await request.json() or {}
        try:
            return await music_assistant_server.sync_user_audiobookshelf_provider(
                runtime.hub,
                username=str(body.get("username") or ""),
                display_name=body.get("display_name"),
                abs_url=body.get("abs_url"),
                abs_api_key=body.get("abs_api_key"),
                abs_username=body.get("abs_username"),
                abs_password=body.get("abs_password"),
                verify_ssl=bool(body.get("verify_ssl", True)),
                existing_instance_id=body.get("existing_instance_id"),
                remove_if_unconfigured=bool(body.get("remove_if_unconfigured", False)),
            )
        except music_assistant_server.AccountError as exc:
            raise HTTPException(exc.status, detail=str(exc)) from exc

    def internal(request: Request) -> None:
        """Only the stack itself may ask for these: they name the household's accounts."""
        token = request.headers.get("x-internal-secret") or bearer(request.headers.get("authorization"))
        p = auth.authenticate(token)
        if p is None or p.user_id != "internal":
            raise HTTPException(401, detail="Unauthorized")

    @router.get("/hub/music_assistant/users")
    async def music_assistant_users(request: Request) -> dict[str, Any]:
        from services.homehub import music_assistant_server

        internal(request)
        try:
            return await music_assistant_server.list_accounts(runtime.hub)
        except music_assistant_server.AccountError as exc:
            raise HTTPException(exc.status, detail=str(exc)) from exc

    @router.post("/hub/music_assistant/users/password")
    async def music_assistant_user_password(request: Request) -> dict[str, Any]:
        """``{"username", "password"}``: the password that person signs in to Music Assistant with."""
        from services.homehub import music_assistant_server

        internal(request)
        body = await request.json()
        try:
            return await music_assistant_server.set_password(
                runtime.hub, str((body or {}).get("username") or ""), str((body or {}).get("password") or "")
            )
        except music_assistant_server.AccountError as exc:
            raise HTTPException(exc.status, detail=str(exc)) from exc

    return router

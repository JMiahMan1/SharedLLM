"""Jarvis's identity service as a login provider (``jarvis_identity``).

Users, their names and pictures, who is an admin, per-entity limits and the password check all come from
identity. The hub calls three of identity's internal routes with the stack's ``INTERNAL_SECRET``:

| Route | For |
|---|---|
| ``GET /api/users`` | every account (id, username, display name, avatar, admin flag, family role) |
| ``GET /api/internal/user-device-assignments?username=`` | the entities a non-admin may use (Jarvis's own rules) |
| ``POST /api/internal/verify-password`` | ``{"username", "password"}`` -> ok or not; identity's lockout applies |

Hub admins are identity's admins and its parents (family role ``father`` or ``mother``). The system default
account becomes a hub user (it can log in) but not a person. The address and the secret are the stack's,
resolved once when the hub starts (``main.py``); the config entry holds neither.
"""
from __future__ import annotations

import logging
from typing import Any

import aiohttp

from services.homehub.auth_providers.base import (
    AuthProvider,
    EntityAccess,
    LoginLocked,
    LoginRejected,
    ProviderUnavailable,
    ProviderUser,
)
from services.homehub.core import Driver

log = logging.getLogger("homehub.auth_providers.jarvis_identity")

DOMAIN = "jarvis_identity"
TIMEOUT = aiohttp.ClientTimeout(total=10.0)
#: Identity's family roles whose holders are the hub's admins, with identity's admins.
PARENT_ROLES = ("father", "mother")


def provider_user(row: dict[str, Any]) -> ProviderUser | None:
    """One identity account as a hub user; None for a row without an id or username."""
    if not isinstance(row, dict) or row.get("id") is None or not str(row.get("username") or "").strip():
        return None
    if row.get("is_test_account"):
        # An automated agent's test account is not a member of the household: never a hub user.
        # Identity leaves it out of GET /api/users already; this holds if that ever changes.
        return None
    username = str(row["username"]).strip().lower()
    family = str(row.get("family_role") or "other")
    return ProviderUser(
        key=str(row["id"]), username=username, name=str(row.get("display_name") or "").strip() or username,
        is_admin=bool(row.get("is_admin")) or family in PARENT_ROLES,
        # Identity has no "disabled" flag today; a removed account is what disables a hub user. These are read
        # in case it gains one, so the hub follows without a change here.
        disabled=bool(row.get("disabled")) or row.get("is_active") is False,
        avatar=str(row["avatar"]) if row.get("avatar") else None,
        roles=(family,) if family != "other" else (),
        can_login=row.get("has_password", True) is not False,
        person=not bool(row.get("is_system_default")),
    )


class JarvisIdentityProvider(AuthProvider):
    type = DOMAIN
    label = "Jarvis"
    external = True
    jarvis_usernames = True

    def __init__(self, url: str, secret: str) -> None:
        self.url = url.rstrip("/")
        self.secret = secret

    def _headers(self) -> dict[str, str]:
        return {"X-Internal-Secret": self.secret}

    async def _request(self, method: str, path: str, **kw: Any) -> tuple[int, Any]:
        """Transport (the tests replace it): (status, parsed JSON or None)."""
        try:
            async with aiohttp.ClientSession(timeout=TIMEOUT) as session, \
                    session.request(method, f"{self.url}{path}", headers=self._headers(), **kw) as resp:
                try:
                    body = await resp.json(content_type=None)
                except ValueError:
                    body = None
                return resp.status, body
        except (aiohttp.ClientError, TimeoutError, OSError) as exc:
            raise ProviderUnavailable(f"Jarvis's identity service cannot be reached ({type(exc).__name__})") from None

    async def list_users(self) -> list[ProviderUser]:
        status, body = await self._request("GET", "/api/users")
        if status != 200 or not isinstance(body, list):
            raise ProviderUnavailable(f"Jarvis's identity service answered {status} for its users")
        return [u for u in (provider_user(r) for r in body) if u is not None]

    async def verify_login(self, username: str, password: str) -> str:
        status, body = await self._request("POST", "/api/internal/verify-password",
                                           json={"username": username, "password": password})
        if status == 200 and isinstance(body, dict) and body.get("ok") and body.get("id") is not None:
            return str(body["id"])
        if status in (401, 403, 404):
            raise LoginRejected("Wrong username or password")
        if status == 429:
            raise LoginLocked("Too many sign-in attempts. Wait fifteen minutes, then try again.")
        raise ProviderUnavailable(f"Jarvis's identity service answered {status} to a sign-in")

    async def entity_access(self, user: ProviderUser) -> EntityAccess | None:
        status, body = await self._request("GET", "/api/internal/user-device-assignments",
                                           params={"username": user.username})
        if status != 200 or not isinstance(body, dict):
            raise ProviderUnavailable(f"Jarvis's identity service answered {status} for {user.username}'s devices")

        def ids(key: str) -> frozenset[str]:
            raw = body.get(key)
            return frozenset(str(x) for x in raw if str(x).strip()) if isinstance(raw, list) else frozenset()

        protected = ids("protected_entity_ids")
        return EntityAccess(assigned=ids("device_ids"), protected=protected,
                            permitted=ids("permitted_entity_ids") & protected,
                            all_unprotected=body.get("all_unprotected") is True)

    def describe(self) -> dict[str, Any]:
        return {"url": self.url}


class JarvisIdentityDriver(Driver):
    """The config entry's driver: a sync when it loads, then one every ``poll_interval`` seconds.

    A sync that cannot reach identity fails the setup (the entry shows "retrying"); the users already synced
    are kept and their sessions keep working meanwhile.
    """

    platform = DOMAIN

    def __init__(self, hub: Any, entry: Any) -> None:
        super().__init__(hub, entry)
        mgr = getattr(hub, "auth_providers", None)
        self.poll_interval = float(getattr(mgr, "sync_every_s", 60.0) or 60.0)

    def _manager(self) -> Any:
        mgr = getattr(self.hub, "auth_providers", None)
        if mgr is None:
            raise RuntimeError("the hub's login providers are not running")
        return mgr

    async def async_setup(self) -> None:
        mgr = self._manager()
        if mgr.provider(DOMAIN) is None:
            raise RuntimeError("IDENTITY_SVC_URL and INTERNAL_SECRET are not set for the hub")
        report = await mgr.sync(DOMAIN)
        if not report.get("ok"):
            raise RuntimeError(report.get("error") or "the sync failed")

    async def async_poll(self) -> None:
        await self._manager().sync(DOMAIN)

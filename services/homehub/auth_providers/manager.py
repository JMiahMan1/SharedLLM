"""Which login providers a hub uses, which is primary, and where each login goes.

Settings (store kv ``auth_providers``, changed by an admin through ``/hub/auth/providers``)::

    {"primary": "local" | <provider type>, "local_logins": bool,
     "auto_added": [<provider types added once at startup>], "last_sync": {<type>: <report>}}

* ``local`` is always active. An external provider is active while its config entry exists and is enabled
  (Devices > Setup > Integrations); its users are mirrored by :mod:`.sync` when the entry loads and then every
  ``sync_every_s`` seconds, or at once with "Sync now".
* ``primary`` decides a username that two active providers both know, and is the provider whose accounts the
  login page asks for. A primary that is not active counts as ``local``.
* ``local_logins`` off refuses logins with hub passwords. It can only be turned off while an active external
  provider has an admin who can log in, and it counts as on whenever no external provider is active, so a
  hub can never be left with no way in (the stack's ``INTERNAL_SECRET`` also always works).

Adding a provider type: subclass :class:`~.base.AuthProvider`, add a factory to ``PROVIDER_TYPES`` and a config
entry domain (driver + manual flow, as ``jarvis_identity`` does). Nothing in auth, ha_auth or persons changes.
An OpenID Connect provider would plug in here; its ``verify_login`` would be replaced by a redirect flow in
``ha_auth_api`` (a provider attribute), which is the one place that would grow.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from services.homehub.auth_providers import sync as sync_mod
from services.homehub.auth_providers.base import (
    AuthProvider,
    LoginLocked,
    LoginRejected,
    ProviderUnavailable,
)
from services.homehub.auth_providers.jarvis_identity import DOMAIN as JARVIS
from services.homehub.auth_providers.jarvis_identity import JarvisIdentityProvider
from services.homehub.auth_providers.local import LocalProvider

log = logging.getLogger("homehub.auth_providers")

KV = "auth_providers"
LOCAL = "local"
#: Provider type -> factory(config) for the external providers this hub can use. ``config`` is what main.py
#: resolved at startup for that type (None: not configured in this stack).
PROVIDER_TYPES: dict[str, Any] = {
    JARVIS: lambda cfg: JarvisIdentityProvider(cfg["url"], cfg["secret"]),
}


class SettingsError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


class LoginFailed(Exception):
    """A refused login: ``code`` is the login form's error (``invalid_auth``, ``cannot_connect``, ``locked``)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ProviderManager:
    def __init__(self, runtime: Any, auth: Any, ha_auth: Any, configs: dict[str, dict[str, Any]] | None = None,
                 *, sync_every_s: float = 60.0) -> None:
        self.runtime = runtime
        self.store = runtime.store
        self.auth = auth
        self.ha_auth = ha_auth
        self.sync_every_s = sync_every_s
        self.providers: dict[str, AuthProvider] = {LOCAL: LocalProvider(ha_auth)}
        for kind, cfg in (configs or {}).items():
            if cfg and kind in PROVIDER_TYPES:
                self.providers[kind] = PROVIDER_TYPES[kind](cfg)
        self._locks: dict[str, asyncio.Lock] = {}
        runtime.hub.auth_providers = self  # the providers' drivers find it here
        runtime.auth_providers = self  # the login flow and the admin API find it here

    # -- settings --------------------------------------------------------------------------
    def settings(self) -> dict[str, Any]:
        raw = self.store.get_kv(KV, {}) or {}
        raw = raw if isinstance(raw, dict) else {}
        return {"primary": raw.get("primary") or LOCAL, "local_logins": raw.get("local_logins", True) is not False,
                "auto_added": list(raw.get("auto_added") or []), "last_sync": dict(raw.get("last_sync") or {})}

    def _save(self, settings: dict[str, Any]) -> None:
        self.store.set_kv(KV, settings)

    def provider(self, kind: str) -> AuthProvider | None:
        return self.providers.get(kind)

    def entry_of(self, kind: str) -> Any:
        return next((e for e in self.runtime.hub.config_entries.values()
                     if e.domain == kind and e.source != "ignore"), None)

    def is_active(self, kind: str) -> bool:
        if kind == LOCAL:
            return True
        entry = self.entry_of(kind)
        return kind in self.providers and entry is not None and not entry.disabled_by

    def active(self) -> list[str]:
        return [k for k in self.providers if self.is_active(k)]

    def primary(self) -> str:
        wanted = self.settings()["primary"]
        return wanted if self.is_active(wanted) else LOCAL

    def local_logins(self) -> bool:
        """Off only while an external provider is active: otherwise there would be no way to log in."""
        if not any(k != LOCAL for k in self.active()):
            return True
        return self.settings()["local_logins"]

    def ensure_entries(self) -> list[str]:
        """Once per provider type: add the entry of a provider the stack configures (it is then on by default).

        An admin who removes it later keeps it removed (``auto_added`` remembers it was done). Returns the types
        added now.
        """
        from services.homehub.auth_providers.jarvis_identity import DOMAIN

        settings = self.settings()
        added = []
        for kind in self.providers:
            if kind == LOCAL or kind in settings["auto_added"]:
                continue
            if self.entry_of(kind) is None:
                entry = new_entry(self.runtime.hub, kind)
                self.store.save_config_entry(entry)
                self.runtime.hub.config_entries[entry.entry_id] = entry
                added.append(kind)
                if kind == DOMAIN and settings["primary"] == LOCAL:
                    settings["primary"] = kind
                log.info("login provider %s added (the stack configures it); primary is %s", kind, settings["primary"])
            settings["auto_added"].append(kind)
        self._save(settings)
        return added

    def update(self, body: dict[str, Any]) -> dict[str, Any]:
        """``{"primary"?, "local_logins"?}`` from an admin."""
        extra = sorted(set(body) - {"primary", "local_logins"})
        if extra:
            raise SettingsError(f"Unknown setting {', '.join(extra)}")
        settings = self.settings()
        if "primary" in body:
            primary = str(body["primary"] or "")
            if primary != LOCAL and not self.is_active(primary):
                raise SettingsError(f"{primary} is not an active login provider", 409)
            settings["primary"] = primary
        if "local_logins" in body:
            if not isinstance(body["local_logins"], bool):
                raise SettingsError("local_logins is true or false")
            if body["local_logins"] is False and not self.external_admins():
                raise SettingsError("No admin could log in: keep the hub's own logins on until an admin from an "
                                    "active login provider can log in.", 409)
            settings["local_logins"] = body["local_logins"]
        self._save(settings)
        return self.status()

    def external_admins(self) -> list[str]:
        """Usernames of active admins from active external providers who can log in."""
        out = []
        for u in self.store.get_kv("users", []) or []:
            link = u.get("provider") if isinstance(u, dict) else None
            if not isinstance(link, dict) or not self.is_active(str(link.get("type"))):
                continue
            if u.get("is_active", True) and link.get("is_admin") and not link.get("removed") \
                    and link.get("can_login", True):
                out.append(str(link.get("username")))
        return out

    # -- sync ------------------------------------------------------------------------------
    async def sync(self, kind: str) -> dict[str, Any]:
        """Mirror one provider's users and persons now; returns (and keeps) the report."""
        provider = self.providers.get(kind)
        if provider is None or not provider.external:
            return {"ok": False, "error": f"{kind} is not an external login provider"}
        lock = self._locks.setdefault(kind, asyncio.Lock())
        async with lock:
            counts = sync_mod.new_counts()
            report: dict[str, Any] = {"ok": True, "at": sync_mod._now(), **counts}
            try:
                users, access, problems = await sync_mod.fetch(provider)
            except ProviderUnavailable as exc:
                report = {**self.settings()["last_sync"].get(kind, {}), "ok": False, "error": str(exc),
                          "failed_at": sync_mod._now()}
                self._remember(kind, report)
                log.warning("%s sync failed, the users synced before are kept: %s", kind, exc)
                return report
            counts["provider_users"] = len(users)
            stored = sync_mod.apply_users(self.store, self.ha_auth, provider, users, access, counts)
            persons_changed = sync_mod.apply_persons(self.runtime, provider, stored, counts)
            report.update(counts)
            if problems:
                report["problems"] = problems
            self._remember(kind, report)
        self.runtime.hub.ha_users = {u["id"]: u.get("name") or "" for u in stored}
        cache = getattr(self.auth, "_cache", None)
        if isinstance(cache, dict):
            cache.clear()  # names, admin rights and limits apply to the next request, not in a minute
        if persons_changed:
            from services.homehub import registries

            await registries._restart_persons(self.runtime)
        changes = {k: v for k, v in counts.items() if v and k not in ("provider_users", "standalone_users")}
        (log.info if changes else log.debug)("%s sync: %d users there; %s; standalone hub users: %s", kind,
                                             len(users), changes or "no changes", counts["standalone_users"] or "none")
        return report

    def _remember(self, kind: str, report: dict[str, Any]) -> None:
        settings = self.settings()
        settings["last_sync"][kind] = report
        self._save(settings)

    # -- logins ----------------------------------------------------------------------------
    def _linked_user(self, kind: str, username: str) -> dict[str, Any] | None:
        for u in self.store.get_kv("users", []) or []:
            link = u.get("provider") if isinstance(u, dict) else None
            if isinstance(link, dict) and link.get("type") == kind and link.get("username") == username \
                    and not link.get("removed"):
                return u
        return None

    def _local_owner(self, username: str) -> str | None:
        """The hub user whose own login has this username, if it is not given to an active provider."""
        users = {u["id"]: u for u in self.store.get_kv("users", []) or [] if isinstance(u, dict)}
        for uid, name in sync_mod.login_usernames(self.ha_auth).items():
            link = (users.get(uid) or {}).get("provider")
            if name == username and not (isinstance(link, dict) and self.is_active(str(link.get("type")))):
                return uid
        return None

    def _order(self) -> list[str]:
        primary = self.primary()
        return [primary, *[k for k in self.active() if k != primary]]

    async def login(self, username: str, password: str) -> dict[str, Any]:
        """The credential (``{"id", "user_id"}``) for a username and password, else :class:`LoginFailed`.

        The hub's own failure limit (per username and overall) applies first; each provider then applies its
        own. A provider that cannot be reached fails the login with ``cannot_connect`` and is not counted as a
        wrong password.
        """
        from services.homehub.ha_auth import normalize_username

        name = normalize_username(username)
        if not name or self.ha_auth._limited(name):
            log.warning("login refused for a while after repeated failures")
            raise LoginFailed("invalid_auth", "Wrong username or password")
        try:
            return await self._login(name, password)
        except LoginRejected:
            self.ha_auth._failures.append((time.monotonic(), name))
            raise LoginFailed("invalid_auth", "Wrong username or password") from None
        except LoginLocked as exc:
            raise LoginFailed("locked", str(exc)) from None
        except ProviderUnavailable as exc:
            log.warning("login for a provider's account failed: %s", exc)
            raise LoginFailed("cannot_connect", "Your Jarvis account cannot be checked right now. Try again in "
                                                "a few minutes.") from None

    async def _login(self, name: str, password: str) -> dict[str, Any]:
        for kind in self._order():
            if kind == LOCAL:
                uid = self._local_owner(name)
                if uid is not None and self.local_logins():
                    return await self._local(name, password, uid)
                continue  # hub logins off: the same refusal as an unknown username, after the same work
            user = self._linked_user(kind, name)
            if user is not None:
                return await self._external(kind, name, password, user)
        # Nobody here has that username yet: an account a provider made since the last sync logs in at once.
        for kind in self._order():
            if kind == LOCAL:
                continue
            try:
                key = await self.providers[kind].verify_login(name, password)
            except LoginRejected:
                continue
            await self.sync(kind)
            user = self._linked_user(kind, name)
            if user is not None and str(user["provider"].get("key")) == key and user.get("is_active", True):
                return {"id": f"{kind}:{key}", "user_id": user["id"]}
        # As long as a real check: the time a refusal takes does not say whether the username exists.
        await asyncio.to_thread(self.ha_auth.check_password, "\x00" + name, password)
        raise LoginRejected("no such user, or a wrong password")

    async def _local(self, name: str, password: str, uid: str) -> dict[str, Any]:
        cred = await asyncio.to_thread(self.ha_auth.check_password, name, password)
        if cred is None or cred.get("user_id") != uid:
            raise LoginRejected("wrong password")
        return cred

    async def _external(self, kind: str, name: str, password: str, user: dict[str, Any]) -> dict[str, Any]:
        if not user.get("is_active", True):
            raise LoginRejected("the account is disabled")
        key = await self.providers[kind].verify_login(name, password)
        if key != str(user["provider"].get("key")):
            raise LoginRejected("the provider answered for another account")
        return {"id": f"{kind}:{key}", "user_id": user["id"]}

    # -- what an admin sees ------------------------------------------------------------------
    def status(self) -> dict[str, Any]:
        settings = self.settings()
        users = [u for u in self.store.get_kv("users", []) or [] if isinstance(u, dict)]
        out = []
        kinds = [LOCAL, *[k for k in PROVIDER_TYPES]]
        for kind in kinds:
            provider = self.providers.get(kind)
            entry = self.entry_of(kind) if kind != LOCAL else None
            mine = [u for u in users if isinstance(u.get("provider"), dict) and u["provider"].get("type") == kind]
            if kind == LOCAL:
                mine = [u for u in users if sync_mod.is_people_user(u) and not isinstance(u.get("provider"), dict)]
            out.append({
                "type": kind, "label": provider.label if provider else _labels().get(kind, kind),
                "external": kind != LOCAL, "configured": provider is not None, "active": self.is_active(kind),
                "primary": self.primary() == kind, "entry_id": getattr(entry, "entry_id", None),
                "state": getattr(entry, "state", None) if entry else ("loaded" if kind == LOCAL else None),
                "reason": getattr(entry, "reason", None) if entry else None,
                "settings": provider.describe() if provider else {},
                "users": sum(1 for u in mine if u.get("is_active", True)),
                "inactive_users": sum(1 for u in mine if not u.get("is_active", True)),
                "last_sync": settings["last_sync"].get(kind),
            })
        return {"providers": out, "primary": self.primary(), "local_logins": self.local_logins(),
                "local_logins_setting": settings["local_logins"], "external_admins": self.external_admins(),
                "standalone_users": sorted(str(u.get("name") or u.get("id")) for u in users
                                           if sync_mod.is_people_user(u) and not isinstance(u.get("provider"), dict))}


def _labels() -> dict[str, str]:
    return {JARVIS: "Jarvis"}


def new_entry(hub: Any, kind: str) -> Any:
    from services.homehub.core import ConfigEntry
    from services.homehub.flows import _new_entry_id

    return ConfigEntry(entry_id=_new_entry_id(), domain=kind, title=_labels().get(kind, kind), data={}, source="user",
                       unique_id=kind)

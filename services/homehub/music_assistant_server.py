"""The household's Music Assistant server: the hub's ``music_assistant`` entry is its one source.

Where Music Assistant runs and the login the hub uses for it live on the hub's
``music_assistant`` config entry and nowhere else: no environment variable, no
built-in address. An admin changes them under Integrations > Music Assistant >
Configure, and the new values are tried against the server before they are
saved (``check_music_assistant``, a ``manual_flows.RECONFIGURE_CHECKS`` entry).

The rest of the stack (identity, which hands every service its Music Assistant
address) asks ``GET /hub/music_assistant/server`` (``household_server``) where
the household's server is. The answer never carries the token.

``fold_entry`` is the migration for entries imported from Home Assistant: they
kept HA's add-on address in ``data["url"]`` (which never resolves outside HA)
and had the real address put in ``options["url"]``. Folding moves the options
into ``data`` so each value is stored once, where Configure edits it.
"""
from __future__ import annotations

import ipaddress
import logging
from typing import Any
from urllib.parse import urlsplit

log = logging.getLogger("homehub.music_assistant_server")

DOMAIN = "music_assistant"
#: The entry keys that say where the server is and how to sign in to it.
SERVER_KEYS = ("url", "token")


def fold_entry(entry: Any) -> bool:
    """Move ``options["url"]``/``options["token"]`` into ``data`` (overwriting it); True when anything moved."""
    if getattr(entry, "domain", None) != DOMAIN:
        return False
    options = dict(entry.options or {})
    moved = {k: options.pop(k) for k in SERVER_KEYS if k in options}
    if not moved:
        return False
    data = dict(entry.data or {})
    for key, value in moved.items():
        if value not in (None, ""):
            data[key] = value
    entry.data, entry.options = data, options
    return True


def fold_all(hub: Any, store: Any) -> list[str]:
    """Fold every Music Assistant entry and save the ones that changed; returns their entry ids."""
    changed = []
    for entry in list(hub.config_entries.values()):
        if fold_entry(entry):
            store.save_config_entry(entry)
            changed.append(entry.entry_id)
            log.info("Music Assistant entry %s: server address and login now kept in the entry's data only",
                     entry.entry_id)
    return changed


def _is_loopback(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _driver_for(hub: Any, entry_id: str) -> Any:
    return (getattr(hub, "drivers", {}) or {}).get(entry_id)


def household_server(hub: Any) -> dict[str, Any]:
    """Where other services reach the household's Music Assistant, as the hub's entry says.

    ``url`` is the entry's address, or, when that is a loopback address (the hub
    shares the host's network with Music Assistant, other services do not), the
    address Music Assistant itself publishes (``base_url`` from its server info).
    ``url`` is None, with ``error`` saying why, when there is no usable address.
    The token is never included.
    """
    entries = [e for e in hub.config_entries.values() if e.domain == DOMAIN and not e.disabled_by
               and not getattr(e, "archived_at", None)]
    if not entries:
        return {"configured": False, "url": None,
                "error": "HomeHub has no Music Assistant integration. An admin can add it under "
                         "Integrations > Add > Music Assistant."}
    if len(entries) > 1:
        loaded = [e for e in entries if e.state == "loaded"]
        entries = loaded or entries
    entry = sorted(entries, key=lambda e: e.entry_id)[0]
    configured = str((entry.data or {}).get("url") or "").strip().rstrip("/")
    driver = _driver_for(hub, entry.entry_id)
    conn = getattr(driver, "conn", None)
    connected = bool(conn is not None and getattr(conn, "connected", False))
    info = dict(getattr(conn, "server_info", None) or {}) if conn is not None else {}
    published = str(info.get("base_url") or "").strip().rstrip("/")
    out: dict[str, Any] = {
        "configured": True, "entry_id": entry.entry_id, "title": entry.title, "state": entry.state,
        "reason": entry.reason, "connected": connected, "configured_url": configured or None,
        "published_url": published or None, "server_id": info.get("server_id") or entry.unique_id,
        "server_version": info.get("server_version"), "url": None, "error": None,
    }
    if not configured:
        out["error"] = "HomeHub's Music Assistant integration has no server address. An admin can set it under " \
                       "Integrations > Music Assistant > Configure."
    elif not _is_loopback(configured):
        out["url"] = configured
    elif published and not _is_loopback(published):
        out["url"] = published
    else:
        out["error"] = (f"HomeHub reaches Music Assistant at {configured}, which only works on HomeHub's own host, "
                        "and Music Assistant has not told HomeHub an address other services can use. Set the "
                        "integration's server address to one the whole network can reach (Integrations > Music "
                        "Assistant > Configure), or set Music Assistant's published IP (Settings > Core > Streams).")
    if out["url"] and not connected and entry.state != "loaded":
        out["error"] = f"HomeHub cannot reach Music Assistant at {configured}: {entry.reason or entry.state}"
    return out


class AccountError(Exception):
    """A person's Music Assistant account could not be found or made."""

    def __init__(self, message: str, status: int = 502) -> None:
        super().__init__(message)
        self.status = status


def _connection(hub: Any) -> Any:
    """The live connection of the household's Music Assistant entry (the hub's own admin login)."""
    server = household_server(hub)
    driver = _driver_for(hub, server.get("entry_id") or "")
    conn = getattr(driver, "conn", None)
    if conn is None or not getattr(conn, "connected", False):
        raise AccountError(server.get("error") or "HomeHub is not connected to Music Assistant", 503)
    return conn


async def provision_account(hub: Any, username: str, display_name: str | None = None,
                            token_name: str = "Jarvis") -> dict[str, Any]:
    """The person's own Music Assistant account (made when missing) and a new long-lived token for it.

    Music Assistant keeps play history, resume points and playback speed per account, so each
    person needs one. An existing account with the same username (any case) is used, never a
    second one. Guest accounts get no token (Music Assistant refuses them). The token is returned
    to the caller (identity, over the internal secret) and never logged.
    """
    name = (username or "").strip().lower()
    if len(name) < 2:
        raise AccountError("A Music Assistant username needs at least 2 characters", 400)
    conn = _connection(hub)
    from services.homehub.core import HubError

    try:
        return await _provision(conn, name, display_name, token_name)
    except HubError as exc:
        raise AccountError(f"Music Assistant refused: {exc}") from exc


async def _provision(conn: Any, name: str, display_name: str | None, token_name: str) -> dict[str, Any]:
    import secrets

    users = await conn.command("auth/users")
    users = users.get("result", users) if isinstance(users, dict) else users
    found = next((u for u in users or [] if str(u.get("username") or "").lower() == name), None)
    created = False
    if found is None:
        # A password nobody knows: the person signs in through Jarvis; an admin can set one in MA.
        found = await conn.command("auth/user/create", username=name, password=secrets.token_urlsafe(24),
                                   role="user", display_name=display_name or name)
        created = True
    if str(found.get("role") or "") == "guest":
        raise AccountError(f"{name} is a guest account in Music Assistant and cannot have a token", 409)
    if found.get("enabled") is False:
        raise AccountError(f"{name}'s Music Assistant account is disabled; an admin can enable it in MA", 409)
    token = await conn.command("auth/token/create", name=token_name, user_id=found["user_id"])
    if not isinstance(token, str) or not token:
        raise AccountError("Music Assistant did not issue a token")
    log.info("Music Assistant account for %s: %s, new %r token issued", name,
             "made" if created else "existing", token_name)
    return {"username": found.get("username") or name, "user_id": found["user_id"], "created": created,
            "token": token}


async def check_music_assistant(data: dict[str, Any]) -> None:
    """Refuse a new address or token that Music Assistant does not accept (connects and signs in once)."""
    from services.homehub.core import HubError
    from services.homehub.drivers.music_assistant import MAConnection
    from services.homehub.flows import NeedsInput

    url = str(data.get("url") or "").strip().rstrip("/")
    if not url:
        return
    conn = MAConnection(url, str(data.get("token") or "") or None, lambda _e: None, lambda: None)
    try:
        await conn.connect()
    except HubError as exc:
        raise NeedsInput([], str(exc), error=f"Not saved: {exc}") from exc
    finally:
        await conn.close()

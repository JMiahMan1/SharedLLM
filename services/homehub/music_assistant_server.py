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

import asyncio
import contextlib
import ipaddress
import logging
from typing import Any
from urllib.parse import urlsplit

log = logging.getLogger("homehub.music_assistant_server")

DOMAIN = "music_assistant"
#: The entry keys that say where the server is and how to sign in to it.
SERVER_KEYS = ("url", "token")
#: hassmic's Sendspin provider listens on this TCP port on the satellite itself.
SENDSPIN_PORT = 28928
SENDSPIN_ENTITY_NAME = "Sendspin pairing token"
SENDSPIN_OBJECT_ID = "sendspin_pairing_token"
#: Music Assistant's Audiobookshelf provider domain (multi-instance).
ABS_DOMAIN = "audiobookshelf"


class SendspinError(Exception):
    """A Sendspin request the hub cannot answer; ``status`` is the HTTP status the API answers with."""

    def __init__(self, message: str, status: int = 503) -> None:
        super().__init__(message)
        self.status = status


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


def _esphome_entry_for_device(hub: Any, device_id: str) -> tuple[Any | None, str | None]:
    """The ESPHome ConfigEntry behind a device, or ``(None, why not)``."""
    device = hub.devices.get(device_id)
    if device is None:
        return None, f"There is no device {device_id!r}. Choose an ESPHome satellite from Devices."
    entry = next((hub.config_entries[eid] for eid in device.config_entries
                  if eid in hub.config_entries and hub.config_entries[eid].domain == "esphome"), None)
    if entry is None:
        return None, "This device is not an ESPHome satellite, so it has no Sendspin pairing token."
    return entry, None


def _sendspin_entity(hub: Any, device_id: str) -> Any | None:
    """The hassmic entity that carries the Sendspin pairing token, whatever its domain or state."""
    for entity in hub.entities.values():
        if entity.device_id != device_id:
            continue
        unique_id = str(entity.unique_id or "").lower()
        object_id = entity.entity_id.split(".", 1)[-1].lower()
        if (unique_id.endswith("/" + SENDSPIN_ENTITY_NAME.lower())
                or SENDSPIN_OBJECT_ID in unique_id
                or object_id == SENDSPIN_OBJECT_ID
                or str(entity.original_name or "").lower() == SENDSPIN_ENTITY_NAME.lower()):
            return entity
    return None


def find_sendspin_token(hub: Any, device_id: str) -> tuple[str | None, str | None]:
    """``(token, host)`` for one ESPHome satellite, or ``(None, why not)``.

    The token is hassmic's own, read from its disabled-by-default diagnostic entity. An unencrypted
    node refuses to publish it, so a missing entity/state is reported as such instead of guessed.
    """
    entry, error = _esphome_entry_for_device(hub, device_id)
    if error is not None:
        return None, error
    host = str((entry.data or {}).get("host") or "").strip()
    if not host:
        return None, "The ESPHome entry has no host address, so its Sendspin address is unknown."
    entity = _sendspin_entity(hub, device_id)
    if entity is None:
        return None, (f"This satellite has no {SENDSPIN_ENTITY_NAME!r} entity. It needs the hassmic "
                      "(echo-dot-assist) firmware that offers Music Assistant Sendspin.")
    if entity.disabled_by:
        reason = f"The {SENDSPIN_ENTITY_NAME!r} entity is disabled. Enable it in the hub, then try again."
        if not str((entry.data or {}).get("noise_psk") or "").strip():
            reason += (" The node is also not encrypted yet: hassmic only publishes the token once it "
                       "has a Noise key, so add the node again with encryption provisioning.")
        return None, reason
    state = hub.states.get(entity.entity_id)
    if state is None:
        return None, (f"The satellite has not published its {SENDSPIN_ENTITY_NAME!r} yet. hassmic does that "
                      "only once the node is encrypted; if it stays unknown, check the node is online.")
    token = str(state.state or "").strip()
    if not token or token.lower() in ("unknown", "unavailable", "none"):
        return None, (f"The satellite has not published its {SENDSPIN_ENTITY_NAME!r} yet. hassmic does that "
                      "only once the node is encrypted; if it stays unknown, check the node is online.")
    return token, host


def _reachable_music_assistant(hub: Any) -> dict[str, Any]:
    """The household's configured, connected Music Assistant, or a clear SendspinError."""
    server = household_server(hub)
    if not server.get("configured"):
        raise SendspinError("HomeHub has no Music Assistant integration. An admin can add it under "
                            "Integrations > Music Assistant.")
    if not server.get("url"):
        raise SendspinError(server.get("error")
                            or "HomeHub has no usable Music Assistant address. An admin can set it under "
                               "Integrations > Music Assistant > Configure.")
    if not server.get("connected"):
        detail = server.get("error") or f"HomeHub is not connected to Music Assistant at {server['url']}."
        raise SendspinError(f"{str(detail).rstrip('.')}. Check Integrations > Music Assistant.")
    return server


def _require_device(hub: Any, device_id: str) -> Any:
    """The device, or a 404 that names it; a Sendspin request never answers 200 for a device that is not there."""
    device = hub.devices.get(device_id)
    if device is None:
        raise SendspinError(f"There is no device {device_id!r}. Choose an ESPHome satellite from Devices.", 404)
    return device


async def sendspin_pairing_info(hub: Any, device_id: str, *, include_token: bool = False) -> dict[str, Any]:
    """Where Music Assistant should reach one satellite's Sendspin provider, and whether its token is ready.

    Fails loudly when Music Assistant is not configured or connected. The pairing token itself is only
    put in the answer when an authenticated admin asks for it (``include_token=True``); it is never logged.
    """
    _require_device(hub, device_id)
    server = _reachable_music_assistant(hub)
    entry, _error = _esphome_entry_for_device(hub, device_id)
    host = str(((entry.data or {}) if entry is not None else {}).get("host") or "").strip()
    token, host_or_reason = find_sendspin_token(hub, device_id)
    out: dict[str, Any] = {
        "ok": token is not None,
        "device_id": device_id,
        "sendspin_address": f"{host}:{SENDSPIN_PORT}" if host else None,
        "token_available": bool(token),
        "music_assistant_url": server.get("url"),
        "paired": False,
        "manual_pairing_required": token is None,
    }
    if token is None and host_or_reason is not None:
        out["error"] = host_or_reason
    if include_token and token:
        out["pairing_token"] = token
    if token:
        out["message"] = (f"Music Assistant can pair this satellite at {out['sendspin_address']} with its "
                          "Sendspin pairing token.")
    else:
        out["message"] = out.get("error") or "This satellite has no Sendspin pairing token yet."
    return out


async def pair_sendspin_device(hub: Any, device_id: str) -> dict[str, Any]:
    """Whether Music Assistant can pair one satellite, and why not.

    Music Assistant pairs an encrypted Sendspin satellite itself: it follows HomeHub's
    ``sendspin/subscribe_clients`` and fetches the token with ``sendspin/get_pairing_token``
    (api.py, sendspin_clients.py). There is no command to send it, so this only reports
    whether HomeHub has what it needs. The token never appears in the answer.
    """
    _require_device(hub, device_id)
    server = _reachable_music_assistant(hub)
    entry, error = _esphome_entry_for_device(hub, device_id)
    host = str(((entry.data or {}) if entry is not None else {}).get("host") or "").strip()
    token, host_or_reason = find_sendspin_token(hub, device_id)
    clients = getattr(getattr(hub, "runtime", None), "sendspin_clients", None)
    client_id = next((c for c in (clients.client_ids() if clients is not None else [])
                      if clients.device_for(c) == device_id), None)
    out: dict[str, Any] = {
        "ok": bool(token), "device_id": device_id,
        "sendspin_address": f"{host}:{SENDSPIN_PORT}" if host else None,
        "token_available": bool(token), "client_id_known": client_id is not None,
        "music_assistant_url": server.get("url"),
        "automatic": True, "paired": False, "manual_pairing_required": False,
    }
    if token is None:
        out["error"] = host_or_reason or error or "This satellite has no Sendspin pairing token yet."
        out["message"] = out["error"]
        return out
    out["message"] = ("Music Assistant pairs this satellite by itself through HomeHub once both are "
                      "online; nothing needs to be done here.")
    return out

#: username -> lock: one account request per person at a time, so two arriving together cannot each
#: make a token (or, for a new person, each try to make the account).
_ACCOUNT_LOCKS: dict[str, asyncio.Lock] = {}


def _account_lock(name: str) -> asyncio.Lock:
    lock = _ACCOUNT_LOCKS.get(name)
    if lock is None:
        lock = _ACCOUNT_LOCKS[name] = asyncio.Lock()
    return lock


async def provision_account(hub: Any, username: str, display_name: str | None = None,
                            token_name: str = "Jarvis") -> dict[str, Any]:
    """The person's own Music Assistant account (made when missing) and a new long-lived token for it.

    Music Assistant keeps play history, resume points and playback speed per account, so each
    person needs one. An existing account with the same username (any case) is used, never a
    second one. Guest accounts get no token (Music Assistant refuses them). The token is returned
    to the caller (identity, over the internal secret) and never logged.

    Music Assistant stores only a hash of a token, so an earlier one cannot be handed out again.
    Instead the new token replaces it: the person's earlier long-lived tokens with the same name
    (made by an earlier request, whose token identity no longer holds, since it only asks when it
    has none) are revoked once the new one exists. Requests for one person run one at a time.
    """
    name = (username or "").strip().lower()
    if len(name) < 2:
        raise AccountError("A Music Assistant username needs at least 2 characters", 400)
    conn = _connection(hub)
    from services.homehub.core import HubError

    async with _account_lock(name):
        try:
            return await _provision(conn, name, display_name, token_name)
        except HubError as exc:
            raise AccountError(f"Music Assistant refused: {exc}") from exc


async def _earlier_tokens(conn: Any, user_id: str, token_name: str) -> list[str]:
    """Ids of the account's long-lived tokens named ``token_name`` ([] if MA cannot list them)."""
    from services.homehub.core import HubError

    try:
        tokens = await conn.command("auth/tokens", user_id=user_id)
    except HubError as exc:
        log.warning("Music Assistant could not list tokens (%s); earlier %r tokens are kept", exc, token_name)
        return []
    tokens = tokens.get("result", tokens) if isinstance(tokens, dict) else tokens
    return [str(t["token_id"]) for t in tokens or []
            if isinstance(t, dict) and t.get("token_id") and t.get("name") == token_name
            and t.get("user_id") in (None, user_id) and t.get("is_long_lived", True)]


async def _revoke(conn: Any, token_ids: list[str]) -> int:
    from services.homehub.core import HubError

    revoked = 0
    for token_id in token_ids:
        try:
            await conn.command("auth/token/revoke", token_id=token_id)
            revoked += 1
        except HubError as exc:
            log.warning("Music Assistant did not revoke an earlier token (%s)", exc)
    return revoked


async def _provision(conn: Any, name: str, display_name: str | None, token_name: str) -> dict[str, Any]:
    import secrets

    users = await conn.command("auth/users")
    users = users.get("result", users) if isinstance(users, dict) else users
    found = next((u for u in users or [] if str(u.get("username") or "").lower() == name), None)
    created = False
    if found is None:
        # Scope a newly created account to shared (non-Audiobookshelf) music providers so it
        # cannot accidentally read or overwrite another family member's Audiobookshelf progress.
        initial_filter = await _default_shared_provider_filter(conn)
        create_args: dict[str, Any] = {
            "username": name,
            "password": secrets.token_urlsafe(24),
            "role": "user",
            "display_name": display_name or name,
        }
        if initial_filter:
            create_args["provider_filter"] = initial_filter
        # A password nobody knows: the person signs in through Jarvis; an admin can set one in MA.
        found = await conn.command("auth/user/create", **create_args)
        created = True
    if str(found.get("role") or "") == "guest":
        raise AccountError(f"{name} is a guest account in Music Assistant and cannot have a token", 409)
    if found.get("enabled") is False:
        raise AccountError(f"{name}'s Music Assistant account is disabled; an admin can enable it in MA", 409)
    earlier = [] if created else await _earlier_tokens(conn, found["user_id"], token_name)
    token = await conn.command("auth/token/create", name=token_name, user_id=found["user_id"])
    if not isinstance(token, str) or not token:
        raise AccountError("Music Assistant did not issue a token")
    revoked = await _revoke(conn, earlier)
    log.info("Music Assistant account for %s: %s, new %r token issued%s", name,
             "made" if created else "existing", token_name,
             f", {revoked} earlier one(s) revoked" if revoked else "")
    return {"username": found.get("username") or name, "user_id": found["user_id"], "created": created,
            "token": token, "revoked_tokens": revoked}


def abs_provider_name(username: str) -> str:
    """Display name for a person's dedicated Audiobookshelf provider instance in Music Assistant."""
    return f"Audiobookshelf ({(username or '').strip().lower()})"


def _unwrap_result(raw: Any) -> Any:
    """Unwrap a JSON-RPC envelope ``{"result": ...}`` without stripping a SetupFlowStep dict.

    MA's ``SetupFlowStep`` objects carry both ``flow_id`` and ``result`` (which is ``None`` on
    ``form`` steps and ``{"instance_id": ...}`` on ``finish`` steps), so they must be kept intact.
    """
    if isinstance(raw, dict) and "result" in raw and "flow_id" not in raw:
        return raw["result"]
    return raw


def _shared_music_provider_ids(providers: list[dict[str, Any]]) -> list[str]:
    """Enabled non-Audiobookshelf music provider instance ids (shared across the household)."""
    out: list[str] = []
    for prov in providers or []:
        if not isinstance(prov, dict):
            continue
        if prov.get("type") != "music":
            continue
        if prov.get("domain") == ABS_DOMAIN:
            continue
        if prov.get("enabled") is False:
            continue
        iid = str(prov.get("instance_id") or "").strip()
        if iid:
            out.append(iid)
    return sorted(set(out))


async def _default_shared_provider_filter(conn: Any) -> list[str]:
    """Shared music providers to initialize a new MA user's provider_filter, or [] if unavailable."""
    from services.homehub.core import HubError

    try:
        providers = _unwrap_result(await conn.command("config/providers"))
    except HubError:
        return []
    if not isinstance(providers, list):
        return []
    return _shared_music_provider_ids(providers)


def _compute_user_provider_filter(
    existing_filter: list[str] | None,
    providers: list[dict[str, Any]],
    user_abs_instance_id: str | None,
) -> list[str]:
    """Allowed music provider instance ids for one user.

    Includes all enabled shared (non-Audiobookshelf) music providers, preserves any other
    non-Audiobookshelf providers already granted to the user, and includes *only* this user's
    own Audiobookshelf instance (never another family member's).
    """
    all_abs_ids = {
        str(p.get("instance_id") or "").strip()
        for p in providers or []
        if isinstance(p, dict) and p.get("domain") == ABS_DOMAIN and p.get("instance_id")
    }
    shared = _shared_music_provider_ids(providers)
    preserved = [
        str(pid).strip()
        for pid in (existing_filter or [])
        if str(pid).strip() and str(pid).strip() not in all_abs_ids
    ]
    allowed = set(shared) | set(preserved)
    if user_abs_instance_id:
        allowed.add(user_abs_instance_id)
    return sorted(allowed)


def _find_user_abs_instance(
    *,
    username: str,
    user: dict[str, Any],
    all_users: list[dict[str, Any]],
    providers: list[dict[str, Any]],
    existing_instance_id: str | None = None,
) -> str | None:
    """Locate the Audiobookshelf provider instance already belonging to ``username``, if any."""
    abs_providers = {
        str(p["instance_id"]): p
        for p in providers or []
        if isinstance(p, dict) and p.get("domain") == ABS_DOMAIN and p.get("instance_id")
    }
    if not abs_providers:
        return None
    if existing_instance_id and existing_instance_id in abs_providers:
        return existing_instance_id

    uid = str(user.get("user_id") or "")
    claimed_by_others: set[str] = set()
    for other in all_users or []:
        if not isinstance(other, dict) or str(other.get("user_id") or "") == uid:
            continue
        for pid in other.get("provider_filter") or []:
            if pid in abs_providers:
                claimed_by_others.add(pid)

    # 1. Exact label match ("Audiobookshelf (<username>)").
    expected_name = abs_provider_name(username).lower()
    for iid, prov in abs_providers.items():
        if iid in claimed_by_others:
            continue
        if str(prov.get("name") or "").strip().lower() == expected_name:
            return iid

    # 2. An ABS instance already exclusively in this user's provider_filter.
    for pid in user.get("provider_filter") or []:
        if pid in abs_providers and pid not in claimed_by_others:
            return pid

    return None


def _build_abs_flow_values(
    *,
    abs_url: str,
    abs_api_key: str | None,
    abs_username: str | None,
    abs_password: str | None,
    verify_ssl: bool = True,
) -> dict[str, Any]:
    """Values for MA's Audiobookshelf setup/reconfigure flow.

    MA's ABS provider checks ``if token_api is not None`` before falling back to
    username/password, so password logins must explicitly pass ``api_token: None``.
    """
    values: dict[str, Any] = {
        "url": abs_url.strip().rstrip("/"),
        "verify_ssl": bool(verify_ssl),
    }
    if abs_api_key:
        values["api_token"] = abs_api_key.strip()
        values["username"] = None
        values["password"] = None
    else:
        values["api_token"] = None
        values["username"] = (abs_username or "").strip()
        values["password"] = abs_password or ""
    return values


async def _run_abs_setup_flow(
    conn: Any,
    *,
    values: dict[str, Any],
    instance_id: str | None,
    providers_before: list[dict[str, Any]],
) -> str:
    """Create or reconfigure an Audiobookshelf provider instance through MA's setup flow."""
    if instance_id:
        step = _unwrap_result(await conn.command("config/providers/reconfigure", instance_id=instance_id))
    else:
        step = _unwrap_result(await conn.command("config/providers/setup", provider_domain=ABS_DOMAIN))
    if not isinstance(step, dict) or not step.get("flow_id") or step.get("type") in ("abort", "finish"):
        raise AccountError(f"Music Assistant did not start Audiobookshelf setup: {step}")

    flow_id = step["flow_id"]
    result = _unwrap_result(await conn.command("config/flows/submit", flow_id=flow_id, values=values))
    errors = result.get("errors") if isinstance(result, dict) else None
    step_type = result.get("type") if isinstance(result, dict) else None
    if errors or step_type == "abort":
        with contextlib.suppress(Exception):
            await conn.command("config/flows/abort", flow_id=flow_id)
        detail = errors or (result.get("reason") if isinstance(result, dict) else result)
        raise AccountError(f"Music Assistant refused Audiobookshelf credentials: {detail}", 422)

    if instance_id:
        return instance_id

    flow_result = result.get("result") if isinstance(result, dict) else None
    if isinstance(flow_result, dict) and flow_result.get("instance_id"):
        return str(flow_result["instance_id"])

    # Fallback: diff config/providers before and after.
    before_ids = {str(p.get("instance_id") or "") for p in providers_before if isinstance(p, dict)}
    providers_after = _unwrap_result(await conn.command("config/providers"))
    for prov in providers_after or []:
        if (
            isinstance(prov, dict)
            and prov.get("domain") == ABS_DOMAIN
            and str(prov.get("instance_id") or "") not in before_ids
        ):
            return str(prov["instance_id"])
    raise AccountError("Music Assistant created an Audiobookshelf provider but did not report its instance_id")


async def sync_user_audiobookshelf_provider(
    hub: Any,
    *,
    username: str,
    display_name: str | None = None,
    abs_url: str | None = None,
    abs_api_key: str | None = None,
    abs_username: str | None = None,
    abs_password: str | None = None,
    verify_ssl: bool = True,
    existing_instance_id: str | None = None,
    remove_if_unconfigured: bool = False,
) -> dict[str, Any]:
    """Ensure ``username`` has their own MA account, dedicated ABS provider (if configured), and provider_filter.

    * Each user with Audiobookshelf credentials gets their own ``audiobookshelf--<id>`` provider
      instance in Music Assistant using *their* ABS account (API token or username+password).
    * Their MA user's ``provider_filter`` is set to all shared non-ABS music providers plus *only*
      their own Audiobookshelf provider instance.
    * A user without Audiobookshelf credentials gets ``provider_filter`` set to shared music
      providers only (no Audiobookshelf provider at all), so they can never read or overwrite
      another family member's progress.
    * Secrets (API key / password) are never logged or returned.
    """
    import secrets

    name = (username or "").strip().lower()
    if len(name) < 2:
        raise AccountError("A Music Assistant username needs at least 2 characters", 400)

    conn = _connection(hub)
    from services.homehub.core import HubError

    has_abs = bool(
        (abs_url or "").strip()
        and (
            (abs_api_key or "").strip()
            or ((abs_username or "").strip() and (abs_password or "").strip())
        )
    )

    async with _account_lock(name):
        try:
            users = _unwrap_result(await conn.command("auth/users")) or []
            found = next((u for u in users if isinstance(u, dict) and str(u.get("username") or "").lower() == name), None)
            user_created = False
            if found is None:
                found = await conn.command(
                    "auth/user/create",
                    username=name,
                    password=secrets.token_urlsafe(24),
                    role="user",
                    display_name=display_name or name,
                )
                user_created = True
                users = [*users, found]

            if str(found.get("role") or "") == "guest":
                raise AccountError(f"{name} is a guest account in Music Assistant", 409)
            if found.get("enabled") is False:
                raise AccountError(f"{name}'s Music Assistant account is disabled", 409)

            providers = _unwrap_result(await conn.command("config/providers")) or []
            owned_id = _find_user_abs_instance(
                username=name,
                user=found,
                all_users=users,
                providers=providers,
                existing_instance_id=existing_instance_id,
            )

            action = "scoped"
            abs_instance_id: str | None = owned_id
            if has_abs:
                flow_values = _build_abs_flow_values(
                    abs_url=str(abs_url),
                    abs_api_key=abs_api_key,
                    abs_username=abs_username,
                    abs_password=abs_password,
                    verify_ssl=verify_ssl,
                )
                abs_instance_id = await _run_abs_setup_flow(
                    conn,
                    values=flow_values,
                    instance_id=owned_id,
                    providers_before=providers,
                )
                action = "updated" if owned_id else "created"
                label = abs_provider_name(name)
                try:
                    await conn.command(
                        "config/providers/save",
                        provider_domain=ABS_DOMAIN,
                        instance_id=abs_instance_id,
                        values={"name": label},
                    )
                except HubError as exc:
                    log.warning("Could not label Music Assistant ABS provider %s as %r: %s", abs_instance_id, label, exc)
                # Refresh provider list so _compute_user_provider_filter knows about any newly created ABS instance.
                providers = _unwrap_result(await conn.command("config/providers")) or providers
            else:
                if owned_id and remove_if_unconfigured:
                    await conn.command("config/providers/remove", instance_id=owned_id)
                    providers = [p for p in providers if not (isinstance(p, dict) and p.get("instance_id") == owned_id)]
                    action = "removed"
                abs_instance_id = None

            desired_filter = _compute_user_provider_filter(
                found.get("provider_filter"),
                providers,
                abs_instance_id,
            )
            current_filter = sorted(str(x).strip() for x in (found.get("provider_filter") or []) if str(x).strip())
            filter_updated = False
            if current_filter != desired_filter:
                await conn.command(
                    "auth/user/update",
                    user_id=found["user_id"],
                    provider_filter=desired_filter,
                )
                filter_updated = True

            log.info(
                "Music Assistant Audiobookshelf sync for %s: action=%s abs_instance_id=%s filter_updated=%s (%d providers)",
                name, action, abs_instance_id, filter_updated, len(desired_filter),
            )
            return {
                "username": found.get("username") or name,
                "user_id": found["user_id"],
                "user_created": user_created,
                "abs_instance_id": abs_instance_id,
                "provider_filter": desired_filter,
                "filter_updated": filter_updated,
                "action": action,
            }
        except HubError as exc:
            raise AccountError(f"Music Assistant refused: {exc}") from exc


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

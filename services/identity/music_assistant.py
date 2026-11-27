"""Which Music Assistant server, and which Music Assistant login, a person uses.

Identity is the one place every service (gateway, execution, the UI's streams
and widgets) gets its Music Assistant address and token from: they all read
``mass_url``/``mass_token`` from ``POST /api/resolve``. The rules, in order:

1. **Their own server.** A person who saved a Music Assistant URL in Accounts &
   Integrations uses that server with their own token. If it does not answer,
   that is reported as their integration's error; nothing switches them to the
   household's server behind their back. "Use the household Music Assistant"
   (clearing their URL) is their explicit choice.
2. **The household's server**, which is wherever HomeHub's Music Assistant
   integration (its config entry) says it is: ``GET /hub/music_assistant/server``
   on the hub. Nothing else names it: no environment variable, no built-in
   address. With no HomeHub, or no Music Assistant integration in it, there is
   no household server and the error says so.
3. **The login on the household server** is the person's own Music Assistant
   account. A token they saved is used as is. A person with none gets an
   account made for them (``provision``): Music Assistant keeps recently played,
   resume points and playback speed per account, so one shared login would mix
   everybody's progress. Only when that cannot be done (no hub) does an admin's
   explicit credential grant lend the household account's login.

Music Assistant stores favourites, the library and playlists for the whole
server, not per account; only play history, resume positions, playback speed and
preferences are per account (read from MA 2.10.9's ``music`` controller).

Tokens are never logged or returned anywhere but ``/api/resolve`` (internal).
"""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

log = logging.getLogger("identity.music_assistant")

HUB_TIMEOUT_S = 8.0
#: Provider setup/reconfigure logs into Audiobookshelf and loads the provider, so give it more time.
HUB_SYNC_TIMEOUT_S = 30.0
#: How long a household-server answer is reused; a failure is retried sooner.
SERVER_TTL_S = 30.0
SERVER_ERROR_TTL_S = 10.0
#: Name of the tokens Jarvis issues on a person's Music Assistant account.
TOKEN_NAME = "Jarvis"

NO_MA_MESSAGE = ("No Music Assistant is set up: add the Music Assistant integration in HomeHub "
                 "or your own in Accounts & Integrations.")
FAVORITES_NOTE = ("Music Assistant keeps play history, resume points and playback speed for each person; "
                  "favourites, the library and playlists are shared by everyone on the same server.")


class HubUnavailable(Exception):
    """The hub could not be asked, or refused."""


@dataclass
class Resolution:
    """The Music Assistant a person uses. ``url``/``token`` are None when ``error`` says why."""

    url: str | None = None
    token: str | None = None
    #: "own" (their own server), "household" (HomeHub's), or "absent".
    server: str = "absent"
    #: Where the login came from: "own", "provisioned", "granted", or "absent".
    login: str = "absent"
    error: str | None = None
    #: The Music Assistant account id the login belongs to, when known (household server only).
    user_id: str | None = None
    #: Accounts provisioned while resolving (username -> created).
    provisioned: dict[str, bool] = field(default_factory=dict)


# -- the hub ------------------------------------------------------------------------------

def _hub_request(
    hub_url: str,
    secret: str,
    path: str,
    body: dict[str, Any] | None = None,
    *,
    timeout: float = HUB_TIMEOUT_S,
) -> dict[str, Any]:
    if not hub_url:
        raise HubUnavailable("HomeHub is not set up (homehub_url)")
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(f"{hub_url.rstrip('/')}{path}", data=data, method="POST" if body is not None else "GET",
                                 headers={"Content-Type": "application/json", "X-Internal-Secret": secret})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read() or b"{}").get("detail")
        except ValueError:
            detail = None
        raise HubUnavailable(str(detail or f"HomeHub answered HTTP {exc.code}")) from None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise HubUnavailable(f"HomeHub could not be reached ({type(exc).__name__})") from None


def _hub_login() -> tuple[str, str]:
    from services import config

    return (getattr(config, "HOMEHUB_URL", "") or "").rstrip("/"), getattr(config, "INTERNAL_SECRET", "") or ""


_cache_lock = threading.Lock()
_cache: dict[str, Any] = {"at": 0.0, "ttl": 0.0, "value": None}


def household_server(*, fresh: bool = False) -> dict[str, Any]:
    """HomeHub's answer about the household server (``url`` None plus ``error`` when there is none)."""
    now = time.monotonic()
    with _cache_lock:
        if not fresh and _cache["value"] is not None and now - _cache["at"] < _cache["ttl"]:
            return dict(_cache["value"])
    hub_url, secret = _hub_login()
    if not hub_url:
        value: dict[str, Any] = {"url": None, "error": NO_MA_MESSAGE, "hub": False}
    else:
        try:
            value = {**_hub_request(hub_url, secret, "/hub/music_assistant/server"), "hub": True}
        except HubUnavailable as exc:
            value = {"url": None, "error": f"HomeHub could not say where Music Assistant is: {exc}", "hub": True}
        if not value.get("url") and value.get("configured") is False:
            value["error"] = NO_MA_MESSAGE
    with _cache_lock:
        _cache.update(at=now, ttl=SERVER_TTL_S if value.get("url") else SERVER_ERROR_TTL_S, value=dict(value))
    return value


def clear_cache() -> None:
    with _cache_lock:
        _cache.update(at=0.0, ttl=0.0, value=None)


def provision_account(username: str, display_name: str | None = None) -> dict[str, Any]:
    """Ask the hub for ``username``'s own Music Assistant account and a fresh token (made if missing)."""
    hub_url, secret = _hub_login()
    return _hub_request(hub_url, secret, "/hub/music_assistant/accounts",
                        {"username": username, "display_name": display_name or username, "token_name": TOKEN_NAME})


def sync_audiobookshelf_provider(
    *,
    username: str,
    display_name: str | None = None,
    abs_url: str | None = None,
    abs_api_key: str | None = None,
    abs_username: str | None = None,
    abs_password: str | None = None,
    existing_instance_id: str | None = None,
    remove_if_unconfigured: bool = False,
) -> dict[str, Any]:
    """Ask the hub to sync ``username``'s dedicated MA Audiobookshelf provider and provider_filter."""
    hub_url, secret = _hub_login()
    payload: dict[str, Any] = {
        "username": username,
        "display_name": display_name or username,
        "abs_url": abs_url,
        "abs_api_key": abs_api_key,
        "abs_username": abs_username,
        "abs_password": abs_password,
        "existing_instance_id": existing_instance_id,
        "remove_if_unconfigured": bool(remove_if_unconfigured),
    }
    return _hub_request(
        hub_url,
        secret,
        "/hub/music_assistant/audiobookshelf",
        payload,
        timeout=HUB_SYNC_TIMEOUT_S,
    )


# -- the rules ----------------------------------------------------------------------------

def resolve(
    *,
    username: str,
    own_url: str | None,
    own_token: str | None,
    own_user_id: str | None = None,
    display_name: str | None = None,
    grant: tuple[str | None, str | None] | None = None,
    may_provision: bool = True,
    server_fn: Any = None,
    provision_fn: Any = None,
) -> Resolution:
    """Apply the rules above to one person. ``grant`` is the household account's (url, token) when an
    admin granted it to them. Provisioning results are in ``Resolution.provisioned`` plus the new token,
    for the caller to save. ``server_fn``/``provision_fn`` default to this module's hub calls."""
    server_fn = server_fn or household_server
    provision_fn = provision_fn or provision_account
    own_url = (own_url or "").strip().rstrip("/") or None
    own_token = (own_token or "").strip() or None
    if own_url:
        if not own_token:
            return Resolution(url=own_url, server="own", error=(
                f"Your Music Assistant at {own_url} has no access token. Add one in Accounts & Integrations, "
                "or use the household Music Assistant."))
        return Resolution(url=own_url, token=own_token, server="own", login="own")

    server = server_fn()
    household_url = server.get("url")
    if not household_url:
        if grant and grant[0] and grant[1]:
            # No household server to make an account on; an admin lent the household account's own server.
            return Resolution(url=grant[0], token=grant[1], server="own", login="granted")
        return Resolution(error=server.get("error") or NO_MA_MESSAGE)
    if own_token:
        return Resolution(url=household_url, token=own_token, server="household", login="own", user_id=own_user_id)
    if may_provision:
        try:
            made = provision_fn(username, display_name)
        except HubUnavailable as exc:
            made = None
            error = f"Could not make your Music Assistant account: {exc}"
        else:
            error = None
        if made and made.get("token"):
            res = Resolution(url=household_url, token=str(made["token"]), server="household", login="provisioned",
                             user_id=made.get("user_id"))
            res.provisioned[username] = bool(made.get("created"))
            return res
        if grant and grant[1]:
            return Resolution(url=household_url, token=grant[1], server="household", login="granted")
        return Resolution(url=household_url, server="household", error=error or (
            "You have no Music Assistant login. Add your token in Accounts & Integrations."))
    if grant and grant[1]:
        return Resolution(url=household_url, token=grant[1], server="household", login="granted")
    return Resolution(url=household_url, server="household",
                      error="You have no Music Assistant login. Add your token in Accounts & Integrations.")


def unreachable_message(res: Resolution, why: str) -> str:
    """What to tell a person whose Music Assistant did not answer."""
    if res.server == "own":
        return (f"Your own Music Assistant at {res.url} did not answer ({why}). Fix it in Accounts & "
                "Integrations, or switch to the household Music Assistant there.")
    return f"The household Music Assistant at {res.url} did not answer ({why})."


# -- probing (Accounts & Integrations status) ---------------------------------------------

def probe(url: str, token: str) -> dict[str, Any]:
    """Ask Music Assistant who this token is: ``{"state": "ok"|"unreachable"|"token_refused", ...}``."""
    req = urllib.request.Request(f"{url.rstrip('/')}/api", method="POST",
                                 data=json.dumps({"message_id": "jarvis-status", "command": "auth/me"}).encode(),
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=HUB_TIMEOUT_S) as resp:
            me = json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            return {"state": "token_refused", "message": "Music Assistant refused the access token."}
        return {"state": "unreachable", "message": f"Music Assistant answered HTTP {exc.code}."}
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return {"state": "unreachable", "message": f"No answer ({type(exc).__name__})."}
    if isinstance(me, dict) and me.get("error_code"):
        return {"state": "token_refused", "message": str(me.get("details") or me.get("error") or "Refused.")}
    me = me.get("result", me) if isinstance(me, dict) else {}
    return {"state": "ok", "ma_user_id": me.get("user_id"), "ma_username": me.get("username"),
            "ma_role": me.get("role")}


def stale_url_plan(users: list[Any], retired_hosts: list[str]) -> list[dict[str, Any]]:
    """The people whose saved Music Assistant URL points at a retired host (names and hosts only)."""
    retired = {h.strip().lower() for h in retired_hosts if h and h.strip()}
    out = []
    for user in users:
        url = (getattr(user, "mass_url", None) or "").strip()
        host = (urlsplit(url).hostname or "").lower() if url else ""
        if host and host in retired:
            out.append({"username": user.username, "host": host})
    return out

"""Audiobookshelf auth for the gateway's direct ABS calls, per user.

The gateway reaches ABS itself for a book's detail, its cover and the
listener's own progress. It used to send only the user's stored ABS API key,
and answered 400 "Audiobookshelf API key is not configured" for anyone who has
an ABS username and password instead (ABS does not let an ordinary user mint an
API key, so most household accounts are like that). Live, 2026-11-21: every
``GET /api/media/item?uri=abs://...`` for such a user was a 400 in ~10 ms and
the book page said "Couldn't load this book", while the execution service,
which logs in with the password, could search and list the same library.

So every gateway ABS call takes its bearer token from here:

* the user's API key when they have one;
* otherwise a login with the user's *own* ABS username and password
  (``POST {abs_url}/login``, the same route the execution service uses: this
  ABS sits behind a path prefix and ``/api/login`` is a 401 there), cached per
  ABS server and ABS username until shortly before the JWT expires;
* a 401 drops the cached token and logs in once more.

Only the credentials Identity resolved for the caller are ever used. There is
no shared or default account here: a user with neither a key nor a password
gets an error that says so, never somebody else's library or progress.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import time
from typing import Any

import aiohttp

log = logging.getLogger("gateway.abs_auth")

#: A token without a readable expiry (a legacy opaque token) is kept this long.
TOKEN_TTL_S = 30 * 60
#: Renew a JWT this long before it expires.
TOKEN_EXPIRY_MARGIN_S = 60
LOGIN_TIMEOUT = aiohttp.ClientTimeout(total=12.0, connect=4.0)

_tokens: dict[tuple[str, str], tuple[str, float]] = {}
_locks: dict[tuple[str, str], asyncio.Lock] = {}


class AbsAuthError(Exception):
    """No usable ABS credential for this user; ``status`` is the HTTP answer."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def _get(creds: Any, key: str) -> str:
    value = creds.get(key) if isinstance(creds, dict) else getattr(creds, key, None)
    return str(value or "").strip()


def abs_base_url(creds: Any) -> str:
    """The user's ABS base URL, or AbsAuthError when it is not set."""
    url = _get(creds, "audiobookshelf_url").rstrip("/")
    if not url:
        raise AbsAuthError("Audiobookshelf is not configured for this account (no server URL).")
    return url


def _cache_key(creds: Any) -> tuple[str, str]:
    return (abs_base_url(creds).lower(), _get(creds, "audiobookshelf_user").lower())


def _token_ttl(token: str) -> float:
    """Seconds a login token may be reused: until just before a JWT's ``exp``."""
    try:
        payload = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        remaining = float(claims["exp"]) - time.time() - TOKEN_EXPIRY_MARGIN_S
    except Exception:  # not a JWT
        return TOKEN_TTL_S
    return max(0.0, min(TOKEN_TTL_S, remaining))


def forget_token(creds: Any) -> None:
    """Drop this user's cached login token (after ABS refused it)."""
    with contextlib.suppress(AbsAuthError):
        _tokens.pop(_cache_key(creds), None)


def can_log_in(creds: Any) -> bool:
    return bool(_get(creds, "audiobookshelf_user") and _get(creds, "audiobookshelf_pass"))


async def _login(client: aiohttp.ClientSession, creds: Any) -> str:
    url = f"{abs_base_url(creds)}/login"
    username = _get(creds, "audiobookshelf_user")
    try:
        resp = await client.post(
            url,
            json={"username": username, "password": _get(creds, "audiobookshelf_pass")},
            timeout=LOGIN_TIMEOUT,
        )
    except (TimeoutError, aiohttp.ClientError) as e:
        raise AbsAuthError(f"Audiobookshelf is unreachable: {type(e).__name__}", status=502) from e
    try:
        if resp.status in (401, 403):
            raise AbsAuthError(
                "Audiobookshelf refused this account's username and password. "
                "Update them under Accounts & Integrations.",
                status=401,
            )
        if resp.status != 200:
            raise AbsAuthError(f"Audiobookshelf login returned HTTP {resp.status}", status=502)
        data = await resp.json(content_type=None)
    finally:
        await resp.release()
    user = (data or {}).get("user") or {}
    # ABS 2.26+ issues a short-lived JWT accessToken; ``token`` is the legacy key.
    token = str(user.get("accessToken") or user.get("token") or "")
    if not token:
        raise AbsAuthError("Audiobookshelf login returned no token", status=502)
    return token


async def abs_token(client: aiohttp.ClientSession, creds: Any, *, force_login: bool = False) -> str:
    """The bearer token this user's ABS calls carry.

    ``force_login`` skips both the API key and the cached token (used after a
    401) when the user can log in; without a password it is the key or nothing.
    """
    api_key = _get(creds, "audiobookshelf_api_key")
    if api_key and not (force_login and can_log_in(creds)):
        return api_key
    if not can_log_in(creds):
        abs_base_url(creds)  # a missing URL is the clearer error
        raise AbsAuthError(
            "Audiobookshelf needs this account's ABS username and password (or an API key). "
            "Add them under Accounts & Integrations."
        )
    key = _cache_key(creds)
    lock = _locks.setdefault(key, asyncio.Lock())
    async with lock:
        cached = _tokens.get(key)
        if cached and not force_login and cached[1] > time.time():
            return cached[0]
        token = await _login(client, creds)
        _tokens[key] = (token, time.time() + _token_ttl(token))
        log.info("[abs_auth] Logged in to Audiobookshelf as %s", key[1])
        return token


async def abs_request(
    client: aiohttp.ClientSession,
    creds: Any,
    method: str,
    path: str,
    *,
    params: dict | None = None,
    json_body: Any = None,
    headers: dict | None = None,
    timeout: aiohttp.ClientTimeout | None = None,
    allow_redirects: bool = True,
) -> aiohttp.ClientResponse:
    """One ABS request as this user, retried once with a fresh login on 401.

    ``path`` is relative to the user's ABS base URL. The caller owns (and must
    release) the returned response.
    """
    url = f"{abs_base_url(creds)}{path if path.startswith('/') else '/' + path}"
    for attempt in (1, 2):
        token = await abs_token(client, creds, force_login=attempt == 2)
        req_headers = dict(headers or {})
        req_headers["Authorization"] = f"Bearer {token}"
        resp = await client.request(
            method,
            url,
            params=params,
            json=json_body,
            headers=req_headers,
            timeout=timeout,
            allow_redirects=allow_redirects,
        )
        if resp.status != 401 or attempt == 2 or not can_log_in(creds):
            return resp
        await resp.release()
        forget_token(creds)
        log.info("[abs_auth] Audiobookshelf answered 401 for %s; logging in again", _cache_key(creds)[1] or "api key")
    raise AssertionError("unreachable")

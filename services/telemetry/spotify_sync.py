"""Spotify's recently played onto each linked member's activity feed (Phase 11).

Every ten minutes, for each member who linked their own Spotify (identity's
``LinkedAccount``): refresh the token when it is about to run out, read
``/me/player/recently-played`` after the last play already copied, and record
each new play like a phone media session. A child's plays go through content
monitoring like every other event; the feed stays parents-only for a minor.

Nothing happens until the household's Spotify app is configured
(``spotify_client_id``, ``spotify_client_secret``, ``spotify_redirect_uri``);
a failure for one member is kept on their link (``last_error``, shown to
them) and never stops the others.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import aiohttp

from services.common import spotify
from services.telemetry import guardian, notices, store
from services.telemetry.config import IDENTITY_SVC
from services.telemetry.store import internal_headers

log = logging.getLogger("telemetry.spotify")

SYNC_SECONDS = 600
FIRST_SYNC_DELAY_SECONDS = 60


async def _patch(session, username: str, body: dict[str, Any]) -> None:
    resp = await session.patch(
        f"{IDENTITY_SVC}/api/internal/linked-accounts/spotify/{username}",
        json=body, headers=internal_headers(), timeout=aiohttp.ClientTimeout(total=10.0),
    )
    async with resp:
        if resp.status != 200:
            raise RuntimeError(f"identity answered {resp.status}")


async def _access_token(session, account: dict[str, Any], settings: dict[str, str], now: float) -> str:
    """The account's token, refreshed (and kept) when it runs out within a minute."""
    if account.get("access_token") and int(account.get("expires_at") or 0) > now + 60:
        return str(account["access_token"])
    if not account.get("refresh_token"):
        raise RuntimeError("the link has no refresh token; link Spotify again")
    resp = await session.post(
        spotify.TOKEN_URL,
        data=spotify.refresh_form(str(account["refresh_token"])),
        headers={"Authorization": spotify.basic_auth(settings["spotify_client_id"], settings["spotify_client_secret"])},
        timeout=aiohttp.ClientTimeout(total=15.0),
    )
    async with resp:
        data = await resp.json(content_type=None)
    token = spotify.parse_token(data, now)
    await _patch(session, account["username"], {k: v for k, v in token.items() if k in ("access_token", "expires_at", "refresh_token") and v})
    return token["access_token"]


async def sync_account(rc, session, account: dict[str, Any], settings: dict[str, str], deliver, now: float | None = None) -> int:
    """New plays for one member; returns how many were recorded."""
    username = str(account["username"])
    token = await _access_token(session, account, settings, now if now is not None else time.time())
    cursor = int(account.get("cursor_ms") or 0)
    params = {"limit": "50", **({"after": str(cursor)} if cursor else {})}
    resp = await session.get(
        f"{spotify.API_URL}/me/player/recently-played",
        params=params, headers={"Authorization": f"Bearer {token}"}, timeout=aiohttp.ClientTimeout(total=15.0),
    )
    async with resp:
        if resp.status == 401:
            raise RuntimeError("Spotify refused the link; link Spotify again")
        if resp.status != 200:
            raise RuntimeError(f"Spotify answered {resp.status}")
        data = await resp.json(content_type=None)
    events, new_cursor = spotify.recently_played_events(data, cursor)
    for event in events:
        event = {**event, "user": username}
        await store.append_activity(rc, username, event)
        await guardian.on_activity(rc, session, event, deliver)
    await _patch(session, username, {"cursor_ms": new_cursor, "synced": True, "last_error": None})
    return len(events)


async def sync_once(rc, session, deliver, now: float | None = None) -> int:
    """One pass over every linked member. Not configured: nothing is read."""
    rows = await notices._identity_get(session, "/api/settings")
    settings = {r["key"]: str(r.get("value") or "").strip() for r in rows or [] if isinstance(r, dict) and r.get("key") in spotify.SETTING_KEYS}
    if spotify.missing_settings(settings):
        return 0
    accounts = (await notices._identity_get(session, "/api/internal/linked-accounts/spotify") or {}).get("accounts") or []
    recorded = 0
    for account in accounts:
        try:
            recorded += await sync_account(rc, session, account, settings, deliver, now)
        except Exception as exc:  # one member's failure never stops the others
            log.warning("Spotify sync for %s failed: %s", account.get("username"), exc)
            try:
                await _patch(session, str(account.get("username")), {"last_error": str(exc) or type(exc).__name__})
            except Exception as again:
                log.warning("could not record the Spotify error for %s: %s", account.get("username"), again)
    return recorded


async def sync_loop(redis_factory, session_factory, deliver) -> None:
    await asyncio.sleep(FIRST_SYNC_DELAY_SECONDS)
    rc = redis_factory()
    while True:
        try:
            await sync_once(rc, session_factory(), deliver)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("Spotify sync tick failed: %s", exc)
        await asyncio.sleep(SYNC_SECONDS)

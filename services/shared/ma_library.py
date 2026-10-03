"""Resolve an Audiobookshelf item id to the Music Assistant library URI.

Why this exists
---------------
Music Assistant streams an audiobook itself, through its own Audiobookshelf
provider. It is *not* a generic URL player: handing it an HLS URL (which is
what the gateway proxy produces) makes it store the URL as if it were a track
title — the player then reports ``media_title`` of ``"1?user=...&mt=..."`` and
never plays anything. MA only accepts its own library URI, which is

    library://audiobook/<numeric MA item id>

and that numeric id is *not* the Audiobookshelf UUID. It is only obtainable from
MA's ``music/search`` response, whose items carry ``provider_mappings`` pairing
each library URI back to its upstream ``provider_domain``/``provider_id``. The
Home Assistant service surface strips that field, so the lookup has to go over
MA's own JSON-RPC — see ``docs/MEDIA_UPSTREAM_API_NOTES.md``.

This module is the single resolver for that mapping. Both the HA-entity path in
``services/execution/handlers/audiobookshelf.py`` and the browser's native-MA
path in the gateway use it, so there is exactly one implementation of "which MA
URI is this book?".

Design notes
------------
* Failure is always explicit. A miss returns a message naming the item and the
  search terms — never a guessed URI and never a silent fallback to a raw URL,
  because a fallback reproduces the exact bug this module exists to fix.
* ``websockets`` is used rather than an MA HTTP call because ``music/search``
  is the only command that returns ``provider_mappings``, and MA serves it over
  the WS JSON-RPC socket. (``POST {ma}/api`` works too, but the WS path is the
  one the repo already uses everywhere else, so this stays consistent.)
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass

log = logging.getLogger("shared.ma_library")

# MA closes the socket if the client goes quiet, so keep the same keepalive the
# gateway's ma-jsonrpc proxy uses (gateway/main.py). Passing ping_interval=None
# makes MA hang up immediately.
WS_PING_INTERVAL = 15
WS_PING_TIMEOUT = 10
WS_OPEN_TIMEOUT = 15
WS_CALL_TIMEOUT = 15
# ABS cold-transcode/MA index latency. The HA-side play_media budget is 60s.
CONNECT_ATTEMPTS = 2
CONNECT_RETRY_DELAY = 2.0


class MALibraryLookupError(RuntimeError):
    """A Music Assistant library URI could not be resolved.

    ``reason`` is a short machine-ish tag for callers that want to branch, and
    the message carries the operator-facing detail.
    """

    def __init__(self, message: str, reason: str = "unresolved") -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class ResolvedAudiobook:
    """An ABS item mapped onto MA's library namespace."""

    abs_item_id: str
    ma_uri: str
    title: str


def _ws_base_url(mass_url: str) -> str:
    """Convert an MA base URL into a ws:// JSON-RPC endpoint."""
    base = mass_url.strip().rstrip("/")
    if not base:
        raise MALibraryLookupError(
            "Music Assistant URL is not configured; cannot resolve a library URI.",
            reason="no_mass_url",
        )
    if base.startswith("https://"):
        base = "wss://" + base[len("https://"):]
    elif base.startswith("http://"):
        base = "ws://" + base[len("http://"):]
    if not base.startswith(("ws://", "wss://")):
        raise MALibraryLookupError(
            f"Music Assistant URL '{mass_url}' is not an http(s) or ws(s) URL.",
            reason="bad_mass_url",
        )
    return f"{base}/ws"


def _message_id() -> str:
    return uuid.uuid4().hex


async def _rpc(ws, command: str, args: dict, timeout: float = WS_CALL_TIMEOUT):
    """Send one JSON-RPC command and return its ``result``.

    Raises MALibraryLookupError on any error frame, missing frame, or timeout —
    MA answers with ``{"error_code": n, "error": "..."}`` rather than closing,
    so a caller that ignores errors would otherwise treat the reply as success.
    """
    msg_id = _message_id()
    await ws.send(json.dumps({"message_id": msg_id, "command": command, "args": args}))

    async def _read_result():
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
            if isinstance(raw, (bytes, bytearray)):
                raw = raw.decode("utf-8", "replace")
            try:
                frame = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(frame, dict):
                continue
            # Server-initiated notifications (player_state, server_info, …) carry
            # no message_id; skip them rather than mistaking one for our reply.
            if frame.get("message_id") != msg_id:
                continue
            if frame.get("error_code"):
                raise MALibraryLookupError(
                    f"Music Assistant rejected '{command}' "
                    f"(error_code {frame['error_code']}): {frame.get('error')}",
                    reason=f"ma_error_{frame['error_code']}",
                )
            return frame.get("result")

    return await _read_result()


def _iter_candidates(payload) -> list[dict]:
    """Flatten every item dict in a music/search payload.

    MA keys results by media type (``tracks``, ``albums``, ``audiobooks``, …).
    Only ``audiobooks``/``podcast_episodes`` can ever carry an
    ``audiobookshelf`` provider mapping, but walking the whole payload costs
    nothing and avoids silently missing a renamed group.
    """
    items: list[dict] = []
    if not isinstance(payload, dict):
        return items
    for value in payload.values():
        if isinstance(value, list):
            items.extend(v for v in value if isinstance(v, dict))
        elif isinstance(value, dict):
            # Some commands nest a single result object.
            items.append(value)
    return items


def _match_by_abs_id(items: list[dict], abs_item_id: str) -> dict | None:
    """The first item MA reports as backed by *this* Audiobookshelf item.

    A blank ``abs_item_id`` (podcast episode) matches the first available
    audiobookshelf-backed item, so MA's availability check still applies.
    """
    target = (abs_item_id or "").strip().lower()
    for item in items:
        mappings = item.get("provider_mappings")
        if not isinstance(mappings, list):
            continue
        for mapping in mappings:
            if not isinstance(mapping, dict):
                continue
            if str(mapping.get("provider_domain", "")).strip().lower() != "audiobookshelf":
                continue
            if target:
                if str(mapping.get("item_id", "")).strip().lower() != target:
                    continue
            elif mapping.get("available") is not True:
                # No id to match on, so only take an item MA confirms it can
                # actually fetch rather than whichever result sorted first.
                continue
            if mapping.get("available") is False:
                # Present in MA's index but not currently playable. Skip so a
                # later healthy candidate can win, and so we never hand MA a URI
                # for media it cannot fetch.
                continue
            return item
    return None


async def _resolve_once(
    mass_url: str,
    mass_token: str,
    abs_item_id: str,
    title: str,
    limit: int,
    allow_idless: bool,
) -> ResolvedAudiobook:
    from websockets.asyncio.client import connect

    ws_url = _ws_base_url(mass_url)
    # MA authenticates from the query token *and* an explicit auth frame; the
    # frame alone is enough but the query param is what the gateway proxy sends
    # and costs nothing.
    sep = "&" if "?" in ws_url else "?"
    async with connect(
        f"{ws_url}{sep}token={mass_token}",
        ping_interval=WS_PING_INTERVAL,
        ping_timeout=WS_PING_TIMEOUT,
        open_timeout=WS_OPEN_TIMEOUT,
    ) as ws:
        await _rpc(ws, "auth", {"token": mass_token})
        result = await _rpc(
            ws,
            "music/search",
            {
                "search_query": title,
                "limit": limit,
                "config": {"providers": ["library"]},
            },
        )

    items = _iter_candidates(result)
    match = _match_by_abs_id(items, abs_item_id)
    if match is None:
        subject = f"audiobookshelf item {abs_item_id}" if abs_item_id else f"'{title}'"
        raise MALibraryLookupError(
            f"Music Assistant does not have {subject} in its library "
            f"(searched {title!r}, {len(items)} result(s)). "
            "Add the library to Music Assistant, or pick the book from the "
            "Media Assistant library list so the right item is used.",
            reason="not_in_ma_library",
        )
    ma_uri = (match.get("uri") or "").strip()
    if not ma_uri.startswith("library://"):
        raise MALibraryLookupError(
            f"Music Assistant returned non-library URI {ma_uri!r} for "
            f"audiobookshelf item {abs_item_id}; refusing to play it.",
            reason="unexpected_uri",
        )
    return ResolvedAudiobook(
        abs_item_id=abs_item_id,
        ma_uri=ma_uri,
        title=(match.get("name") or title or "").strip(),
    )


async def resolve_audiobook_uri(
    mass_url: str,
    mass_token: str,
    abs_item_id: str,
    title: str,
    *,
    limit: int = 10,
    allow_idless: bool = False,
) -> ResolvedAudiobook:
    """Map an Audiobookshelf item id to MA's ``library://audiobook/<n>`` URI.

    ``allow_idless=True`` handles podcast episodes, which have no ABS *library
    item* id (the episode id is not an item id) so there is nothing to match on;
    it then takes the first result MA reports for the search, still requiring MA
    to confirm it is backed by an available audiobookshelf item.

    Raises MALibraryLookupError when MA is unreachable, not configured, or does
    not know the item. Never returns a guessed or raw-URL fallback.
    """
    if not mass_token:
        raise MALibraryLookupError(
            "Music Assistant token is not configured; cannot resolve a library URI.",
            reason="no_mass_token",
        )
    abs_item_id = (abs_item_id or "").strip()
    if not abs_item_id and not allow_idless:
        raise MALibraryLookupError(
            "An Audiobookshelf item id is required to resolve a Music Assistant URI.",
            reason="no_abs_item_id",
        )
    # A blank title makes MA's search match nothing; fall back to the id, which
    # MA also indexes for audiobookshelf items.
    query = (title or "").strip() or abs_item_id
    if not query:
        raise MALibraryLookupError(
            "A title or item id is required to search the Music Assistant library.",
            reason="no_search_query",
        )

    last_error: Exception | None = None
    for attempt in range(1, CONNECT_ATTEMPTS + 1):
        try:
            return await _resolve_once(
                mass_url, mass_token, abs_item_id, query, limit, allow_idless
            )
        except MALibraryLookupError:
            # A *lookup* miss will not fix itself on retry. Only transport-level
            # failures are worth a second attempt.
            raise
        except Exception as exc:  # noqa: BLE001 - reported, never swallowed
            last_error = exc
            log.warning(
                "[ma_library] attempt %s/%s failed for %s: %s",
                attempt, CONNECT_ATTEMPTS, abs_item_id or query, exc,
            )
            if attempt < CONNECT_ATTEMPTS:
                await asyncio.sleep(CONNECT_RETRY_DELAY)

    raise MALibraryLookupError(
        f"Could not reach Music Assistant to resolve {abs_item_id or query!r}: {last_error}",
        reason="mass_unreachable",
    ) from last_error
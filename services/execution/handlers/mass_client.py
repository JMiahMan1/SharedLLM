"""Music Assistant client for direct MA service calls (REST for lists, WebSocket JSON-RPC for search)."""
import asyncio
import json
import logging
import uuid
from contextlib import asynccontextmanager
from typing import Any

import aiohttp

from services.execution.http_client import get_session, host_of

log = logging.getLogger(__name__)


class MASearchError(Exception):
    """Direct Music Assistant search failed; callers should fall back (HA proxy)."""


@asynccontextmanager
async def _mass_session(mass_url: str, verify: bool = False):
    """Yield the pooled MA session WITHOUT closing it (reused across calls)."""
    yield await get_session(host_of(mass_url), verify=verify)


async def _ma_api(mass_url: str, mass_token: str, command: str, params: dict[str, Any] | None = None) -> Any:
    """Call MA REST API with JWT auth and return the items from the response."""
    if not mass_url or not mass_token:
        return []

    # Normalize URL - ensure it ends without trailing slash, always use /api for MA v2
    base = mass_url.rstrip("/")
    from urllib.parse import urlparse
    parsed = urlparse(base)
    base_url = f"{parsed.scheme}://{parsed.hostname}"
    if parsed.port:
        base_url = f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"

    # MA v2 REST API requires /api suffix and message_id in JSON-RPC payload
    urls_to_try = [f"{base_url}/api"]

    payload = {
        "message_id": uuid.uuid4().hex,
        "command": command,
        "args": params or {}
    }

    for url in urls_to_try:
        try:
            async with _mass_session(mass_url) as client, client.post(
                url,
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {mass_token}",
                },
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    # music/search returns a dict of media_type -> [items]
                    if isinstance(data, dict) and command == "music/search":
                        return data
                    if isinstance(data, dict):
                        result = data.get("result", data.get("items", []))
                        if isinstance(result, list):
                            return result
                        # Some commands return data directly in a key
                        for key in ("playlists", "items", "data"):
                            if key in data and isinstance(data[key], list):
                                return data[key]
                    elif isinstance(data, list):
                        return data
                    else:
                        log.warning(f"[mass] MA API returned unexpected shape for {command} on {url}")
                else:
                    log.warning(f"[mass] MA API returned {resp.status} for {command} on {url}: {(await resp.text())[:200]}")
        except Exception as e:
            log.debug(f"[mass] MA API call to {url} failed: {e}")
            continue

    return []


async def get_playlists(mass_url: str, mass_token: str) -> list[dict[str, Any]]:
    """Get Music Assistant playlists via REST API."""
    try:
        raw = await _ma_api(mass_url, mass_token, "music/playlists/library_items")
        return [
            {
                "name": item.get("name", ""),
                "items": item.get("num_tracks", item.get("track_count", 0)),
                "uri": item.get("uri", ""),
                "type": item.get("media_type", "playlist"),
            }
            for item in raw
        ]
    except Exception as e:
        log.error(f"[mass] Failed to get playlists: {e}")
        return []


async def get_recent(mass_url: str, mass_token: str) -> list[dict[str, Any]]:
    """Get Music Assistant recently played items via REST API."""
    try:
        raw = await _ma_api(mass_url, mass_token, "music/recently_played_items")
        return [
            {
                "name": mi.get("name", ""),
                "artist": mi.get("artist", (mi.get("artists", [{}])[0].get("name", "") if mi.get("artists") else "")),
                "uri": mi.get("uri", ""),
                "last_played": mi.get("timestamp_played", mi.get("added_at", mi.get("last_played", mi.get("timestamp", "")))),
                "image": mi.get("image", {}).get("path", "") if isinstance(mi.get("image"), dict) else (mi.get("image", "") or ""),
                "type": mi.get("media_type", "track"),
            }
            for item in raw
            for mi in [item.get("media_item") or item]
        ]
    except Exception as e:
        log.error(f"[mass] Failed to get recent: {e}")
        return []


async def search(
    mass_url: str,
    mass_token: str,
    query: str,
    limit: int = 20,
    media_types: list[str] | None = None,
    artist: str = "",
    album: str = "",
    library_only: bool = True,
) -> list[dict[str, Any]]:
    """Search Music Assistant via the MA WebSocket JSON-RPC API using the MA token.

    The MA REST `/api` search endpoint is unreliable in MA 2.9.x (returns
    "Internal server error"), and the search command requires the `search_query`
    argument plus a `config.providers` filter (searching all providers hangs).
    The WebSocket JSON-RPC path is the real MA API and works correctly.

    Raises MASearchError on any failure (connection, timeout, MA error_code)
    so callers can fall back to the HA proxy; failures must never look like
    an empty result set (BUG-17).
    """
    if not mass_url or not mass_token or not query:
        return []
    try:
        from urllib.parse import urlparse
        parsed = urlparse(mass_url.rstrip("/"))
        host = parsed.hostname
        port = parsed.port or 8095
        if not host:
            raise MASearchError(f"Invalid mass_url (no host): {mass_url!r}")
        ws_url = f"ws://{host}:{port}/ws?token={mass_token}"

        args: dict[str, Any] = {
            "search_query": query,
            "limit": limit,
        }
        if library_only:
            # library_only=False leaves MA's own provider defaults in place;
            # explicitly listing every provider hangs (see docstring above).
            args["config"] = {"providers": ["library"]}
        if media_types:
            args["media_type"] = [mt.lower() for mt in media_types]

        results: list[dict[str, Any]] = []
        async with _mass_session(mass_url) as session, session.ws_connect(ws_url, heartbeat=15, timeout=aiohttp.ClientTimeout(total=25)) as ws:
            # server/hello
            await ws.receive_str()
            # authenticate
            await ws.send_str(json.dumps({"message_id": "auth", "command": "auth", "args": {"token": mass_token}}))
            await ws.receive_str()
            # search
            await ws.send_str(json.dumps({"message_id": "mass_search", "command": "music/search", "args": args}))
            try:
                while True:
                    msg = await asyncio.wait_for(ws.receive(), timeout=20)
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        data = json.loads(msg.data)
                        if data.get("message_id") != "mass_search":
                            continue
                        if data.get("error_code"):
                            raise MASearchError(
                                f"MA search error {data.get('error_code')}: {data.get('details')}"
                            )
                        raw = data.get("result", {})
                        items: list[dict[str, Any]] = []
                        if isinstance(raw, dict):
                            for v in raw.values():
                                if isinstance(v, list):
                                    items.extend(v)
                        elif isinstance(raw, list):
                            items = raw
                        for item in items:
                            if not isinstance(item, dict):
                                continue
                            item_artists = item.get("artists") or []
                            item_artist = item_artists[0].get("name", "") if item_artists and isinstance(item_artists[0], dict) else (item.get("artist", "") or "")
                            item_album = item.get("album")
                            album_name = item_album.get("name", "") if isinstance(item_album, dict) else (item_album or "")
                            image = item.get("image")
                            image_path = image.get("path", "") if isinstance(image, dict) else (image or "")
                            results.append({
                                "name": item.get("name", ""),
                                "uri": item.get("uri", ""),
                                "type": item.get("media_type", "track"),
                                "artist": item_artist,
                                "album": album_name,
                                "duration": item.get("duration", 0),
                                "image": image_path,
                            })
                        break
                    elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        raise MASearchError("MA search socket closed before a result arrived")
            except TimeoutError as e:
                raise MASearchError("MA search timed out") from e
        if artist:
            needle = artist.casefold()
            results = [r for r in results if needle in (r.get("artist") or "").casefold()]
        if album:
            needle = album.casefold()
            results = [r for r in results if needle in (r.get("album") or "").casefold()]
        return results[:limit]
    except MASearchError:
        raise
    except Exception as e:
        raise MASearchError(f"MA search failed: {e}") from e



# --------------------------------------------------------------------------- #
# Direct player control, used when Home Assistant is down.                    #
#                                                                             #
# Normally speakers are driven through HA's music_assistant integration       #
# (music_assistant.play_media / play_announcement). Music Assistant is its own #
# server, so when HA is unreachable the same work can go straight to MA's     #
# JSON-RPC `/api` - the endpoint the gateway already uses for players/all and  #
# player commands.                                                             #
# --------------------------------------------------------------------------- #


class MACommandError(Exception):
    """A direct Music Assistant command failed (connection, HTTP or MA error)."""


def _api_url(mass_url: str) -> str:
    from urllib.parse import urlparse

    parsed = urlparse(mass_url.rstrip("/"))
    if not parsed.hostname:
        raise MACommandError(f"Invalid mass_url (no host): {mass_url!r}")
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme or 'http'}://{parsed.hostname}{port}/api"


async def ma_command(
    mass_url: str, mass_token: str, command: str, args: dict[str, Any] | None = None, *, timeout: float = 15.0
) -> Any:
    """Send one command to MA's JSON-RPC `/api` and return its result. Raises MACommandError.

    Unlike ``_ma_api`` (which returns [] on any failure, fine for lists), a
    command's caller has to know whether it worked: "playing" must not be said
    about a speaker that stayed silent.
    """
    if not mass_url or not mass_token:
        raise MACommandError("Music Assistant is not configured")
    payload: dict[str, Any] = {"message_id": uuid.uuid4().hex, "command": command}
    if args:
        payload["args"] = args
    try:
        async with _mass_session(mass_url) as client, client.post(
            _api_url(mass_url),
            json=payload,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {mass_token}"},
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            text = await resp.text()
            if resp.status != 200:
                raise MACommandError(f"Music Assistant returned HTTP {resp.status} for {command}: {text[:200]}")
            data = json.loads(text) if text.strip() else None
    except MACommandError:
        raise
    except Exception as e:
        raise MACommandError(f"Music Assistant {command} failed: {type(e).__name__}: {e}") from e
    if isinstance(data, dict) and data.get("error_code"):
        raise MACommandError(f"Music Assistant {command} error {data.get('error_code')}: {data.get('details')}")
    if isinstance(data, dict) and "result" in data:
        return data["result"]
    return data


async def get_players(mass_url: str, mass_token: str) -> list[dict[str, Any]]:
    result = await ma_command(mass_url, mass_token, "players/all")
    return [p for p in (result or []) if isinstance(p, dict) and p.get("player_id")]


def _slug(text: str) -> str:
    import re

    return re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")


def match_player(players: list[dict[str, Any]], entity_id: str, friendly_name: str = "") -> dict[str, Any] | None:
    """The MA player behind an HA ``media_player`` entity, without asking HA.

    HA's music_assistant integration names its entities after the MA player
    (``media_player.<slug of the player name>``, with ``_2``-style suffixes on
    collisions), so the slug is the join key. Exact slug first, then the slug
    without a numeric suffix, then a name that contains the entity's words.
    Unavailable players are skipped: they cannot play.
    """
    object_id = entity_id.split(".", 1)[-1].lower()
    import re

    base = re.sub(r"_\d+$", "", object_id)
    wanted = {object_id, base, _slug(friendly_name)} - {""}
    live = [p for p in players if p.get("available", True)]
    for candidates in (
        [p for p in live if _slug(p.get("display_name") or p.get("name") or "") in wanted],
        [p for p in live if p.get("player_id", "").lower() in wanted],
        [p for p in live if base and base in _slug(p.get("display_name") or p.get("name") or "")],
    ):
        if candidates:
            return candidates[0]
    return None


async def play_media(mass_url: str, mass_token: str, player_id: str, uri: str, option: str = "replace") -> None:
    await ma_command(
        mass_url, mass_token, "player_queues/play_media",
        {"queue_id": player_id, "media": uri, "option": option or "replace"},
    )


async def play_announcement(
    mass_url: str, mass_token: str, player_id: str, url: str, volume_level: int | None = None
) -> None:
    args: dict[str, Any] = {"player_id": player_id, "url": url}
    if volume_level is not None:
        args["volume_level"] = volume_level
    await ma_command(mass_url, mass_token, "players/play_announcement", args, timeout=30.0)


#: Same preference the HA path uses when a search returns several kinds of hit.
_PLAY_ORDER = ("track", "album", "artist", "playlist", "radio")


def best_uri(results: list[dict[str, Any]]) -> tuple[str, str] | None:
    """(uri, media_type) of the hit to play, in the HA path's category order."""
    for kind in _PLAY_ORDER:
        for item in results:
            if item.get("type") == kind and item.get("uri"):
                return item["uri"], kind
    return None

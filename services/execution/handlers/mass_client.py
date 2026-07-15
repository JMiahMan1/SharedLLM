"""Music Assistant client for direct MA service calls (REST for lists, WebSocket JSON-RPC for search)."""
import asyncio
import json
import logging
import uuid
from contextlib import asynccontextmanager
from typing import Any

import aiohttp

log = logging.getLogger(__name__)

from services.execution.http_client import get_session, host_of


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
            }
            for item in raw
            for mi in [item.get("media_item") or item]
        ]
    except Exception as e:
        log.error(f"[mass] Failed to get recent: {e}")
        return []


async def search(mass_url: str, mass_token: str, query: str, limit: int = 20, media_types: list[str] | None = None) -> list[dict[str, Any]]:
    """Search Music Assistant via the MA WebSocket JSON-RPC API using the MA token.

    The MA REST `/api` search endpoint is unreliable in MA 2.9.x (returns
    "Internal server error"), and the search command requires the `search_query`
    argument plus a `config.providers` filter (searching all providers hangs).
    The WebSocket JSON-RPC path is the real MA API and works correctly.
    """
    if not mass_url or not mass_token or not query:
        return []
    try:
        from urllib.parse import urlparse
        parsed = urlparse(mass_url.rstrip("/"))
        host = parsed.hostname
        port = parsed.port or 8095
        if not host:
            return []
        ws_url = f"ws://{host}:{port}/ws?token={mass_token}"

        args: dict[str, Any] = {
            "search_query": query,
            "limit": limit,
            "config": {"providers": ["library"]},
        }
        if media_types:
            args["media_type"] = [mt.lower() for mt in media_types]

        results: list[dict[str, Any]] = []
        async with _mass_session(mass_url) as session:
            async with session.ws_connect(ws_url, heartbeat=15, timeout=aiohttp.ClientTimeout(total=25)) as ws:
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
                                log.warning(f"[mass] MA search error {data.get('error_code')}: {data.get('details')}")
                                break
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
                                artists = item.get("artists") or []
                                artist = artists[0].get("name", "") if artists and isinstance(artists[0], dict) else (item.get("artist", "") or "")
                                album = item.get("album")
                                album_name = album.get("name", "") if isinstance(album, dict) else (album or "")
                                image = item.get("image")
                                image_path = image.get("path", "") if isinstance(image, dict) else (image or "")
                                results.append({
                                    "name": item.get("name", ""),
                                    "uri": item.get("uri", ""),
                                    "type": item.get("media_type", "track"),
                                    "artist": artist,
                                    "album": album_name,
                                    "duration": item.get("duration", 0),
                                    "image": image_path,
                                })
                            break
                        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            break
                except TimeoutError:
                    log.warning("[mass] MA search timed out")
        return results[:limit]
    except Exception as e:
        log.error(f"[mass] Failed to search via MA websocket: {e}")
        return []


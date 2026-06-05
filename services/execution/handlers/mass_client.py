"""Music Assistant REST API client for direct MA service calls."""
import logging
from typing import List, Dict, Any
import httpx

log = logging.getLogger(__name__)


async def _ma_api(mass_url: str, mass_token: str, command: str, params: Dict[str, Any] | None = None) -> List[Dict[str, Any]]:
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
    
    # MA v2 REST API requires /api suffix
    urls_to_try = [f"{base_url}/api"]

    payload = {
        "command": command,
        "args": params or {}
    }

    for url in urls_to_try:
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    url,
                    json=payload,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {mass_token}",
                    }
                )
                if resp.status_code == 200:
                    data = resp.json()
                    # MA API returns {"result": [...]} for list commands
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
                    log.warning(f"[mass] MA API returned {resp.status_code} for {command} on {url}: {resp.text[:200]}")
        except Exception as e:
            log.debug(f"[mass] MA API call to {url} failed: {e}")
            continue

    return []


async def get_playlists(mass_url: str, mass_token: str) -> List[Dict[str, Any]]:
    """Get Music Assistant playlists via REST API."""
    try:
        raw = await _ma_api(mass_url, mass_token, "playlists/list_playlists")
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


async def get_recent(mass_url: str, mass_token: str) -> List[Dict[str, Any]]:
    """Get Music Assistant recently played items via REST API."""
    try:
        raw = await _ma_api(mass_url, mass_token, "recently_played/list")
        return [
            {
                "name": mi.get("name", item.get("name", "")) if mi else item.get("name", ""),
                "artist": (mi.get("artists", [{}])[0].get("name", "") if mi.get("artists") else "") if mi else item.get("artist", ""),
                "uri": mi.get("uri", item.get("uri", "")) if mi else item.get("uri", ""),
                "last_played": item.get("added_at", item.get("last_played", item.get("timestamp", ""))),
            }
            for item in raw
            for mi in [item.get("media_item") or item]
        ]
    except Exception as e:
        log.error(f"[mass] Failed to get recent: {e}")
        return []

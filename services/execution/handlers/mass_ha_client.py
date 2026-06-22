"""Music Assistant proxy client via Home Assistant services.

The HA integration exposes domain-level services:
- music_assistant.search - search MA for media items
- music_assistant.get_library - get library content (playlists, tracks, etc.)
- music_assistant.get_queue - get queue state (entity-specific)

These use HA's existing MA connection, avoiding direct MA REST API auth issues.
"""
import logging
import httpx
from typing import List, Dict, Any

log = logging.getLogger("execution.mass_ha")

_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


async def _call_ha_ma_service(
    ha_url: str,
    ha_token: str,
    service: str,
    service_data: dict | None = None,
    entity_id: str = "",
) -> dict | None:
    """Call a music_assistant domain service via HA API with return_response."""
    if not ha_url or not ha_token:
        log.error("[mass_ha] HA URL or token not configured")
        return None

    headers = {"Authorization": f"Bearer {ha_token}", "Content-Type": "application/json"}
    url = f"{ha_url.rstrip('/')}/api/services/music_assistant/{service}?return_response"
    
    payload = {}
    if entity_id:
        payload["entity_id"] = entity_id
    if service_data:
        payload.update(service_data)

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, verify=False) as client:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code == 200:
                return resp.json()
            log.error(f"[mass_ha] Service {service} returned {resp.status_code}: {resp.text[:300]}")
            return None
    except Exception as e:
        log.error(f"[mass_ha] Service {service} failed: {e}")
        return None


async def search(
    ha_url: str,
    ha_token: str,
    query: str,
    mass_entry_id: str = "",
    media_types: List[str] | None = None,
    limit: int = 10,
    artist: str = "",
    album: str = "",
) -> List[Dict[str, Any]]:
    """Search MA for media items via HA proxy.
    
    Args:
        query: Search query string
        mass_entry_id: MA config entry ID from HA
        media_types: List of MediaType strings (TRACK, ALBUM, ARTIST, PLAYLIST, etc.) or None for ALL
        limit: Max results to return
        artist: Artist name to refine search
        album: Album name to refine search
    
    Returns:
        List of media items with name, uri, type fields
    """
    if not ha_url or not ha_token:
        return []

    service_data: dict[str, Any] = {
        "name": query,
        "limit": limit,
    }
    
    if mass_entry_id:
        service_data["config_entry_id"] = mass_entry_id
    
    if media_types:
        # MA HA integration expects lowercase media type strings as a list
        service_data["media_type"] = [mt.lower() for mt in media_types] if len(media_types) > 1 else [media_types[0].lower()]
    else:
        service_data["media_type"] = ["track", "artist", "album", "playlist", "radio"]
    
    if artist:
        service_data["artist"] = artist
    if album:
        service_data["album"] = album

    result = await _call_ha_ma_service(ha_url, ha_token, "search", service_data)
    if not result:
        return []

    log.info(f"[mass_ha] raw search response: {result}")

    # HA response format: {"changed_states": [], "service_response": {artists: [...], albums: [...], ...}}
    # Or sometimes {"results": [...]} at top level
    items = []
    results = []
    if result:
        results = result.get("results", [])
        if not results:
            # Check nested response keys
            for key in ("service_response", "response"):
                inner = result.get(key, {})
                if isinstance(inner, dict):
                    results = inner.get("results", [])
                    if results:
                        break
                elif isinstance(inner, list):
                    results = inner
                    break
    if isinstance(results, dict):
        for media_type, items_list in results.items():
            if isinstance(items_list, list):
                for item in items_list:
                    items.append({
                        "name": item.get("name", ""),
                        "uri": item.get("uri", ""),
                        "type": media_type,
                        "artist": item.get("artists", [{}])[0].get("name", "") if item.get("artists") else item.get("artist", ""),
                        "duration": item.get("duration", 0),
                    })
    elif isinstance(results, list):
        for item in results:
            items.append({
                "name": item.get("name", ""),
                "uri": item.get("uri", ""),
                "type": item.get("media_type", "unknown"),
                "artist": item.get("artists", [{}])[0].get("name", "") if item.get("artists") else item.get("artist", ""),
                "duration": item.get("duration", 0),
            })
    
    return items[:limit]


async def get_library(
    ha_url: str,
    ha_token: str,
    media_type: str,
    limit: int = 50,
    offset: int = 0,
    favorite: bool = False,
    search: str = "",
    order_by: str = "",
) -> List[Dict[str, Any]]:
    """Get library content from MA via HA proxy.
    
    Args:
        media_type: MediaType string - PLAYLIST, TRACK, ALBUM, ARTIST, RADIO
        limit: Max results
        offset: Pagination offset
        favorite: Only return favorites
        search: Filter by search term
        order_by: Sort order
    
    Returns:
        List of library items
    """
    if not ha_url or not ha_token:
        return []

    service_data = {
        "media_type": media_type,
        "limit": limit,
        "offset": offset,
    }
    
    if favorite:
        service_data["favorite"] = favorite
    if search:
        service_data["search"] = search
    if order_by:
        service_data["order_by"] = order_by

    result = await _call_ha_ma_service(ha_url, ha_token, "get_library", service_data)
    if not result:
        return []

    # MA get_library returns {"playlists": [...]} or {"tracks": [...]} etc.
    items = []
    # Look for the key matching the media_type pluralized
    type_keys = {
        "PLAYLIST": "playlists",
        "PLAYLISTS": "playlists",
        "TRACK": "tracks",
        "TRACKS": "tracks",
        "ALBUM": "albums",
        "ALBUMS": "albums",
        "ARTIST": "artists",
        "ARTISTS": "artists",
        "RADIO": "radios",
        "RADIOS": "radios",
    }
    key = type_keys.get(media_type, f"{media_type.lower()}s")
    items_list = result.get(key, result.get("items", []))
    if isinstance(items_list, list):
        for item in items_list:
            items.append({
                "name": item.get("name", ""),
                "uri": item.get("uri", ""),
                "type": media_type,
                "duration": item.get("duration", 0),
                "num_tracks": item.get("num_tracks", item.get("track_count", 0)),
            })
    
    return items


async def get_queue(
    ha_url: str,
    ha_token: str,
    entity_id: str,
) -> Dict[str, Any]:
    """Get active queue for an MA player entity.
    
    Args:
        entity_id: HA media_player entity_id (e.g., 'media_player.office_speaker')
    
    Returns:
        Queue dict with items, current_item, state, etc.
    """
    if not ha_url or not ha_token:
        return {}

    result = await _call_ha_ma_service(ha_url, ha_token, "get_queue", entity_id=entity_id)
    if not result:
        return {}

    # Flatten queue data with media item details
    queue = {
        "queue_id": result.get("queue_id", ""),
        "state": result.get("state", "idle"),
        "shuffle_enabled": result.get("shuffle_enabled", False),
        "repeat_mode": result.get("repeat_mode", "off"),
        "current_index": result.get("current_index", -1),
        "elapsed_time": result.get("elapsed_time", 0),
        "elapsed_time_last_updated": result.get("elapsed_time_last_updated", 0),
    }

    # Extract current item details
    cur = result.get("current_item", {})
    media_item = cur.get("media_item") or cur
    if media_item:
        queue["current_item"] = {
            "name": media_item.get("name", ""),
            "uri": media_item.get("uri", ""),
            "media_type": media_item.get("media_type", ""),
            "duration": media_item.get("duration", 0),
            "artist": media_item.get("artists", [{}])[0].get("name", "") if media_item.get("artists") else media_item.get("artist", ""),
            "album": media_item.get("album", {}).get("name", "") if media_item.get("album") else "",
        }

    # Extract all queue items
    queue_items = result.get("items", [])
    queue["items"] = []
    for idx, qi in enumerate(queue_items):
        mi = qi.get("media_item") or qi
        if mi:
            queue["items"].append({
                "index": idx,
                "name": mi.get("name", ""),
                "uri": mi.get("uri", ""),
                "media_type": mi.get("media_type", ""),
                "duration": mi.get("duration", 0),
                "artist": mi.get("artists", [{}])[0].get("name", "") if mi.get("artists") else mi.get("artist", ""),
            })

    return queue


async def play_media(
    ha_url: str,
    ha_token: str,
    entity_id: str,
    media_id: str,
    media_type: str = "url",
    enqueue: str = "replace",
) -> dict:
    """Play media on an MA player via HA.
    
    Args:
        entity_id: HA media_player entity_id
        media_id: URI or search query to play
        media_type: content type
        enqueue: play mode (replace, next, add, etc.)
    
    Returns:
        Service call result
    """
    if not ha_url or not ha_token:
        return {"ok": False, "error": "HA credentials not configured"}

    payload = {
        "entity_id": entity_id,
        "media_content_id": media_id,
        "media_content_type": media_type,
    }

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, verify=False) as client:
            headers = {"Authorization": f"Bearer {ha_token}", "Content-Type": "application/json"}
            url = f"{ha_url.rstrip('/')}/api/services/media_player/play_media"
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code == 200:
                return {"ok": True, "message": f"Playing on {entity_id}"}
            return {"ok": False, "error": f"HA returned {resp.status_code}", "status_code": resp.status_code}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def get_ma_players(ha_url: str, ha_token: str) -> List[Dict[str, Any]]:
    """Get all MA player entities from HA.
    
    Returns:
        List of player info dicts
    """
    if not ha_url or not ha_token:
        return []

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, verify=False) as client:
            headers = {"Authorization": f"Bearer {ha_token}"}
            resp = await client.get(f"{ha_url.rstrip('/')}/api/states", headers=headers)
            if resp.status_code != 200:
                return []
            
            players = []
            for state in resp.json():
                eid = state.get("entity_id", "")
                attrs = state.get("attributes", {})
                if attrs.get("app_id") == "music_assistant" or attrs.get("mass_player_type"):
                    players.append({
                        "entity_id": eid,
                        "friendly_name": attrs.get("friendly_name", ""),
                        "state": state.get("state", "unknown"),
                        "volume_level": attrs.get("volume_level"),
                        "is_volume_muted": attrs.get("is_volume_muted", False),
                        "media_title": attrs.get("media_title", ""),
                        "media_artist": attrs.get("media_artist", ""),
                        "app_id": attrs.get("app_id", ""),
                        "mass_player_type": attrs.get("mass_player_type"),
                        "active_queue": attrs.get("active_queue"),
                        "supported_features": attrs.get("supported_features", 0),
                    })
            return players
    except Exception as e:
        log.error(f"[mass_ha] Failed to get MA players: {e}")
        return []


async def get_recently_played(
    ha_url: str,
    ha_token: str,
    limit: int = 10,
) -> List[Dict[str, Any]]:
    """Get recently played items from MA via HA proxy.
    
    Uses get_library with TRACK type and library_only=False to get recently played.
    MA tracks are ordered by recently_played rank when no order_by specified.
    """
    return await get_library(ha_url, ha_token, "TRACK", limit=limit)


"""
Music Assistant browsing tools for playlists, podcasts, and media library.
"""
import logging
import requests
from typing import Dict, List, Optional
from app.settings import GlobalResources, log

async def _get_ma_player() -> str:
    """Finds a valid Music Assistant player from the DB."""
    try:
        # Search for any device with integration 'music_assistant'
        # Since we don't have a specific query, we browse? 
        # Or query for "mass"?
        docs = GlobalResources.ha_collection.similarity_search("mass", k=10)
        for d in docs:
            if d.metadata.get("integration") == "music_assistant":
                return d.metadata.get("entity_id")
    except Exception:
        pass
    return "media_player.mass" # Fallback

async def browse_music_library(entity_id: str, user_creds: dict, media_type: str = "playlist") -> dict:
    """
    Browse Music Assistant library to list playlists, artists, albums, etc.
    """
    ha_url = user_creds.get("ha_url")
    token = user_creds.get("ha_token")
    
    if not ha_url or not token:
        return {"status": "FAILURE", "message": "Missing HA credentials"}
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    
    try:
        if not entity_id: entity_id = await _get_ma_player()
        
        # Call browse_media service
        service_url = f"{ha_url}/api/services/media_player/browse_media"
        payload = {
            "entity_id": entity_id,
            "media_content_type": "library",
            "media_content_id": f"library://{media_type}"
        }
        
        log.info(f"[MA BROWSE] Browsing {media_type} on {entity_id}")
        response = requests.post(service_url, json=payload, headers=headers, timeout=10)
        
        if response.status_code != 200:
            log.error(f"[MA BROWSE] Failed: {response.status_code} - {response.text}")
            return {
                "status": "FAILURE",
                "message": f"Failed to browse library: {response.status_code}"
            }
        
        # Parse browse response
        data = response.json()
        items = []
        
        # Navigate the media browser response
        if "children" in data:
            for child in data["children"]:
                items.append({
                    "title": child.get("title", "Unknown"),
                    "media_content_id": child.get("media_content_id"),
                    "media_content_type": child.get("media_content_type"),
                    "can_play": child.get("can_play", False)
                })
        
        log.info(f"[MA BROWSE] Found {len(items)} {media_type}s")
        return {
            "status": "SUCCESS",
            "items": items,
            "count": len(items),
            "media_type": media_type
        }
    
    except Exception as e:
        log.error(f"[MA BROWSE] Error: {e}")
        return {
            "status": "FAILURE",
            "message": f"Error browsing library: {str(e)}"
        }


async def tool_list_playlists(query: str, user_creds: dict, redis_client=None) -> dict:
    """List available playlists."""
    ma_entity = await _get_ma_player()
    result = await browse_music_library(ma_entity, user_creds, media_type="playlist")
    return _format_list_result(result, "playlists")

async def tool_list_radio(query: str, user_creds: dict, redis_client=None) -> dict:
    """List available radio stations."""
    ma_entity = await _get_ma_player()
    result = await browse_music_library(ma_entity, user_creds, media_type="radio")
    return _format_list_result(result, "radio stations")

def _format_list_result(result: dict, type_name: str) -> dict:
    if result["status"] == "SUCCESS":
        items = result["items"]
        if items:
            names = [item["title"] for item in items[:25]]
            message = f"Found {result['count']} {type_name}:\n" + "\n".join(f"- {name}" for name in names)
            if result['count'] > 25: message += f"\n... ({result['count']-25} more)"
        else:
            message = f"No {type_name} found."
        
        return {"status": "SUCCESS", "message": message, "items": items, "service": f"list_{type_name}"}
    return result

async def tool_music_search(query: str, user_creds: dict, redis_client=None) -> dict:
    """
    Search Music Assistant library for Tracks, Artists, Albums matching the query.
    """
    ma_entity = await _get_ma_player()
    q_low = query.lower()
    
    results = []
    
    import difflib
    
    # Browse Artist, Album, Track in parallel? (For now sequential)
    for mtype in ["artist", "album", "track", "radio", "podcast"]:
        res = await browse_music_library(ma_entity, user_creds, media_type=mtype)
        if res["status"] == "SUCCESS":
            for item in res["items"]:
                # 1. Exact/Substring match (Fast)
                if q_low in item["title"].lower():
                    item["type"] = mtype
                    results.append(item)
                    continue
                    
                # 2. Fuzzy match (Slower but robust for "Brenden" -> "Brandon")
                # Threshold > 0.7 covers minor typos
                ratio = difflib.SequenceMatcher(None, q_low, item["title"].lower()).ratio()
                if ratio > 0.7:
                     item["type"] = mtype
                     item["match_score"] = ratio
                     results.append(item)
    
    # Format
    if results:
        # Sort by match score (descending) then title length (shorter is usually better match)
        # Note: 'match_score' only exists for fuzzy ones. Exact matches need high priority.
        def get_score(x):
            if q_low in x["title"].lower(): return 1.0
            return x.get("match_score", 0.0)
            
        results.sort(key=get_score, reverse=True)
        
        lines = []
        for r in results[:15]:
            lines.append(f"- [{r['type'].upper()}] {r['title']}")
            
        message = f"Found {len(results)} matches for '{query}':\n" + "\n".join(lines)
        return {"status": "SUCCESS", "message": message, "results": results}
    
    return {"status": "FAILURE", "message": f"No matches found for '{query}' in library."}

async def play_media(entity_id: str, media_id: str, media_type: str, user_creds: dict) -> dict:
    """
    Play media on a specific Music Assistant entity.
    Implements retry logic across media types (Artist -> Track -> Playlist) for generic queries.
    """
    ha_url = user_creds.get("ha_url")
    token = user_creds.get("ha_token")

    if not ha_url or not token:
        return {"status": "FAILURE", "message": "Missing HA credentials"}

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    service_url = f"{ha_url}/api/services/music_assistant/play_media"

    # Clean prefixes
    if media_id.startswith(("music:", "search:")):
        media_id = media_id.split(":", 1)[1]

    # Determine types to try
    # If generic request, try robust sequence. If specific URI, use as is.
    types_to_try = []
    
    if media_type.startswith("library://"):
        types_to_try = ["library"]
    elif media_type in ["music", "search"]:
        # Priority: Artist > Track > Playlist > Radio
        types_to_try = ["artist", "track", "playlist", "radio"]
    else:
        # Explicit type passed? Try that, then fallback if it looks like a search
        types_to_try = [media_type]
        if media_type == "artist":
             types_to_try.extend(["track", "playlist"])
    
    log.info(f"[MA PLAY] Strategy: Trying types {types_to_try} for '{media_id}' on {entity_id}")
    
    last_error = "No attempts made"
    
    for current_type in types_to_try:
        # For library types, media_id is the full URI, media_type is 'library' (usually handled by caller)
        # But here we handle strict types vs search types
        final_type = current_type
        final_id = media_id
        
        if current_type == "library":
             final_id = media_type # Argument passed was the URI
             
        payload = {
            "entity_id": entity_id,
            "media_id": final_id,
            "media_type": final_type,
            "enqueue": "play"
        }

        try:
            log.info(f"[MA PLAY] Attempting '{final_id}' as type '{final_type}'...")
            response = requests.post(service_url, json=payload, headers=headers, timeout=10)

            if response.status_code == 200:
                log.info(f"[MA PLAY] SUCCESS playing as {final_type}")
                return {
                    "status": "SUCCESS",
                    "message": f"Playing {final_id} ({final_type}) on {entity_id}",
                    "entity_id": entity_id
                }
            else:
                last_error = f"HTTP {response.status_code}: {response.text[:100]}"
                log.warning(f"[MA PLAY] Failed attempt as {final_type}: {last_error}")
                
        except Exception as e:
            last_error = str(e)
            log.warning(f"[MA PLAY] Exception attempting {final_type}: {e}")

    log.error(f"[MA PLAY] All attempts failed. Last error: {last_error}")
    return {
        "status": "FAILURE",
        "message": f"Failed to play media on Music Assistant. Last error: {last_error}"
    }

async def control_player(entity_id: str, command: str, user_creds: dict) -> dict:
    """
    Control a MA player (play, pause, stop, next, previous).
    """
    ha_url = user_creds.get("ha_url")
    token = user_creds.get("ha_token")
    
    if not ha_url or not token:
        return {"status": "FAILURE", "message": "Missing HA credentials"}
        
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    
    # Map command to service
    service_map = {
        "play": "media_play",
        "pause": "media_pause", 
        "stop": "media_stop",
        "next": "media_next_track",
        "previous": "media_previous_track"
    }
    
    service = service_map.get(command)
    if not service:
        return {"status": "FAILURE", "message": f"Unknown command: {command}"}

    service_url = f"{ha_url}/api/services/media_player/{service}"
    payload = {"entity_id": entity_id}
    
    try:
        log.info(f"[MA CONTROL] {command} on {entity_id}")
        response = requests.post(service_url, json=payload, headers=headers, timeout=5)
        
        if response.status_code == 200:
            return {"status": "SUCCESS", "message": f"Executed {command} on {entity_id}"}
        else:
            return {"status": "FAILURE", "message": f"Failed: {response.status_code}"}
    except Exception as e:
        return {"status": "FAILURE", "message": str(e)}


"""
Music Assistant browsing tools for playlists, podcasts, and media library.
"""
import logging
import requests
from typing import Dict, List, Optional
from settings import GlobalResources, log

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
    ha_url = user_creds.get("url")
    token = user_creds.get("token")
    
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
    
    # Browse Artist, Album, Track in parallel? (For now sequential)
    for mtype in ["artist", "album", "track", "radio", "podcast"]:
        res = await browse_music_library(ma_entity, user_creds, media_type=mtype)
        if res["status"] == "SUCCESS":
            for item in res["items"]:
                if q_low in item["title"].lower():
                    item["type"] = mtype
                    results.append(item)
    
    # Format
    if results:
        # Sort by exact match?
        results.sort(key=lambda x: q_low not in x["title"].lower()) # Simple sort
        
        lines = []
        for r in results[:15]:
            lines.append(f"- [{r['type'].upper()}] {r['title']}")
            
        message = f"Found {len(results)} matches for '{query}':\n" + "\n".join(lines)
        return {"status": "SUCCESS", "message": message, "results": results}
    
    return {"status": "FAILURE", "message": f"No matches found for '{query}' in library."}

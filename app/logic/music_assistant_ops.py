"""
Music Assistant browsing tools for playlists, podcasts, and media library.
"""
import logging
import requests
from typing import Dict, List, Optional

log = logging.getLogger(__name__)


async def browse_music_library(entity_id: str, user_creds: dict, media_type: str = "playlist") -> dict:
    """
    Browse Music Assistant library to list playlists, artists, albums, etc.
    
    Args:
        entity_id: Music Assistant media player entity (e.g., media_player.office_tv_chrome_2)
        user_creds: HA credentials dict with 'url' and 'token'
        media_type: Type of media to browse - 'playlist', 'artist', 'album', 'track'
    
    Returns:
        Dict with status and list of items
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
    """
    Tool to list available Music Assistant playlists.
    Usage: "What playlists do I have?" or "List my playlists"
    """
    # Try to find a Music Assistant media player
    # For now, use a default MA player - you might want to make this configurable
    ma_entity = "media_player.mass"  # Common MA entity name
    
    result = await browse_music_library(ma_entity, user_creds, media_type="playlist")
    
    if result["status"] == "SUCCESS":
        items = result["items"]
        if items:
            playlist_names = [item["title"] for item in items[:20]]  # Limit to 20
            message = f"You have {result['count']} playlists:\n" + "\n".join(f"- {name}" for name in playlist_names)
            if result['count'] > 20:
                message += f"\n... and {result['count'] - 20} more"
        else:
            message = "No playlists found in your Music Assistant library"
        
        return {
            "status": "SUCCESS",
            "message": message,
            "playlists": items,
            "service": "list_playlists"
        }
    else:
        return result

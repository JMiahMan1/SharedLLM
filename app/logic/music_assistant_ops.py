
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
    Browse Music Assistant library using native `music_assistant.get_library` service.
    This provides cleaner data than the generic media_player.browse_media.
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
        # We don't strictly *need* an entity_id for get_library if we target the integration, 
        # but the service call often takes a config_entry_id or just works globally on the integration instance.
        # However, calling the service via REST API usually requires just passing the data.
        
        # Use music_assistant.get_library
        # It's a service call that returns response data (requires HA 2023.7+)
        service_url = f"{ha_url}/api/services/music_assistant/get_library?return_response=true"
        
        # Mapping our types to MA types
        # MA Types: artist, album, track, radio, playlist
        
        payload = {
            "media_type": media_type,
            "limit": 5000, # Fetch a large chunk for sync/search
            "order_by": "name"
        }
        
        log.info(f"[MA BROWSE] Calling music_assistant.get_library for {media_type} (Limit 5000)")
        response = requests.post(service_url, json=payload, headers=headers, timeout=20)
        
        if response.status_code != 200:
            log.error(f"[MA BROWSE] Failed: {response.status_code} - {response.text}")
            return {
                "status": "FAILURE",
                "message": f"Failed to get library: {response.status_code}"
            }
        
        # Parse response
        # The service response structure usually matches the 'response_variable' content directly in REST?
        # Typically it returns {"result": { ... }} or just the JSON if return_response=true.
        # Structure: {'items': [...], 'count': N, 'limit': ...}
        
        data = response.json()
        
        # Safety check on response format
        items_data = []
        if isinstance(data, dict):
             # It might be in 'response' key depending on HA version/wrapper?
             # But 'return_response=true' usually returns the dictionary directly or inside 'response'.
             # Let's handle both.
             if "items" in data:
                 items_data = data["items"]
             elif "response" in data and isinstance(data["response"], dict) and "items" in data["response"]:
                 items_data = data["response"]["items"]
        
        items = []
        for item in items_data:
            # MA returns 'name' usually, 'title' in old browse_media?
            # Standardizing on 'title' for our app
            title = item.get("name") or item.get("title") or "Unknown"
            uri = item.get("uri") or item.get("item_id")
            
            items.append({
                "title": title,
                "media_content_id": uri,
                "media_content_type": media_type,
                "can_play": True # items in library are playable
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

async def sync_library_to_redis(user_creds: dict, redis_client) -> dict:
    """
    Syncs the entire Music Assistant library (titles) to Redis for robust fuzzy matching.
    """
    import json
    import time
    
    if not redis_client:
        return {"status": "FAILURE", "message": "Redis client not available"}

    ma_entity = await _get_ma_player()
    sync_types = ["artist", "album", "track", "playlist", "radio"]
    total_synced = 0
    
    log.info("[MA SYNC] Starting library sync to Redis...")
    
    for mtype in sync_types:
        try:
            # Browse full library for type
            # browse_music_library iterates paging? No, currently fetches one page.
            # Usually 'library://artist' returns the main list. 
            # If it's huge, browse_media might truncate (HA limit), but it's the best we have via REST.
            res = await browse_music_library(ma_entity, user_creds, media_type=mtype)
            
            if res["status"] == "SUCCESS":
                items = res.get("items", [])
                titles = [item["title"] for item in items]
                
                # Store as JSON list
                key = f"ma_cache:{mtype}"
                redis_client.setex(key, 86400, json.dumps(titles)) # 24h Expire
                
                log.info(f"[MA SYNC] Synced {len(titles)} {mtype}s to {key}")
                total_synced += len(titles)
            else:
                log.warning(f"[MA SYNC] Failed to browse {mtype}: {res.get('message')}")
                
        except Exception as e:
            log.error(f"[MA SYNC] Error syncing {mtype}: {e}")

    # Set timestamp
    redis_client.set("ma_cache:updated_at", str(time.time()))
    log.info(f"[MA SYNC] Complete. Total items: {total_synced}")
    return {"status": "SUCCESS", "total": total_synced}


async def tool_music_search(query: str, user_creds: dict, redis_client=None) -> dict:
    """
    Search Music Assistant library.
    Step 1: Check Redis Cache for fuzzy match (Fast, handles typos like 'Brendan' -> 'Brandon').
    Step 2: Fallback to live browsing/search (Slow).
    """
    ma_entity = await _get_ma_player()
    q_low = query.lower()
    results = []
    import difflib
    import json
    
    # --- STEP 1: CACHE SEARCH ---
    if redis_client:
        cache_hit = False
        for mtype in ["artist", "album", "track", "playlist", "radio"]:
            key = f"ma_cache:{mtype}"
            try:
                data = redis_client.get(key)
                if data:
                    titles = json.loads(data)
                    # 1. Exact/Substring in Cache
                    # 2. Fuzzy in Cache
                    
                    # Get close matches (cutoff=0.6 to catch 'Brendan' -> 'Brandon')
                    matches = difflib.get_close_matches(query, titles, n=3, cutoff=0.6)
                    
                    for match in matches:
                        # Reconstruct basic item structure
                        score = difflib.SequenceMatcher(None, q_low, match.lower()).ratio()
                        results.append({
                            "title": match,
                            "type": mtype,
                            "match_score": score,
                            "source": "cache"
                        })
                        cache_hit = True
            except Exception as e:
                log.warning(f"[MA CACHE] Error reading {key}: {e}")
                
        if cache_hit and results:
            log.info(f"[MA SEARCH] Found {len(results)} matches in Redis Cache.")
            # Sort and return immediately if we have high confidence?
            # Or mix with live? 
            # If we have a > 0.8 match in cache, it's likely what they want.
            results.sort(key=lambda x: x["match_score"], reverse=True)
            if results[0]["match_score"] > 0.8:
                 return _format_search_results(query, results)
                 
    # --- STEP 2: LIVE SEARCH (Fallback) ---
    log.info(f"[MA SEARCH] Cache miss or low confidence. Falling back to live browse...")
    
    # (Existing Logic)
    for mtype in ["artist", "album", "track", "radio", "podcast"]:
        res = await browse_music_library(ma_entity, user_creds, media_type=mtype)
        if res["status"] == "SUCCESS":
            for item in res["items"]:
                if q_low in item["title"].lower():
                    item["type"] = mtype
                    item["source"] = "live"
                    results.append(item)
                    continue
                ratio = difflib.SequenceMatcher(None, q_low, item["title"].lower()).ratio()
                if ratio > 0.7:
                     item["type"] = mtype
                     item["match_score"] = ratio
                     item["source"] = "live"
                     results.append(item)

    return _format_search_results(query, results)

def _format_search_results(query, results):
    if results:
        # Sort
        results.sort(key=lambda x: x.get("match_score", 0 if query.lower() in x["title"].lower() else 0), reverse=True)
        
        lines = []
        for r in results[:15]:
            src = f"[{r.get('source', 'live').upper()}]"
            lines.append(f"- [{r['type'].upper()}] {r['title']} {src}")
            
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

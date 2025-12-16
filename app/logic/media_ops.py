# app/logic/media_ops.py
import json
import re
import logging
import requests
import asyncio
from typing import List, Dict, Optional, Tuple
from settings import run_blocking, HA_URL, DEFAULT_MODEL, GlobalResources
from logic.pattern_matching import detect_number_pattern, filter_entities_by_pattern

log = logging.getLogger(__name__)


def safe_similarity_search(collection, query: str, k: int = 5):
    docs = collection.similarity_search(query, k=k)
    if not docs:
        log.warning(f"No docs returned from ChromaDB for query '{query}'.")
    return docs

# --- Media Intent Definitions ---
# App Package IDs for Android TV Smart Routing
APP_PACKAGES = {
    "youtube": "com.google.android.youtube.tv",
    "netflix": "com.netflix.ninja",
    "disney": "com.disney.disneyplus",
    "disney+": "com.disney.disneyplus",
    "spotify": "com.spotify.tv.android",
    "prime video": "com.amazon.amazonvideo.livingroom",
    "amazon prime": "com.amazon.amazonvideo.livingroom",
    "plex": "com.plexapp.android",
    "twitch": "tv.twitch.android.app",
    "kodi": "org.xbmc.kodi",
    "hulu": "com.hulu.livingroomplus",
    "hbo": "com.wbd.stream", 
    "max": "com.wbd.stream"
}

# Used by pipeline.py for routing
MEDIA_INTENTS = [
    "turn_on", "turn_off", "toggle", 
    "stop_media", "play_media", "open_app",
    "media_next", "media_previous",
    "volume_up", "volume_down", "volume_set", "volume_mute",  # Volume controls
    "nav_up", "nav_down", "nav_left", "nav_right", 
    "nav_enter", "nav_back", "nav_home",
    "set_color", "set_brightness", "dim", "brighten"
]

# Used by pipeline.py for Regex Overrides
REGEX_INTENT_MAP = {
    r"\b(open|launch|start)\s+(netflix|youtube|disney|hulu|plex|prime|spotify)": "open_app",
    r"\b(play)\b": "play_media",
    r"\b(stop|pause)\b": "stop_media",
    r"\b(skip|next)\b": "media_next",
    r"\b(previous|back|prev)\b": "media_previous",
    r"\b(scroll|move|go)\s+up\b": "nav_up",
    r"\b(scroll|move|go)\s+down\b": "nav_down",
    r"\b(scroll|move|go)\s+left\b": "nav_left",
    r"\b(scroll|move|go)\s+right\b": "nav_right",
    r"\bgo back\b|\bback\b": "nav_back",
    r"\bgo home\b|\bhome\b": "nav_home",
    r"\bselect\b|\benter\b|\bok\b": "nav_enter",
    # Color control: matches "set/change/make X color" OR "turn X to color"
    r"\b(set|change|make).+(color|colour|red|blue|green|purple|orange|yellow|pink|white|warm|cool)": "set_color",
    r"\bturn\s+.+\s+(?:to\s+)?(red|blue|green|purple|orange|yellow|pink|white|warm|cool)": "set_color",
    r"\b(dim|darken|lower)\b": "dim",
    r"\b(brighten|brighter|increase)\b": "brighten",
    r"\b(brightness|bright)\b": "set_brightness",
}

# Color name to RGB mapping
COLOR_MAP = {
    "red": [255, 0, 0],
    "green": [0, 255, 0],
    "blue": [0, 0, 255],
    "yellow": [255, 255, 0],
    "orange": [255, 165, 0],
    "purple": [128, 0, 128],
    "pink": [255, 192, 203],
    "white": [255, 255, 255],
    "warm white": [255, 220, 180],
    "cool white": [200, 220, 255],
    "cyan": [0, 255, 255],
    "magenta": [255, 0, 255],
}

# --------------------------------------
def _get_last_entity_key(user: str) -> str:
    return f"rag:last_entity:{user}"

def _set_last_entity(redis_client, user: str, entity_id: str):
    if redis_client and entity_id:
        redis_client.setex(_get_last_entity_key(user), 86400, entity_id)

def get_last_entity(redis_client, user: str) -> str:
    if redis_client:
        val = redis_client.get(_get_last_entity_key(user))
        return val.decode('utf-8') if isinstance(val, bytes) else val
    return None

async def get_entity_state(entity_id: str, user_creds: dict) -> str:
    if not HA_URL:
        return "unknown"

    url = f"{HA_URL.rstrip('/')}/api/states/{entity_id}"
    headers = {"Authorization": f"Bearer {user_creds['ha_token']}"}

    try:
        def _fetch():
            return requests.get(url, headers=headers, timeout=2.0)

        r = await run_blocking(_fetch)
        if r.status_code == 200:
            return r.json().get("state", "unknown")
    except Exception as e:
        log.error(f"State fetch error for {entity_id}: {e}")

    return "unknown"

async def get_device_capabilities(entity_id: str, user_creds: dict, redis_client) -> dict:
    """
    Fetch and cache device capabilities from Home Assistant.
    Parses supported_features bitmask and available attributes.
    
    Returns dict with device capabilities:
    - Light: has_brightness, has_color, has_color_temp, color_modes
    - Media Player: has_next, has_previous, has_volume, has_play_media
    """
    import json
    
    # Check Redis cache first (TTL: 1 hour)
    cache_key = f"capabilities:{entity_id}"
    if redis_client:
        try:
            log.debug(f"[CAPABILITY] Checking Redis cache for {entity_id}")
            cached = redis_client.get(cache_key)
            if cached:
                cached_str = cached.decode('utf-8') if isinstance(cached, bytes) else cached
                log.info(f"[CAPABILITY] Redis cache HIT for {entity_id}")
                return json.loads(cached_str)
            log.debug(f"[CAPABILITY] Redis cache MISS for {entity_id}")
        except Exception as e:
            log.warning(f"Redis cache read error for {cache_key}: {e}")
    else:
        log.debug(f"[CAPABILITY] No Redis client, skipping cache for {entity_id}")
    
    # Try ChromaDB next (faster than HA API, no rate limits)
    try:
        from settings import GlobalResources
        log.debug(f"[CAPABILITY] Querying ChromaDB for {entity_id}")
        
        # Query by exact entity_id match
        ha_collection = GlobalResources.ha_collection
        results = await run_blocking(lambda: ha_collection.get(
            where={"entity_id": entity_id},
            include=["metadatas"]
        ))
        
        if results and results["metadatas"] and len(results["metadatas"]) > 0:
            metadata = results["metadatas"][0]
            log.info(f"[CAPABILITY] ChromaDB HIT for {entity_id}")
            
            # Parse capabilities from stored metadata
            capabilities = {
                "domain": metadata.get("domain", entity_id.split('.')[0]),
                "friendly_name": metadata.get("friendly_name", entity_id.split('.')[-1].replace('_', ' ').title()),
                "integration": metadata.get("integration", "unknown")
            }
            
            # Parse supported_features if present
            if "supported_features" in metadata:
                try:
                    features = int(metadata["supported_features"])
                    capabilities["supported_features"] = features
                    
                    # Parse domain-specific capabilities
                    if capabilities["domain"] == "light":
                        capabilities["has_brightness"] = bool(features & 1)
                        capabilities["has_color_temp"] = bool(features & 2)
                        capabilities["has_color"] = bool(features & 16)
                        
                        # Parse color_modes if present
                        if "supported_color_modes" in metadata:
                            color_modes = json.loads(metadata["supported_color_modes"])
                            capabilities["color_modes"] = color_modes
                            # Override has_color based on color_modes (more authoritative)
                            capabilities["has_color"] = any(m in color_modes for m in ["rgb", "hs", "xy", "rgbw", "rgbww"])
                            capabilities["has_color_temp"] = "color_temp" in color_modes
                    
                    elif capabilities["domain"] == "media_player":
                        capabilities["has_pause"] = bool(features & 1)
                        capabilities["has_previous"] = bool(features & 16)
                        capabilities["has_next"] = bool(features & 32)
                        capabilities["has_volume"] = bool(features & 4)
                        capabilities["has_volume_mute"] = bool(features & 8)
                        capabilities["has_play_media"] = bool(features & 512)
                        capabilities["has_volume_step"] = bool(features & 1024)
                    
                    # Cache in Redis and return
                    if redis_client:
                        try:
                            redis_client.setex(cache_key, 3600, json.dumps(capabilities))
                        except Exception as e:
                            log.warning(f"Redis cache write error: {e}")
                    
                    log.info(f"[CAPABILITY] Parsed from ChromaDB: {entity_id} → has_color={capabilities.get('has_color', False)}, has_brightness={capabilities.get('has_brightness', False)}")
                    return capabilities
                    
                except (ValueError, KeyError) as e:
                    log.warning(f"[CAPABILITY] Error parsing ChromaDB metadata for {entity_id}: {e}")
        else:
            log.debug(f"[CAPABILITY] ChromaDB MISS for {entity_id}")
    except Exception as e:
        log.warning(f"[CAPABILITY] ChromaDB query error for {entity_id}: {e}")
    
    # Fallback to Home Assistant API
    if not HA_URL:
        return {"domain": entity_id.split('.')[0], "error": "no_ha_url"}
    
    url = f"{HA_URL.rstrip('/')}/api/states/{entity_id}"
    headers = {"Authorization": f"Bearer {user_creds['ha_token']}"}
    
    try:
        log.debug(f"[CAPABILITY] Fetching from HA API: {entity_id}")
        def _fetch():
            return requests.get(url, headers=headers, timeout=3.0)
        
        r = await run_blocking(_fetch)
        log.debug(f"[CAPABILITY] HA API response: {r.status_code} for {entity_id}")
        
        if r.status_code != 200:
            log.warning(f"Failed to fetch capabilities for {entity_id}: {r.status_code}")
            return {"domain": entity_id.split('.')[0], "error": "unavailable"}
        
        data = r.json()
        attrs = data.get("attributes", {})
        
        # Base capabilities
        capabilities = {
            "domain": entity_id.split('.')[0],
            "supported_features": attrs.get("supported_features", 0),
            "available_attributes": list(attrs.keys()),
            "friendly_name": attrs.get("friendly_name", entity_id.split('.')[-1].replace('_', ' ').title())
        }
        
        # Light-specific capability detection
        if capabilities["domain"] == "light":
            features = capabilities["supported_features"]
            # HA Light Feature Bitmasks
            # SUPPORT_BRIGHTNESS = 1, SUPPORT_COLOR_TEMP = 2, SUPPORT_EFFECT = 4
            # SUPPORT_FLASH = 8, SUPPORT_COLOR = 16, SUPPORT_TRANSITION = 32
            capabilities["has_brightness"] = bool(features & 1)
            capabilities["has_color_temp"] = bool(features & 2)
            capabilities["has_color"] = bool(features & 16)
            capabilities["color_modes"] = attrs.get("supported_color_modes", [])
            
            # If color_modes is present, it's more authoritative
            if capabilities["color_modes"]:
                capabilities["has_color"] = any(m in capabilities["color_modes"] for m in ["rgb", "hs", "xy", "rgbw", "rgbww"])
                capabilities["has_color_temp"] = "color_temp" in capabilities["color_modes"]
        
        # Media Player-specific capability detection
        elif capabilities["domain"] == "media_player":
            features = capabilities["supported_features"]
            # HA Media Player Feature Bitmasks
            # SUPPORT_PAUSE = 1, SUPPORT_SEEK = 2, SUPPORT_VOLUME_SET = 4, SUPPORT_VOLUME_MUTE = 8
            # SUPPORT_PREVIOUS_TRACK = 16, SUPPORT_NEXT_TRACK = 32, SUPPORT_TURN_ON = 128
            # SUPPORT_TURN_OFF = 256, SUPPORT_PLAY_MEDIA = 512
            capabilities["has_pause"] = bool(features & 1)
            capabilities["has_previous"] = bool(features & 16)
            capabilities["has_next"] = bool(features & 32)
            capabilities["has_volume"] = bool(features & 4)
            capabilities["has_volume_mute"] = bool(features & 8)
            capabilities["has_play_media"] = bool(features & 512)
            capabilities["has_volume_step"] = bool(features & 1024)
            capabilities["has_turn_on"] = bool(features & 128)
            capabilities["has_turn_off"] = bool(features & 256)
        
        # Cache for 1 hour
        if redis_client:
            try:
                redis_client.setex(cache_key, 3600, json.dumps(capabilities))
                log.debug(f"[CAPABILITY] Cached capabilities for {entity_id}")
            except Exception as e:
                log.warning(f"Cache write error for {cache_key}: {e}")
        
        log.info(f"[CAPABILITY] Parsed: {entity_id} → domain={capabilities['domain']}, features={capabilities.get('supported_features', 0)}, has_color={capabilities.get('has_color', False)}, has_brightness={capabilities.get('has_brightness', False)}")
        return capabilities
        
    except Exception as e:
        log.error(f"Error fetching capabilities for {entity_id}: {e}")
        return {"domain": entity_id.split('.')[0], "error": str(e)}

async def get_active_media_players(user_creds: dict) -> list:
    """Returns a list of entity_ids for media players that are currently playing or paused."""
    if not HA_URL: return []

    url = f"{HA_URL.rstrip('/')}/api/states"
    headers = {"Authorization": f"Bearer {user_creds['ha_token']}"}

    try:
        def _fetch_all():
            return requests.get(url, headers=headers, timeout=3.0)

        r = await run_blocking(_fetch_all)
        if r.status_code == 200:
            all_states = r.json()
            active = []
            for s in all_states:
                eid = s.get("entity_id", "")
                if eid.startswith("media_player."):
                    state = s.get("state", "off")
                    if state in ["playing", "paused", "buffering"]:
                        active.append(eid)
            return active
    except Exception as e:
        log.error(f"Error fetching active players: {e}")
        return []
    return []

async def get_available_media_players(user_creds: dict) -> list:
    """Returns a list of ALL valid media players (excluding unavailable)."""
    if not HA_URL: return []
    
    url = f"{HA_URL.rstrip('/')}/api/states"
    headers = {"Authorization": f"Bearer {user_creds['ha_token']}"}
    
    try:
        def _fetch_all():
            return requests.get(url, headers=headers, timeout=3.0)
        
        r = await run_blocking(_fetch_all)
        if r.status_code == 200:
            all_states = r.json()
            available = []
            for s in all_states:
                eid = s.get("entity_id", "")
                if eid.startswith("media_player."):
                    state = s.get("state", "unknown")
                    if state not in ["unavailable", "unknown"]:
                        available.append(eid)
            return available
    except Exception as e:
        log.error(f"Error fetching available players: {e}")
        return []
    return []

async def execute_ha_service(domain, service, entity_id, user_creds, service_data=None, redis_client=None):
    """
    Executes a Home Assistant service and returns a structured dictionary result.
    Includes optimized state verification loop.
    """
    user = user_creds.get("user")

    if not HA_URL:
        return {"status": "FAILURE", "message": "Error: Home Assistant URL not configured.", "entity_id": entity_id, "service": f"{domain}.{service}"}

    # Fetch initial state for pre/post comparison
    initial_state = await get_entity_state(entity_id, user_creds)

    url = f"{HA_URL.rstrip('/')}/api/services/{domain}/{service}"
    headers = {"Authorization": f"Bearer {user_creds['ha_token']}"}
    payload = {"entity_id": entity_id, **(service_data or {})}

    log.info(f"EXEC HA: {domain}.{service} on {entity_id} | Data: {service_data}")

    last_err = None

    for attempt in range(2): 
        try:
            def _post():
                return requests.post(url, json=payload, headers=headers, timeout=5.0)

            r = await run_blocking(_post)

            if r.status_code < 400:
                _set_last_entity(redis_client, user, entity_id)

                # --- OPTIMIZED: Faster State Verification ---
                # No initial long wait, start checking immediately
                new_state = "N/A"
                friendly_name = entity_id

                # Check up to 5 times, every 0.5 seconds (Total ~2.5s max wait)
                for state_attempt in range(5):
                    await asyncio.sleep(0.5) 
                    try:
                        state_url = f"{HA_URL.rstrip('/')}/api/states/{entity_id}"
                        def _get_name():
                            return requests.get(state_url, headers=headers, timeout=1.0)

                        r_state = await run_blocking(_get_name)
                        if r_state.status_code == 200:
                            state_data = r_state.json()
                            friendly_name = state_data.get("attributes", {}).get("friendly_name", entity_id)
                            current_state = state_data.get("state", "unknown")

                            # Check for expected state change
                            expected_change = False
                            if service.startswith("turn_off") and current_state in ["off", "unavailable"]:
                                expected_change = True
                            elif service.startswith("turn_on") and current_state not in ["off", "unavailable"]:
                                expected_change = True
                            elif service.startswith("media_play") and current_state in ["playing", "paused", "buffering"]:
                                expected_change = True

                            if expected_change or state_attempt == 4:
                                new_state = current_state
                                break
                    except:
                        pass

                # --- END FIX ---

                verb = service.replace("_", " ")
                return {
                    "status": "SUCCESS", 
                    "message": f"Sent command to {verb} the {friendly_name}.", 
                    "entity_id": entity_id,
                    "friendly_name": friendly_name,
                    "service": f"{domain}.{service}",
                    "new_state": new_state
                }

            # Error Capture
            try:
                err_data = r.json()
                msg = err_data.get("message", r.text)
            except:
                msg = r.text[:200] if r.text else "Unknown Error"

            last_err = f"HTTP {r.status_code}: {msg}"

            if r.status_code >= 500:
                log.warning(f"HA 500 Error: {msg}")
                break

        except Exception as e:
            last_err = str(e)

        await asyncio.sleep(0.5)

    log.error(f"Failed to execute HA command: {last_err}")
    return {
        "status": "FAILURE", 
        "message": f"Failed: {last_err}", 
        "entity_id": entity_id,
        "friendly_name": entity_id.split(".")[-1].replace("_", " ").title() if entity_id else "System",
        "service": f"{domain}.{service}"
    }

# --- REFACTORED: Duplicate implementation removed ---
async def resolve_multiple_entities_with_pattern(
    query: str, 
    intent: str, 
    ha_collection
) -> List[Tuple[str, str]]:
    """
    Resolve entities with pattern matching support.
    Returns list of (entity_id, integration) tuples.
    
    If pattern detected (even/odd/range/list/all), returns all matching entities.
    Otherwise returns single best match.
    """
    # Detect entity patterns (returns list of type/data tuples)
    detected_patterns = detect_number_pattern(query)
    
    if not detected_patterns:
        # No pattern - use single entity resolution
        result = await smart_resolve_entity(query, intent, ha_collection)
        
        # Check if smart_resolve_entity returned a list (batch)
        if isinstance(result, list):
             return result
        
        entity_id, integration = result
        if entity_id:
            return [(entity_id, integration)]
        return []
    
    # Prioritize Music Assistant entities if multiple are active
    ma_players = []
    other_players = []
    
    try:
        log.info(f"Scan: Checking {len(entities)} players for active state.")
        for entity in entities:
             state = entity.get("state")
             eid = entity.get("entity_id")
             # Log potentially active devices to debug state mismatches
             if state in ["playing", "buffering", "paused"]:
                 log.info(f"Scan: Inspecting {eid} (State: {state})")
                 
             if state == "playing":
                  # Check for MA attributes
                  attrs = entity.get("attributes") or {}
                  
                  # DEBUG LOG
                  aid = attrs.get("app_id")
                  mass_type = attrs.get("mass_player_type")
                  
                  if aid == "music_assistant" or mass_type:
                       log.info(f"Scan: MATCH MA Player {eid} (app_id={aid})")
                       ma_players.append(eid)
                  else:
                       log.info(f"Scan: MATCH Generic Player {eid} (app_id={aid})")
                       other_players.append(eid)
    except Exception as e:
        log.error(f"Error in scan_for_active_players: {e}")
        # Fallback to simple scan if complex one fails
        for entity in entities:
            if entity.get("state") == "playing":
                return entity.get("entity_id")

    # Return MA player if exists, else first other player
    if ma_players:
         return ma_players[0]
    if other_players:
         return other_players[0]
         
    return None
    
    log.info(f"[PATTERN] Detected patterns: {[p[0] for p in detected_patterns]}")
    
    # Pattern detected - get all candidates and filter
    docs = await run_blocking(lambda: safe_similarity_search(ha_collection, query, k=50))
    if not docs:
        return []
    
    # Build candidates list with domain filtering
    candidates = []
    
    for d in docs:
        eid = d.metadata.get("entity_id")
        integration = d.metadata.get("integration", "unknown")
        # Ensure we keep all metadata (like friendly_name, area_name)
        metadata = d.metadata
        
        if not eid:
            continue
            
        domain = eid.split('.')[0]
        
        # Domain filtering (same as smart_resolve_entity)
        if intent in ["set_color", "set_brightness", "dim", "brighten"]:
            if domain != "light":
                continue
        elif intent in ["play_media", "open_app", "media_next", "media_previous", "stop_media"]:
            if domain not in ["media_player", "group", "script"]:
                continue
        
        # Append tuple of (entity_id, integration, metadata_dict)
        candidates.append((eid, integration, metadata))
    
    # Filter by pattern
    matching_entities = filter_entities_by_pattern(
        candidates,
        detected_patterns
    )
    
    log.info(f"[PATTERN] Resolved {len(matching_entities)} entities matching pattern '{pattern_type}'")
    return matching_entities


async def execute_batch_command(
    entities: List[Tuple[str, str]],
    intent: str,
    query: str,
    user_creds: dict,
    ha_collection,
    redis_client
) -> dict:
    """
    Execute same command on multiple entities and aggregate results.
    """
    if not entities:
        return {
            'status': 'FAILURE',
            'message': 'No matching devices found for pattern',
            'service': intent
        }
    
    log.info(f"[BATCH] Executing '{intent}' on {len(entities)} entities")
    
    results = []
    for entity_id, integration in entities:
        try:
            result = await handle_media_command(
                intent, query, entity_id, user_creds, ha_collection, redis_client
            )
            results.append(result)
        except Exception as e:
            log.error(f"[BATCH] Error executing on {entity_id}: {e}")
            results.append({
                'status': 'FAILURE',
                'message': str(e),
                'entity_id': entity_id,
                'service': intent
            })
    
    # Aggregate results
    success_count = sum(1 for r in results if r.get('status') == 'SUCCESS')
    failure_count = len(results) - success_count
    
    # Get list of successful/failed devices
    successful_devices = [r.get('friendly_name', r.get('entity_id', '?')) 
                         for r in results if r.get('status') == 'SUCCESS']
    failed_devices = [r.get('friendly_name', r.get('entity_id', '?'))
                     for r in results if r.get('status') != 'SUCCESS']
    
    if success_count == len(results):
        message = f"Successfully controlled {success_count} devices: {', '.join(successful_devices)}"
        status = 'SUCCESS'
    elif success_count > 0:
        message = f"Controlled {success_count}/{len(results)} devices. "
        message += f"Success: {', '.join(successful_devices)}. "
        if failed_devices:
            message += f"Failed: {', '.join(failed_devices)}"
        status = 'SUCCESS'  # Partial success still counts as success
    else:
        message = f"Failed to control all {len(results)} devices: {', '.join(failed_devices)}"
        status = 'FAILURE'
    
    return {
        'status': status,
        'message': message,
        'service': intent,
        'batch_results': results,
        'success_count': success_count,
        'failure_count': failure_count,
        'friendly_name': f"{success_count} devices",  # For LLM context formatting
        'entity_id': 'batch_command'  # Identify as batch in context
    }
async def smart_resolve_entity(query_name: str, intent: str, ha_collection, is_music: bool = False, is_video: bool = False, allow_multiple: bool = False) -> list:
    """
    Resolves the best entity (or entities) based on query and intent.
    When is_music=True, it prioritizes Music Assistant devices.
    
    Returns:
       - If allow_multiple=False: (entity_id, integration) tuple (legacy)
       - If allow_multiple=True: List of (entity_id, integration) tuples
    """
    log.info(f"DEBUG: Entering smart_resolve_entity. Q='{query_name}' Intent='{intent}' Multiple={allow_multiple}")
    
    # 0. Setup & lazy imports
    try:
        from settings import GlobalResources, run_blocking
        from langchain_chroma import Chroma
    except ImportError:
        log.error("Could not import dependencies for resolution.")
        return [] if allow_multiple else (None, None)

    # 1. Detect Entity Grouping/Pattern (Numbers, Locations, Directions, Plurals)
    # 1. Detect Entity Grouping/Pattern (Numbers, Locations, Directions, Plurals)
    patterns = detect_number_pattern(query_name) # Aliased to detect_entity_pattern
    if patterns:
        allow_multiple = True
        log.info(f"Detected grouping pattern: {patterns}")

    # 2. Similarity Search using Chroma
    # Increase k if looking for a group/pattern to ensure we catch all potential matches
    k = 30 if allow_multiple else 15
    try:
        results = await run_blocking(
            lambda: GlobalResources.ha_collection.similarity_search_with_score(query_name, k=k)
        )
        log.info(f"DEBUG: Search returned {len(results)} docs.")
    except Exception as e:
        log.error(f"Error querying Chroma: {e}")
        return [] if allow_multiple else (None, None)

    # 3. Filter & formatting
    raw_candidates = []
    
    if results:
        # DEBUG: Inspect first result structure
        first_item = results[0]
        log.info(f"DEBUG: First result type: {type(first_item)} val: {first_item}")

    try:
        for item in results:
            # Handle potential variation in return type (Doc, Score) vs (Doc,)
            if isinstance(item, tuple) and len(item) == 2:
                doc, score = item
            elif isinstance(item, tuple) and len(item) == 1:
                doc = item[0]
                score = 0.0 # Default score if missing
            elif hasattr(item, 'metadata'): # It's just a Document
                doc = item
                score = 0.0
            else:
                log.warning(f"Unexpected result item format: {type(item)}")
                continue

            eid = doc.metadata.get("entity_id")
            friendly_name = doc.metadata.get("friendly_name")
            integration = doc.metadata.get("integration", "unknown")
            
            # Threshold (Relaxed for distance-based scoring)
            if score > 1.4: continue 
            
            # Basic Domain Safety
            domain = eid.split('.')[0]
            if intent in ["set_color", "set_brightness", "dim", "brighten"] and domain != "light":
                 continue
            if intent in ["turn_on", "turn_off", "toggle"] and domain in ["sensor", "binary_sensor", "sun", "weather"]:
                 continue
            
            raw_candidates.append({
                "eid": eid, 
                "integration": integration, 
                "friendly_name": friendly_name,
                "score": score,
                "metadata": doc.metadata 
            })

    except Exception as e:
        log.error(f"Error filtering candidates: {e}")
        return [] if allow_multiple else (None, None)

    if not raw_candidates:
        return [] if allow_multiple else (None, None)

    # 4. Pattern Logic Application
    if patterns:
        # Prepare entities list with metadata for filtering [(eid, integration, metadata_dict)]
        entities_with_meta = []
        for c in raw_candidates:
             # Reconstruct metadata dict
             meta = {
                 "friendly_name": c["friendly_name"],
                 "area_name": "", # Start empty, would need to fetch if not present. 
                 # Optimization: Ideally filter_entities_by_pattern logic shouldn't need re-fetching, 
                 # but our current candidates dict lacks area_name explicitly unless we add it in step 3. 
                 # For now, let's assume friendly_name/domain is enough for most patterns.
                 "domain": c["eid"].split(".")[0]
             }
             entities_with_meta.append((c["eid"], c["integration"], meta))
        
        filtered_tuples = filter_entities_by_pattern(entities_with_meta, patterns)
        
        if filtered_tuples:
            log.info(f"Patterns {patterns} matched {len(filtered_tuples)} entities.")
            if allow_multiple:
                return filtered_tuples
            return [filtered_tuples[0]] # Should not happen if allow_multiple forced True

    # 5. Standard Priority Logic
    # Reconstruct simple candidates list for legacy logic
    candidates = [(c["eid"], c["integration"]) for c in raw_candidates]

    # Priority Logic for Music/Power/Etc
    
    # --- ENFORCED PRIORITY FOR MUSIC ---
    # --- MUSIC PRIORITY (Boost MA devices) ---
    if is_music:
        ma_candidate = None
        # First pass: Look for exact Music Assistant match

        for c in raw_candidates:
             eid = c["eid"]
             integ = c["integration"]
             meta = c.get("metadata", {})
             attrs = meta.get("attributes", "")
             
             # Check for explicit integration OR metadata signature
             is_ma = "music_assistant" in integ or "music_assistant" in str(attrs) or "mass_player" in str(attrs)
             
             if is_ma:
                 ma_candidate = (eid, integ)
                 break
        
        # Second pass: If no MA match, look for "speaker" type devices that aren't strict TVs
        if not ma_candidate:
             for eid, integration in candidates:
                  if "speaker" in eid or "audio" in integration:
                       ma_candidate = (eid, integration)
                       break
        
        if ma_candidate:
            return [ma_candidate] if allow_multiple else ma_candidate

        # Fallback: If no MA device found, we return the best TV candidate (if exists), 
        # but we log a warning because we expected music.
        tv_candidate = None
        for eid, integration in candidates:
            if eid.startswith("media_player.") and any(x in eid.lower() for x in ["tv", "chromecast", "shield", "androidtv"]):
                if tv_candidate is None:
                    tv_candidate = (eid, integration)
        
        if tv_candidate:
             return [tv_candidate] if allow_multiple else tv_candidate
             
        log.warning(f"Strict Music Mode: No suitable music player found for '{query_name}'. Returning None.")
        return [] if allow_multiple else (None, None)

    # --- POWER/HARDWARE PRIORITY ---
    if intent in ["turn_on", "turn_off", "toggle"]:
        HW_INTEGRATIONS_POWER = ["androidtv", "webostv", "braviatv", "roku", "apple_tv", "samsungtv", "esphome", "tasmota", "shelly", "hue", "lutron_caseta", "kodi", "vlc", "denonavr", "yamaha"]
        
        # Capability Enrichment (Re-added)
        from settings import get_user_creds
        redis_client = GlobalResources.redis_client
        admin_creds = get_user_creds("admin")
        
        enriched_candidates = []
        for c in raw_candidates:
             try:
                 caps = await get_device_capabilities(c["eid"], admin_creds, redis_client)
                 real_int = caps.get("integration", c["integration"])
                 if real_int == "unknown": real_int = c["integration"]
                 enriched_candidates.append({
                     "eid": c["eid"], 
                     "integration": real_int, 
                     "supported_features": caps.get("features_breakdown", {}),
                     "friendly_name": caps.get("friendly_name", c["friendly_name"])
                 })
             except:
                 enriched_candidates.append({"eid": c["eid"], "integration": c["integration"], "supported_features": {}, "friendly_name": c["friendly_name"]})

        matches = []
        for c in enriched_candidates:
            score = 0
            integ = c["integration"]
            eid = c["eid"]
            feats = c["supported_features"]
            
            if integ in HW_INTEGRATIONS_POWER: score += 20
            if feats.get("turn_off"): score += 10
            if any(x in eid.lower() for x in ["tv", "projector", "receiver", "remote"]): score += 5
            
            is_chrome = "chrome" in integ.lower() or "cast" in integ.lower() or "google_cast" in integ.lower()
            if is_chrome: score -= 20
            
            matches.append((score, eid, integ))

        if matches:
            matches.sort(key=lambda x: x[0], reverse=True)
            res = (matches[0][1], matches[0][2])
            return [res] if allow_multiple else res

    # --- FALLBACK / GENERIC ---
    preferred_type = "generic"
    if intent == "play_media":
         APP_PACKAGES = ["netflix", "youtube", "hulu", "plex", "spotify", "disney"] 
         q_low = query_name.lower()
         if any(app in q_low for app in APP_PACKAGES) or is_video:
             preferred_type = "android"

    elif intent in ["open_app"]:
        preferred_type = "android"
    
    elif intent in ["turn_on", "turn_off", "toggle"] or intent.startswith("nav_"):
        preferred_type = "android" # Prefer hardware for these if no explicit match above

    best_candidate = None
    
    # ---------------------------------------------------------
    # CAPABILITY / GROUP ROUTING (NEW)
    # ---------------------------------------------------------
    # If we found a match, check if it belongs to a Device Group.
    # If so, fetch the whole group and route based on Intent.
    
    top_doc = None
    if results:
         # Handle (Doc, Score) tuple or just Doc
         item = results[0]
         if isinstance(item, tuple): top_doc = item[0]
         else: top_doc = item
    
    if top_doc:
        group_id = top_doc.metadata.get("group_id")
        
        if group_id:
            log.info(f"match found in Group: {group_id} (via {top_doc.page_content[:20]}...)")
            try:
                # Fetch all group members from Chroma
                # Note: ha_collection is Langchain wrapper. Access internal collection if possible, 
                # or rely on metadata from the search result if we indexed enough? 
                # Better to query. keys: "entity_id", "integration", "capabilities", "domain"
                
                # Access underlying chromadb collection if available
                if hasattr(ha_collection, "_collection"):
                     group_res = ha_collection._collection.get(where={"group_id": group_id})
                     # group_res keys: ids, metadatas, documents...
                     
                     if group_res and group_res.get("metadatas"):
                          members = group_res["metadatas"]
                          log.info(f"Group {group_id} has {len(members)} members.")
                          
                          # ROUTING LOGIC
                          selected = _route_by_intent(intent, members, is_music, is_video)
                          if selected:
                               log.info(f"Capability Routing used {selected['entity_id']} ({selected.get('integration')}) for intent {intent}")
                               return (selected["entity_id"], selected.get("integration", "unknown"))

            except Exception as e:
                log.error(f"Group Routing Failed: {e}")

    # Fallback to standard selection
    for eid, integration in candidates:
        if preferred_type == "remote" and ("remote" in eid or "androidtv" in integration):
             return eid, integration
    
    return candidates[0]

def _route_by_intent(intent: str, members: list, is_music: bool, is_video: bool) -> dict:
    """Selects best entity from a group based on intent and capabilities."""
    
    # Score candidates
    scored = []
    
    for m in members:
        score = 0
        eid = m.get("entity_id", "")
        domain = m.get("domain", "")
        integration = m.get("integration", "unknown")
        caps = m.get("capabilities", "").split(",")
        
        friendly_name = m.get("friendly_name", "").lower()
        
        # POWER
        if intent in ["turn_on", "turn_off", "toggle"]:
            if domain == "remote": score += 100
            elif "remote" in friendly_name: score += 95 # High priority for "Office TV Remote"
            elif integration == "androidtv_remote": score += 90
            elif domain == "switch": score += 50 # Smart plug?
            elif domain == "media_player":
                # Deprioritize cast/chrome for power
                if "cast" in integration or "_chrome" in eid: score -= 50
                if "turn_off" in caps: score += 10
        
        # MEDIA PLAY
        elif intent == "play_media":
            if is_music:
                if integration == "music_assistant": score += 100
                elif "play_media" in caps: score += 10
            elif is_video:
                if "cast" in integration or "androidtv" in integration: score += 100
            else:
                # Ambiguous
                if integration == "music_assistant": score += 50
                elif "play_media" in caps: score += 10
        
        # REMOTE CONTROL
        elif intent.startswith("nav_") or intent == "open_app":
            if domain == "remote": score += 100
            elif integration == "androidtv_remote": score += 90
        
        scored.append((score, m))
    
    scored.sort(key=lambda x: x[0], reverse=True)
    if scored:
        return scored[0][1]
    return None

async def handle_media_command(
    intent: str, 
    query: str, 
    entity_id: str, 
    user_creds: dict, 
    ha_collection, 
    redis_client,
    device_name: str = None,  # Optional: Explicit device name from Orchestrator
):
    """
    Handles media command and ensures a structured dictionary is returned.
    Supports multi-device pattern matching (even/odd/range/list/all).
    """
    q_low = query.lower()
    
    # [IntentOverride] Force upgrade for ambiguous "Watch" commands (e.g. "Watch on Roku" classified as turn_on)
    if re.search(r"\b(watch|view)\b", q_low) and intent not in ["watch_video", "view_content", "play_media"]:
         # Note: We exclude 'play_media' from forced upgrade here if we want to let play_media logic handle it?
         # No, 'play_media' logic below (lines 1500+) ALREADY handles 'watch' detection.
         # The issue is 'turn_on' or generic intents.
         # So we force upgrade only if it's NOT already media-related?
         # Actually, force it for EVERYTHING except maybe 'stop', 'volume', etc.
         if intent not in ["stop_media", "volume_up", "volume_down", "volume_mute", "volume_set"]:
             log.info(f"[IntentOverride] Detected 'watch' keyword. Upgrading intent '{intent}' -> 'watch_video'")
             intent = "watch_video"
    
    integration = "unknown"
    
    # 1. EARLY MUSIC/CONTENT DETECTION (Moved Up)
    music_keywords = ["music", "song", "artist", "album", "track", "playlist", "radio"]
    audiobook_keywords = ["read", "book", "chapter", "audiobook"]
    video_keywords = ["movie", "film", "show", "video", "youtube", "netflix", "watch"]
    
    is_music_request = any(x in q_low for x in music_keywords)
    is_audiobook_request = any(x in q_low for x in audiobook_keywords)
    is_video_request = any(x in q_low for x in video_keywords)
    
    # For play_media intent, default to music mode UNLESS explicitly requesting video
    # This ensures "Play Brandon Lake" (no "music" keyword) still prefers MA players over generic TVs
    strict_resolution = (is_music_request or is_audiobook_request) or (
        (intent == "play_media" or intent == "play") and not is_video_request
    )
    is_transport = intent in ["media_next", "media_previous", "stop_media", "volume_set", "volume_up", "volume_down", "volume_mute"]

    # --- Device Name Fallback ---
    # If Orchestrator provides device_name but no entity_id, resolve it
    if not entity_id and device_name:
        log.info(f"[Device Fallback] No entity_id provided. Attempting to resolve device_name: '{device_name}' (Music Mode: {strict_resolution})")
        try:
            resolved = await smart_resolve_entity(
                device_name,
                intent,
                ha_collection, 
                is_music=strict_resolution,
                is_video=is_video_request,
                allow_multiple=True
            )
            
            if isinstance(resolved, list):
                if resolved:
                    log.info(f"[Device Fallback] Resolved {len(resolved)} entities. Executing Batch.")
                    return await execute_batch_command(resolved, intent, query, user_creds, ha_collection, redis_client)
                else:
                     log.info(f"[Device Fallback] No devices found for {device_name}")
            elif isinstance(resolved, tuple):
                 entity_id, integration = resolved
                 log.info(f"[Device Fallback] Resolved '{device_name}' to {entity_id}")
            elif resolved: 
                 entity_id = resolved
                 log.info(f"[Device Fallback] Resolved '{device_name}' to {entity_id}")
        except Exception as e:
            log.error(f"[Device Fallback] Error resolving '{device_name}': {e}", exc_info=True)
            # Fallback failed, continue to standard resolution (which might also fail, but safely)
    
    # --- PATTERN PREVENTION (Handled downstream) ---
    # Manual pattern checks removed to prevent list unpacking errors.
    # Patterns are now handled within smart_resolve_entity -> resolve_multiple_entities_with_pattern

    # --- Sanitize Intent if LLM hallucinated a full sentence ---
    if intent not in MEDIA_INTENTS:
        original_intent = intent
        intent_lower = intent.lower()
        if "play" in intent_lower:
            intent = "play_media"
        elif "stop" in intent_lower or "pause" in intent_lower:
            intent = "stop_media"
        elif "next" in intent_lower or "skip" in intent_lower:
            intent = "media_next"
        elif "turn on" in intent_lower:
            intent = "turn_on"
        elif "turn off" in intent_lower:
            intent = "turn_off"
        elif "mute" in intent_lower:
            intent = "volume_mute"
        # --- Volume Intent Sanitization ---
        elif "volume" in intent_lower:
            if "up" in intent_lower:
                intent = "volume_up"
            elif "down" in intent_lower:
                intent = "volume_down"
            else:
                intent = "volume_set"  # Default to volume_set for specific % commands
        # ----------------------------------

        if intent != original_intent:
            log.info(f"Sanitized intent from '{original_intent}' to '{intent}'")
    # ------------------------------------------------------------------------

    # --- TRANSPORT SHORT CIRCUIT (High Confidence/Explicit Target) ---
    if is_transport:
        device_match = re.search(r"\b(on|in)\s+(the\s+)?(office|tv|bedroom|kitchen|speaker|remote|media)\b", q_low)

        # 2a. Resolve device name from query if present
        if not entity_id and device_match:
             potential_device_name = q_low.split(device_match.group(1))[-1].strip()
             if potential_device_name:
                 # Pass strict_resolution=True if we are skipping tracks, to prefer MA entities
                 resolved_result = await smart_resolve_entity(potential_device_name, intent, ha_collection, is_music=True, is_video=is_video_request)
                 
                 resolved_id, resolved_int = None, None
                 if isinstance(resolved_result, list):
                     # If patterns matched (e.g. "Kitchen Lights"), we shouldn't allow this Short Circuit 
                     # to capture it as a single media device, unless there's only one.
                     # For now, let's skip short circuit if it's a group, so it falls to standard resolution.
                     log.info(f"Transport Short Circuit: Ignored group result for '{potential_device_name}' (Size: {len(resolved_result)})")
                 else:
                     resolved_id, resolved_int = resolved_result
                 
                 if resolved_id:
                    log.info(f"Transport Short Circuit: Found explicit device {resolved_id} from query.")
                    entity_id = resolved_id
                    integration = resolved_int

        # 2b. If we have an entity_id now (from Redis or short circuit), check its state
        if entity_id:
             # Check if an MA version exists and is active, swap if needed
             # Check if an MA version exists and is active, swap if needed
             # FIX: Do not swap for power commands (turn_off checks hardware)
             if "music_assistant" not in integration and intent not in ["turn_on", "turn_off", "toggle"]:
                 # Clean Lookup for linked MA player
                 # Try finding a device with same name but 'music_assistant' integration
                 from settings import GlobalResources
                 ma_docs = GlobalResources.ha_collection.similarity_search(f"{entity_id} music assistant", k=1)
                 ma_entity = None
                 
                 for d in ma_docs:
                     if d.metadata.get("integration") == "music_assistant":
                         # Check strict overlap if possible, or just trust the search
                         ma_entity = d.metadata.get("entity_id")
                         break
                 
                 if ma_entity:
                     ma_state = await get_entity_state(ma_entity, user_creds)
                     if ma_state in ["playing", "paused"]:
                         log.info(f"Transport Smart Swap: Swapping {entity_id} for active MA player {ma_entity}")
                         entity_id = ma_entity
                         integration = "music_assistant"

             state = await get_entity_state(entity_id, user_creds)
             if state in ["playing", "paused", "buffering"]:
                 log.info(f"Transport Short Circuit: Device {entity_id} is active, proceeding directly.")
                 domain = entity_id.split('.')[0]
                 return [await _execute_transport_command(intent, entity_id, domain, user_creds, integration, redis_client)]

    # ------------------------------------------------------------------
    # 2. FULL RESOLUTION PATH
    # ------------------------------------------------------------------
    clean_title = q_low

    # "On" Splitting
    if not entity_id and " on " in clean_title:
        parts = clean_title.rpartition(" on ")
        potential_content = parts[0].strip()
        potential_device = parts[2].strip()

        if len(potential_device) > 2:
            resolved_result = await smart_resolve_entity(potential_device, intent, ha_collection, is_music=strict_resolution, is_video=is_video_request)
            
            # Handle List Return (Batch)
            if isinstance(resolved_result, list):
                 if resolved_result:
                     log.info(f"['On' Split] Resolved {len(resolved_result)} entities. Executing Batch.")
                     return await execute_batch_command(resolved_result, intent, query, user_creds, ha_collection, redis_client)
                 else:
                     resolved_id, resolved_int = None, None
            else:
                 resolved_id, resolved_int = resolved_result

            if resolved_id:
                # --- START MASS INTELLIGENCE SWAP ---
                #, If we resolved a hardware device but assume music (or ambiguous), check if MA player exists.
                # This fixes "Play Brandon Lake on Office TV" -> resolved hardware TV -> failed video play.
                if not is_video_request and "music_assistant" not in resolved_int and "media_player" in resolved_id and intent not in ["turn_on", "turn_off", "toggle"]:
                     from settings import GlobalResources
                     # Search for MA alternative in DB
                     ma_docs = GlobalResources.ha_collection.similarity_search(f"{potential_device} music assistant", k=3)
                     for d in ma_docs:
                         if d.metadata.get("integration") == "music_assistant":
                             found_id = d.metadata.get("entity_id")
                             log.info(f"Mass Intelligence: Swapping hardware {resolved_id} -> MA Player {found_id}")
                             resolved_id = found_id
                             resolved_int = "music_assistant"
                             break
                # --- END MASS INTELLIGENCE SWAP ---

                # Update Context
                user = user_creds.get("user")
                if user and resolved_id:
                     _set_last_entity(redis_client, user, resolved_id)

                if strict_resolution and "music_assistant" not in resolved_int and not any(x in resolved_id.lower() for x in ["tv", "chromecast", "shield", "androidtv"]):
                    log.error(f"Strict Resolution failure: Resolved {resolved_id} ({resolved_int}) which is not MA/TV.")
                    return [{"status": "FAILURE", "message": f"I couldn't find a Music Assistant device named '{potential_device}'.", "entity_id": potential_device, "service": "media_command"}]

                entity_id = resolved_id
                integration = resolved_int
                clean_title = potential_content
                log.info(f"'On' Split Success: Device='{potential_device}' ({entity_id}), Content='{clean_title}'")
            else:
                 return [{"status": "FAILURE", "message": f"I couldn't find a device named '{potential_device}' to play media.", "entity_id": potential_device, "service": "media_command"}]

    # Standard Resolution
    if not entity_id:
        cleaned_for_res = clean_title
        # --- FIXED: Added transport verbs to cleaning list so 'skip' becomes empty string ---
        for p in ["turn on", "turn off", "toggle", "play", "stop", "open", "launch", "the", " on ", " please ",
                  "skip", "next", "previous", "back", "pause", "resume",
                  "this song", "the song", "current song", "track", "music"]:
            cleaned_for_res = cleaned_for_res.replace(p, " ")
        cleaned_for_res = cleaned_for_res.strip()

        if not cleaned_for_res:
            # THIS triggers the context memory retrieval
            entity_id = get_last_entity(redis_client, user_creds.get("user"))
        else:
            resolved_result = await smart_resolve_entity(cleaned_for_res, intent, ha_collection, is_music=strict_resolution, is_video=is_video_request)
            if isinstance(resolved_result, list):
                 if resolved_result:
                     log.info(f"[Standard] Resolved {len(resolved_result)} entities. Executing Batch.")
                     return await execute_batch_command(resolved_result, intent, query, user_creds, ha_collection, redis_client)
                 else:
                     entity_id, integration = None, None
            else:
                 entity_id, integration = resolved_result

        # --- START MASS INTELLIGENCE SWAP (Standard Path) ---
        if entity_id and "media_player" in entity_id and not is_video_request and "music_assistant" not in (integration or "") and intent not in ["turn_on", "turn_off", "toggle"]:
             from settings import GlobalResources
             # Search for MA alternative in DB
             clean_name = entity_id.split('.')[-1].replace('_', ' ')
             ma_docs = GlobalResources.ha_collection.similarity_search(f"{clean_name} music assistant", k=3)
             for d in ma_docs:
                 if d.metadata.get("integration") == "music_assistant":
                     found_id = d.metadata.get("entity_id")
                     log.info(f"Mass Intelligence: Swapping hardware {entity_id} -> MA Player {found_id}")
                     entity_id = found_id
                     integration = "music_assistant"
                     break
        # --- END MASS INTELLIGENCE SWAP ---

        # Update Context
        user = user_creds.get("user")
        if user and entity_id:
             _set_last_entity(redis_client, user, entity_id)

    if entity_id:
        domain = entity_id.split('.')[0]

    if not entity_id and intent not in ["turn_on", "turn_off", "toggle"]: 
         return {"status": "FAILURE", "message": "Could not determine which device you mean.", "entity_id": "N/A", "service": "media_command"}

    # 3. TRANSPORT REDIRECTION
    if is_transport:
        should_scan = False
        if not entity_id:
            should_scan = True
        else:
            state = await get_entity_state(entity_id, user_creds)
            if state in ["off", "unavailable"]:
                log.info(f"Targeted entity {entity_id} is {state}. Scanning for active players...")
                should_scan = True

        if should_scan:
            active_players = await get_active_media_players(user_creds)
            if active_players:
                if entity_id and entity_id in active_players:
                    pass
                else:
                    new_entity = active_players[0]
                    log.info(f"Redirecting {intent} from {entity_id or 'None'} to active device: {new_entity}")
                    entity_id = new_entity
            else:
                if not entity_id:
                     return [{"status": "FAILURE", "message": "No active media players found to control.", "entity_id": "N/A", "service": "media_command"}]

        domain = entity_id.split('.')[0]
        return [await _execute_transport_command(intent, entity_id, domain, user_creds, integration, redis_client, query)]


    if not entity_id:
         return [{"status": "FAILURE", "message": "Could not determine which device you mean.", "entity_id": "N/A", "service": "media_command"}]

    domain = entity_id.split('.')[0]
    service = intent
    service_data = {}

    # -------------------------------------------------
    # COLOR & BRIGHTNESS CONTROL
    # -------------------------------------------------
    if intent in ["set_color", "set_brightness", "dim", "brighten"]:
        log.debug(f"[COLOR/BRIGHTNESS] Handling intent='{intent}' for {entity_id}")
        
        if domain != "light":
            return [{"status": "FAILURE", "message": f"Color/brightness control only works with lights, not {domain} devices.", "entity_id": entity_id, "service": intent}]
        
        # Fetch device capabilities
        log.debug(f"[COLOR/BRIGHTNESS] Fetching capabilities for {entity_id}...")
        caps = await get_device_capabilities(entity_id, user_creds, redis_client)
        log.debug(f"[COLOR/BRIGHTNESS] Capabilities retrieved for {entity_id}")
        friendly_name = caps.get("friendly_name", entity_id.split('.')[-1].replace('_', ' ').title())
        
        # Validate color support
        if intent == "set_color":
            if not caps.get("has_color") and not caps.get("has_color_temp"):
                return [{
                    "status": "FAILURE", 
                    "message": f"{friendly_name} doesn't support color control. It's a simple on/off or brightness-only light.",
                    "entity_id": entity_id, 
                    "service": "set_color"
                }]
            
            # Parse requested color
            color_found = None
            color_name_found = None
            for color_name, rgb in COLOR_MAP.items():
                if color_name in q_low:
                    color_found = rgb
                    color_name_found = color_name
                    break
            
            if not color_found:
                return [{"status": "FAILURE", "message": "I couldn't determine which color you want. Try: red, blue, green, warm white, etc.", "entity_id": entity_id, "service": "set_color"}]
            
            # Smart mode selection based on device capabilities
            service = "turn_on"
            color_modes = caps.get("color_modes", [])
            
            # Check for any RGB variant (rgb, rgbw, rgbww)
            has_rgb_mode = any(mode.startswith("rgb") for mode in color_modes)
            if caps.get("has_color") and has_rgb_mode:
                # Full RGB color support (works for rgb, rgbw, rgbww modes)
                service_data = {"rgb_color": color_found}
                log.info(f"Setting {entity_id} to RGB {color_found}")
            
            elif caps.get("has_color") and "hs" in color_modes:
                # HS color mode (convert RGB to HS)
                r, g, b = [x/255.0 for x in color_found]
                max_c = max(r, g, b)
                min_c = min(r, g, b)
                diff = max_c - min_c
                
                # Hue calculation
                if diff == 0:
                    h = 0
                elif max_c == r:
                    h = (60 * ((g - b) / diff) + 360) % 360
                elif max_c == g:
                    h = (60 * ((b - r) / diff) + 120) % 360
                else:
                    h = (60 * ((r - g) / diff) + 240) % 360
                
                # Saturation calculation  
                s = 0 if max_c == 0 else (diff / max_c) * 100
                
                service_data = {"hs_color": [h, s]}
                log.info(f"Setting {entity_id} to HS [{h}, {s}]")
            
            elif caps.get("has_color_temp") and ("warm" in color_name_found or "cool" in color_name_found):
                # Fallback to color temperature for warm/cool white
                kelvin = 370 if "warm" in color_name_found else 200  # Warm = higher mireds (lower kelvin)
                service_data = {"color_temp": kelvin}
                log.info(f"Setting {entity_id} to color_temp {kelvin}")
            
            elif caps.get("has_color_temp"):
                # Device ONLY supports color temp (no RGB/HS), and user didn't request warm/cool
                return [{
                    "status": "FAILURE",
                    "message": f"{friendly_name} doesn't support full color. Try 'set to warm white' or 'set to cool white' instead.",
                    "entity_id": entity_id,
                    "service": "set_color"
                }]
            else:
                # Should not reach here, but safety fallback - try RGB anyway
                service_data = {"rgb_color": color_found}
                log.warning(f"No matching color mode for {entity_id}, trying RGB fallback")
        
        # Validate brightness support
        elif intent in ["set_brightness", "dim", "brighten"]:
            if not caps.get("has_brightness"):
                return [{
                    "status": "FAILURE",
                    "message": f"{friendly_name} is an on/off only light and doesn't support brightness control.",
                    "entity_id": entity_id,
                    "service": "set_brightness"
                }]
            
            brightness = None
            
            # Look for percentage (e.g., "50%", "100%")
            pct_match = re.search(r"(\d+)\s*%", query)
            if pct_match:
                pct = int(pct_match.group(1))
                brightness = int((pct / 100.0) * 255)
            
            # Relative adjustments
            elif intent == "dim":
                brightness = 70  # ~30% brightness
            elif intent == "brighten":
                brightness = 255  # Max brightness
            
            if brightness is None:
                brightness = 128  # Default to 50%
            
            service = "turn_on"
            service_data = {"brightness": max(1, min(255, brightness))}
            log.info(f"Setting {entity_id} brightness to {brightness}")
        
        return [await execute_ha_service(domain, service, entity_id, user_creds, service_data, redis_client)]

    # -------------------------------------------------
    # POWER, NAVIGATION
    # -------------------------------------------------
    if intent in ["turn_on", "turn_off", "toggle"] or intent.startswith("nav_"):
        # SMART POWER SWAP: Prefer parent 'TV' entity over 'Chromecast' for power commands
        # SMART POWER SWAP: Always prefer a 'remote' entity for power commands on media players
        # This handles Android TV, Roku, etc. where the media_player might be a cast target or less capable.
        if intent in ["turn_on", "turn_off", "toggle"] and domain == "media_player":
            from settings import GlobalResources
            
            # 1. Naive Check: media_player.foo -> remote.foo
            candidates = [entity_id.replace("media_player.", "remote.")]
            
            # 2. Vector Search: Find "remote" entities related to this device
            # e.g. "Office TV Chrome" -> finds "remote.office_tv"
            search_query = entity_id.split(".")[-1].replace("_", " ")
            docs = GlobalResources.ha_collection.similarity_search(f"{search_query} remote", k=3)
            for d in docs:
                if d.metadata.get("domain") == "remote":
                    candidates.append(d.metadata.get("entity_id"))
            
            # 3. Validation: Pick the first candidate that actually exists/is effective
            best_remote = None
            for cand in candidates:
                if cand == entity_id: continue
                
                # Check state to verify existence
                st = await get_entity_state(cand, user_creds)
                if st not in ["unknown", "unavailable", None]:
                    best_remote = cand
                    break
            
            if best_remote:
                log.info(f"Smart Power Swap: Switching {entity_id} -> {best_remote} (Remote Integration)")
                entity_id = best_remote
                domain = "remote"

        # ------------------------------------------------------------------------
        # EXECUTE ACTION BASED ON INTENT
        # ------------------------------------------------------------------------
        log.info(f"[DISPATCH] Dispatching intent: '{intent}' (Type: {type(intent)}) for entity: {entity_id}")

        if intent.startswith("nav_"):
            # Special handling for media player navigation commands
            if intent.startswith("nav_") and domain == "media_player":
                # Map navigation intents to D-pad commands
                cmd_map = {
                    "nav_up": "DPAD_UP", "nav_down": "DPAD_DOWN", 
                    "nav_left": "DPAD_LEFT", "nav_right": "DPAD_RIGHT",
                    "nav_enter": "DPAD_CENTER", "nav_back": "BACK", "nav_home": "HOME"
                }
                remote_cmd = cmd_map.get(intent)
                if remote_cmd:
                    # Try to find associated remote
                    remote_id = entity_id.replace("media_player", "remote")
                    # Helper to check if remote exists
                    async def _has_remote(rid):
                        s = await get_entity_state(rid, user_creds)
                        return s and s != "unknown"
                    if await _has_remote(remote_id):
                        return [await execute_ha_service("remote", "send_command", remote_id, user_creds, {"command": remote_cmd}, redis_client)]
            
            cmd_map = {
                "nav_up": "DPAD_UP", "nav_down": "DPAD_DOWN",
                "nav_left": "DPAD_LEFT", "nav_right": "DPAD_RIGHT",
                "nav_enter": "DPAD_CENTER", "nav_back": "BACK",
                "nav_home": "HOME",
            }
            service = "send_command"
            domain = "remote"
            service_data = {"command": cmd_map.get(intent)}
            if "media_player" in entity_id:
                entity_id = entity_id.replace("media_player", "remote")
        elif domain == "remote":
            service = "turn_" + intent.split("_")[1]
        if domain not in ["light", "switch", "remote", "media_player"]:
            domain = "homeassistant"
        return [await execute_ha_service(domain, service, entity_id, user_creds, service_data, redis_client)]

    # -------------------------------------------------
    # MEDIA (PLAY / OPEN APP)
    # -------------------------------------------------
    if intent in ["play_media", "open_app", "watch_video", "view_content"]:

        # APP LAUNCH
        # APP LAUNCH
        # If integration is Android TV or device has apps, try to resolve app intent
        app_name_candidate = None
        
        if "android" in integration or "app" in q_low or intent == "open_app":
             # Extract App Name (naive)
             app_name_candidate = q_low.replace("open ", "").replace("launch ", "").strip()
             # Or iterate known apps
             matched_app = None
             from logic.android_tv_ops import APP_IDS, launch_app as atv_launch
             
             for app_key in APP_IDS.keys():
                 if app_key in q_low:
                     matched_app = app_key
                     break
             
             if matched_app or intent == "open_app": # If explicit open_app, execute even if not in known list (pass raw)
                 target_app = matched_app if matched_app else app_name_candidate
                 log.info(f"Delegating App Launch '{target_app}' on {entity_id} to android_tv_ops")
                 return [await atv_launch(entity_id, target_app, user_creds, redis_client)]
        
        # WebOS App Launch
        if "webostv" in integration or intent == "open_app":
             from logic.webos_ops import launch_app as webos_launch
             # Naive extraction again if not already done
             if not app_name_candidate:
                  app_name_candidate = q_low.replace("open ", "").replace("launch ", "").strip()
             
             # WebOS sources are usually case sensitive or specific names like "YouTube", "Netflix"
             # Ideally we'd have a mapping or fuzzy search the source list, but simple pass-through is a start.
             log.info(f"Delegating App Launch '{app_name_candidate}' on {entity_id} to webos_ops")
             return await webos_launch(entity_id, app_name_candidate, user_creds, redis_client)

        # Roku App Launch
        if "roku" in integration or intent == "open_app":
             from logic.roku_ops import launch_app as roku_launch
             # Naive extraction if not done yet
             if not app_name_candidate:
                  app_name_candidate = q_low.replace("open ", "").replace("launch ", "").strip()
             
             # Roku supports app names or app IDs
             log.info(f"Delegating App Launch '{app_name_candidate}' on {entity_id} to roku_ops")
             return await roku_launch(entity_id, app_name_candidate, user_creds, redis_client)

        # ------------------------------------------------------------------------
        # UNIVERSAL VIDEO INTENT ("WATCH" / "VIEW")
        # ------------------------------------------------------------------------
        # Strict separation: "Watch X" -> Video Search. "Play X" -> Music.
        
        # Check explicit video intent

        is_watch_intent = bool(re.search(r"^\b(watch|view)\b", q_low))
        
        target_url = None
        
        # If user says "Watch...", we try to find a video URL if one isn't provided
        if is_watch_intent:
             # Remove trigger word
             video_query = re.sub(r"^\b(watch|view)\b", "", q_low).strip()
             
             # Check if it's already a URL
             if re.search(r"(youtube\.com|youtu\.be|rumble\.com|\.mp4|\.mkv)", video_query):
                 target_url = video_query
             else:
                 # SEARCH: Use existing web search tool
                 log.info(f"[Search-Play] 'Watch' intent detected. Searching for video: {video_query}")
                 from logic.web_search import tool_web_search
                 
                 # Clean Query: Remove device name from search string to avoid "video on office tv" noise
                 # Aggressive regex to remove "on [device/room]" patterns
                 clean_q = re.sub(r"\b(on|in)\s+(the\s+)?(office|tv|bedroom|kitchen|speaker|remote|media|living|room|den|basement|roku|chromecast|shield|monitor|display)\b.*", "", video_query, flags=re.IGNORECASE).strip()
                 
                 # Basic fallback
                 clean_q = clean_q.replace(" on ", " ").strip()
                 if device_name:
                     clean_q = re.sub(re.escape(device_name), "", clean_q, flags=re.IGNORECASE).strip()
                 
                 # Also try to strip friendly_name if available
                 try:
                      # Determine friendly name from entity_id lookup (cached/local)
                      # We don't have easy access to state here without an async call, but we can try simple heuristics
                      # or rely on the general " on " removal.
                      pass
                 except:
                      pass

                 # Search for YouTube specifically (most reliable deep linking)
                 # We use site:youtube.com to force video results
                 search_q = f"site:youtube.com {clean_q}"
                 search_result = await tool_web_search(search_q)
                 
                 # Extract first URL
                 match = re.search(r'(https?://(?:www\.)?(?:youtube\.com/watch\?v=[^"\s]+|youtu\.be/[^"\s]+))', search_result)
                 if match:
                      target_url = match.group(1)
                      log.info(f"[Search-Play] Found video URL: {target_url}")
                 else:
                      return {"status": "FAILURE", "message": f"Could not find any video results for '{video_query}'"}

        # Fallback: Check if it's a raw video URL even without "watch" (existing logic)
        elif re.search(r"(youtube\.com|youtu\.be|rumble\.com|\.mp4|\.mkv)", q_low):
             target_url = q_low.strip()

        # ------------------------------------------------------------------------
        # EXECUTE VIDEO PLAYBACK
        # ------------------------------------------------------------------------
        if target_url:
            log.info(f"[DeepLink] Routing Video URL: {target_url}")
            
            # 1. Android TV Deep Link
            if "android" in integration:
                from logic.android_tv_ops import play_video as atv_play
                return await atv_play(entity_id, target_url, user_creds)
                
            # 2. WebOS Deep Link
            elif "webostv" in integration:
                from logic.webos_ops import play_url as webos_play
                return await webos_play(entity_id, target_url, user_creds, redis_client)
                
            # 3. Roku Deep Link
            elif "roku" in integration:
                from logic.roku_ops import play_media_url as roku_play
                return await roku_play(entity_id, target_url, user_creds, redis_client)

        # ------------------------------------------------------------------------
        # UNIVERSAL VIDEO DEEP LINKING (YouTube, Rumble, etc.)
        # ------------------------------------------------------------------------
        # Check for video URLs to route to TV integrations instead of Music Assistant


        # --- SMART CONTENT TYPE DETECTION (Music Assistant Fallback) ---
        ctype = "music" # Default
        detected_specific_type = False

        if is_music_request:
            if re.search(r"\b(album|record)\b", q_low):
                ctype = "album"
                detected_specific_type = True
            elif re.search(r"\b(artist|band)\b", q_low):
                ctype = "artist"
                detected_specific_type = True
            elif re.search(r"\b(playlist)\b", q_low):
                ctype = "playlist"
                detected_specific_type = True
            elif re.search(r"\b(track|song)\b", q_low):
                ctype = "track"
                detected_specific_type = True
            elif re.search(r"\b(radio|station)\b", q_low):
                ctype = "radio"
                detected_specific_type = True
            elif re.search(r"\b(podcast)\b", q_low):
                ctype = "podcast"
                detected_specific_type = True
        
        if is_audiobook_request:
            ctype = "audiobook"
            detected_specific_type = True

        # TV Logic: TVs play video ONLY if explicitly video request
        # Fix: Don't force video for ambiguous music requests like "Play Brandon Lake"
        is_tv = any(x in entity_id.lower() for x in ["tv", "chromecast", "shield", "androidtv"])
        if is_tv and is_video_request:
            ctype = "video"
        elif not is_video_request and not is_music_request:
            # Ambiguous request (no keywords): default to music for play_media intent
            ctype = "music"

        # Fallback Logic for Non-MA Devices:
        if detected_specific_type and "music_assistant" not in integration:
            log.info(f"Target {entity_id} is not Music Assistant. Downgrading type '{ctype}' to 'music'.")
            ctype = "music"

        # --- CONTENT CLEANING ---
        original_title = clean_title
        
        # Aggressively remove the device name from the query if present
        # logic: remove "on {friendly_name}", "on {entity_id}", "on {device_name}"
        if entity_id:
             # Get friendly name
             caps = await get_device_capabilities(entity_id, user_creds, redis_client)
             fname = caps.get("friendly_name", "").lower()
             ename = entity_id.split(".")[-1].replace("_", " ").lower()
             
             # Remove "on {name}" patterns
             for name in [fname, ename, "office tv", "master bedroom samsung"]: # Add known aliases if needed
                 if name and name in clean_title:
                     clean_title = re.sub(f"\\b(on|in|at)?\\s*{re.escape(name)}\\b", " ", clean_title)
        
        # Only remove control/action words

        clean_title = re.sub(r"\b(play|please|from|on|open|launch|playback|listen to)\b", " ", clean_title)

        # Only remove content TYPE keywords IF the request is for MUSIC
        if is_music_request:
            clean_title = re.sub(r"\b(music|song|album|track|playlist|artist|radio|podcast)\b", " ", clean_title)

        # Remove filler words
        clean_title = re.sub(r"\b(by|the|some|a|an)\b", " ", clean_title)

        clean_title = re.sub(r"[^\w\s]", " ", clean_title) 
        clean_title = re.sub(r"\s+", " ", clean_title).strip()

        if len(clean_title) < 3:
             clean_title = original_title
             log.warning(f"Content cleaning resulted in empty string. Using original content: {clean_title}")

        if not clean_title:
             return {"status": "FAILURE", "message": "I understood the device, but not what to play. Please specify content.", "entity_id": entity_id, "service": "media_command"}

        state = await get_entity_state(entity_id, user_creds)
        if state in ["off", "unavailable"]:
            await execute_ha_service(domain, "turn_on", entity_id, user_creds, redis_client=redis_client)

        # --- CRITICAL FIX: Use 'music_assistant.play_media' for MA devices ---
        # --- CRITICAL FIX: Use 'music_assistant.play_media' for MA devices (Delegated) ---
        if "music_assistant" in integration:
            try:
                from logic.music_assistant_ops import play_media as ma_play_media
                log.info(f"Delegating Music Assistant Play on {entity_id} to music_assistant_ops")
                
                # Attempt specific type
                result = await ma_play_media(entity_id, clean_title, ctype, user_creds)
                
                # Fallback to 'search' 
                if result.get("status") == "FAILURE":
                     log.info("MA play_media failed with specific type. Retrying with media_type='search'...")
                     result = await ma_play_media(entity_id, clean_title, "search", user_creds)
                
                # Maintain compatibility with existing result structure
                # The existing code expects a certain dict shape for logging, though the core execution is done.
                if result["status"] == "SUCCESS":
                    # Mocking the HA service result format for consistency
                    return [{"status": "SUCCESS", "message": result["message"], "service": "ma_play", "entity_id": entity_id}]
                else: 
                     return {"status": "FAILURE", "message": result["message"], "entity_id": entity_id}
            
            except ImportError:
                 log.error("Failed to import music_assistant_ops. Falling back to inline logic.")
                 # Fallback inline logic would go here if we were paranoid, but for now we trust the import.
                 return {"status": "FAILURE", "message": "Internal Logic Error (MA Import)", "entity_id": entity_id}
        else:
            # Standard Media Player Service
            
            # If content is generic/garbage (failed to clean meaningfully) and it's a TV/Generic, 
            # try RESUME (media_play) instead of play_media with garbage.
            # "original_title" would be "Play some music", "clean_title" might be "Play some music" if <3 char logic hit.
            is_generic = clean_title.lower() in ["music", "some music", "anything", "play some music"] or len(clean_title) > 20 and "play" in clean_title.lower()
            
            if is_generic or not clean_title:
                log.info(f"Generic content request on {entity_id}. Attempting RESUME (media_play).")
                result = await execute_ha_service(domain, "media_play", entity_id, user_creds, {}, redis_client)
            else:
                log.info(f"Executing Standard Play on {entity_id} Type: {ctype}")
                std_service_data = {
                    "media_content_id": clean_title,
                    "media_content_type": ctype
                }
                result = await execute_ha_service(domain, "play_media", entity_id, user_creds, std_service_data, redis_client)

            # Self-Healing for generic players
            if result.get("status") == "FAILURE" and "500" in result.get("message", ""):
                new_type = "video" if ctype == "music" else "music"
                log.info(f"Self-Healing: Retrying '{clean_title}' as '{new_type}' on {entity_id}")
                service_data = {"media_content_id": clean_title, "media_content_type": new_type}
                result = await execute_ha_service(domain, "play_media", entity_id, user_creds, service_data, redis_client)

            # FINAL FALLBACK: If play_media failed with 500 (Server Error), and it's a TV/Remote, try waking it up or just logging clearly.
            if result.get("status") == "FAILURE" and "500" in result.get("message", ""):
                 log.error(f"Persistent 500 Error on {entity_id}. Device might be unresponsive or integration broken.")
                 # Optional: Try one last ditch 'turn_on' if we suspect sleep?
                 # await execute_ha_service(domain, "turn_on", entity_id, user_creds, redis_client=redis_client)

            return result

    return {"status": "FAILURE", "message": f"Media command '{intent}' could not be executed.", "entity_id": entity_id, "service": intent}

async def _execute_transport_command(intent: str, entity_id: str, domain: str, user_creds: dict, integration: str, redis_client, query: str = ""):
    """Executes media transport command with self-healing fallback prioritizing remote control. Returns structured dict."""
    log.info(f"[_execute_transport_command] Intent='{intent}' Entity='{entity_id}'")

    if intent == "stop_media":
        # Check state first to avoid 500 error on off devices
        state = await get_entity_state(entity_id, user_creds)
        if state in ["off", "unavailable", "idle"]:
             return {"status": "SUCCESS", "message": f"{entity_id} is already stopped.", "entity_id": entity_id, "service": "media_stop", "new_state": state}
        return await execute_ha_service("media_player", "media_stop", entity_id, user_creds, {}, redis_client)

    is_mass = "music_assistant" in integration

    # Helper to check if remote exists
    async def _has_remote(rid):
        s = await get_entity_state(rid, user_creds)
        return s and s != "unknown"

    if intent == "media_next":
        remote_id = entity_id.replace("media_player", "remote")

        # --- FIXED: Use service FIRST for everyone. MA stops here. Others fallback. ---
        result = await execute_ha_service("media_player", "media_next_track", entity_id, user_creds, {}, redis_client)

        if not is_mass and result.get("status") == "FAILURE":
            if await _has_remote(remote_id):
                log.info(f"Next track failed on {entity_id}. Falling back to remote: {remote_id}")
                result = await execute_ha_service("remote", "send_command", remote_id, user_creds, {"command": "DPAD_RIGHT"}, redis_client)

        return result

    elif intent == "media_previous":
        remote_id = entity_id.replace("media_player", "remote")

        # --- FIXED: Use service FIRST for everyone. MA stops here. Others fallback. ---
        result = await execute_ha_service("media_player", "media_previous_track", entity_id, user_creds, {}, redis_client)

        if not is_mass and result.get("status") == "FAILURE":
            if await _has_remote(remote_id):
                log.info(f"Previous track failed on {entity_id}. Falling back to remote: {remote_id}")
                result = await execute_ha_service("remote", "send_command", remote_id, user_creds, {"command": "DPAD_LEFT"}, redis_client)

        return result

    # --- Volume Control Commands ---
    elif intent in ["volume_set", "volume_up", "volume_down", "volume_mute"]:
        import re
        # Extract volume level from query (e.g., "30%", "60 percent", "to 50")
        volume_match = re.search(r'(\d+)\s*%?', query)
        volume_level = None
        if volume_match:
            volume_level = int(volume_match.group(1)) / 100.0  # Convert to 0.0-1.0
            volume_level = max(0.0, min(1.0, volume_level))  # Clamp
        
        if intent == "volume_set":
            if volume_level is not None:
                result = await execute_ha_service(
                    "media_player", "volume_set", entity_id, user_creds, 
                    {"volume_level": volume_level}, redis_client
                )
                return result
            else:
                return {"status": "FAILURE", "message": "Could not parse volume level from command.", "entity_id": entity_id, "service": "volume_set"}
        
        elif intent == "volume_up":
            if volume_level is not None:
                # Set to specific level
                result = await execute_ha_service(
                    "media_player", "volume_set", entity_id, user_creds, 
                    {"volume_level": volume_level}, redis_client
                )
            else:
                # Just bump up by default increment
                result = await execute_ha_service(
                    "media_player", "volume_up", entity_id, user_creds, {}, redis_client
                )
            return result
        
        elif intent == "volume_down":
            if volume_level is not None:
                result = await execute_ha_service(
                    "media_player", "volume_set", entity_id, user_creds, 
                    {"volume_level": volume_level}, redis_client
                )
            else:
                result = await execute_ha_service(
                    "media_player", "volume_down", entity_id, user_creds, {}, redis_client
                )
            return result
        
        elif intent == "volume_mute":
            should_mute = True
            log.info(f"[DEBUG_MUTE] Raw Query: '{query}'")
            # Fix: 'mute office' contains 'mute off', so we need word boundaries
            if "unmute" in query.lower() or re.search(r"\bmute\s+off\b", query.lower()):
                 should_mute = False
            log.info(f"[DEBUG_MUTE] Determined should_mute: {should_mute}")
            
            result = await execute_ha_service(
                "media_player", "volume_mute", entity_id, user_creds, 
                {"is_volume_muted": should_mute}, redis_client
            )
            return result

    return {"status": "FAILURE", "message": f"Media command '{intent}' could not be executed.", "entity_id": entity_id, "service": intent}

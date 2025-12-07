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
    r"\bturn\s+.+\s+to\s+(red|blue|green|purple|orange|yellow|pink|white|warm|cool)": "set_color",
    r"\b(dim|darken|lower)\b": "dim",
    r"\b(brighten|brighter|increase)\b": "brighten",
    r"\b(brightness|bright|set.+\d+%)": "set_brightness",
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
                        capabilities["has_play_media"] = bool(features & 512)
                    
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
            capabilities["has_play_media"] = bool(features & 512)
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
        "friendly_name": entity_id.split(".")[-1].replace("_", " ").title(),
        "service": f"{domain}.{service}"
    }

# Insert this before smart_resolve_entity (around line 447)

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
    # Detect number pattern
    pattern_type, pattern_data = detect_number_pattern(query)
    
    if not pattern_type:
        # No pattern - use single entity resolution
        entity_id, integration = await smart_resolve_entity(query, intent, ha_collection)
        if entity_id:
            return [(entity_id, integration)]
        return []
    
    log.info(f"[PATTERN] Detected pattern '{pattern_type}' in query")
    
    # Pattern detected - get all candidates and filter
    docs = await run_blocking(lambda: safe_similarity_search(ha_collection, query, k=30))
    if not docs:
        return []
    
    # Build candidates list with domain filtering
    candidates = []
    friendly_names = {}
    
    for d in docs:
        eid = d.metadata.get("entity_id")
        integration = d.metadata.get("integration", "unknown")
        friendly_name = d.metadata.get("friendly_name", eid)
        
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
        
        candidates.append((eid, integration))
        friendly_names[eid] = friendly_name
    
    # Filter by pattern
    matching_entities = filter_entities_by_pattern(
        candidates,
        pattern_type,
        pattern_data,
        friendly_names
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
        'failure_count': failure_count
    }
# Insert this before smart_resolve_entity (around line 447)

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
    # Detect number pattern
    pattern_type, pattern_data = detect_number_pattern(query)
    
    if not pattern_type:
        # No pattern - use single entity resolution
        entity_id, integration = await smart_resolve_entity(query, intent, ha_collection)
        if entity_id:
            return [(entity_id, integration)]
        return []
    
    log.info(f"[PATTERN] Detected pattern '{pattern_type}' in query")
    
    # Pattern detected - get all candidates and filter
    docs = await run_blocking(lambda: safe_similarity_search(ha_collection, query, k=30))
    if not docs:
        return []
    
    # Build candidates list with domain filtering
    candidates = []
    friendly_names = {}
    
    for d in docs:
        eid = d.metadata.get("entity_id")
        integration = d.metadata.get("integration", "unknown")
        friendly_name = d.metadata.get("friendly_name", eid)
        
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
        
        candidates.append((eid, integration))
        friendly_names[eid] = friendly_name
    
    # Filter by pattern
    matching_entities = filter_entities_by_pattern(
        candidates,
        pattern_type,
        pattern_data,
        friendly_names
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
async def smart_resolve_entity(query_name: str, intent: str, ha_collection, is_music: bool = False, is_video: bool = False) -> tuple:
    """
    Resolves the best entity based on query and intent.
    When is_music=True, it prioritizes Music Assistant devices.
    When is_video=True, it prioritizes Hardware/Android TV devices.
    """
    log.info(f"DEBUG: Entering smart_resolve_entity. Q='{query_name}' Intent='{intent}' Collection={ha_collection}")
    
    if not ha_collection or not query_name.strip():
        log.warning("DEBUG: Early exit - No collection or empty query.")
        return (None, None)

    # Search top 15 to capture relevant but potentially lower-ranked MA entities
    docs = await run_blocking(lambda: safe_similarity_search(ha_collection, query_name, k=15))
    log.info(f"DEBUG: Search returned {len(docs) if docs else 0} docs.")
    
    if not docs:
        return (None, None)

    candidates = []
    for d in docs:
        eid = d.metadata.get("entity_id")
        integration = d.metadata.get("integration", "unknown")
        if eid:
            domain = eid.split('.')[0]

            # Domain Filtering
            if intent in ["play_media", "open_app", "media_next", "media_previous", "stop_media"]:
                if domain not in ["media_player", "group", "script"]:
                    continue
            
            # Color and brightness commands only work on lights
            if intent in ["set_color", "set_brightness", "dim", "brighten"]:
                if domain != "light":
                    continue

            if intent in ["turn_on", "turn_off", "toggle"]:
                 if domain in ["sensor", "binary_sensor", "sun", "weather", "remote"]:
                     # Exclude remotes from power commands
                     continue
                 if "music_assistant" in integration:
                     # Music Assistant cannot control device power
                     continue

            candidates.append((eid, integration))

    if not candidates:
        return (None, None)

    q_low = query_name.lower()
    
    # --- ENFORCED PRIORITY FOR MUSIC ---
    # Only runs if strict resolution (play_media + music keywords) is active.
    if is_music:
        ma_candidate = None
        tv_candidate = None

        # Pass 1: Find the absolute highest ranked MA and TV candidates
        for eid, integration in candidates:
            # Look for MA candidate first
            if "music_assistant" in integration:
                # Found the most relevant MA entity based on search rank. Use it immediately.
                ma_candidate = (eid, integration)
                break 

            # Look for generic TV/Chromecast candidate as a fallback
            if eid.startswith("media_player.") and any(x in eid.lower() for x in ["tv", "chromecast", "shield", "androidtv"]):
                # Keep the best ranked TV as a fallback
                if tv_candidate is None:
                    tv_candidate = (eid, integration)

        # Priority 1: Use Music Assistant entity if found.
        if ma_candidate:
            log.info(f"Strict Music Mode: Prioritizing MA candidate: {ma_candidate[0]}")
            return ma_candidate

        # Priority 2: Use the best ranked generic TV/Chromecast if MA not found.
        if tv_candidate:
            log.info(f"Strict Music Mode: Falling back to generic TV candidate: {tv_candidate[0]}")
            return tv_candidate

        # Priority 3: Fail if no suitable music device found.
        log.warning(f"Strict Music Mode: No suitable music player found for '{query_name}'. Returning None.")
        return (None, None)

    # --- POWER/HARDWARE PRIORITY (Turn On/Off) ---
    # Prefer hardware integrations (Android TV, Roku, etc.) over software streams (Music Assistant) for power.
    if intent in ["turn_on", "turn_off", "toggle"]:
        hw_candidate = None
        # Common hardware integrations that control physical power
        HW_INTEGRATIONS = ["androidtv", "cast", "google_cast", "webostv", "braviatv", "roku", "apple_tv", "samsungtv", "esphome", "tasmota", "shelly", "hue", "lutron_caseta", "kodi", "vlc"]
        
        for eid, integration in candidates:
             if integration in HW_INTEGRATIONS:
                 hw_candidate = (eid, integration)
                 break
             
             # Heuristic fallback: If it's NOT Music Assistant, and has "TV" in the ID, it's likely the hardware.
             if "music_assistant" not in integration and any(x in eid.lower() for x in ["tv", "projector", "receiver"]):
                 if not hw_candidate:
                     hw_candidate = (eid, integration)

        if hw_candidate:
             log.info(f"Power Priority: Resolved '{query_name}' to hardware entity {hw_candidate[0]} ({hw_candidate[1]})")
             return hw_candidate

    # --- NON-STRICT / GENERIC LOGIC (For power, nav, non-music play) ---

    preferred_type = "generic"

    if intent == "play_media":
         # Fallback for play_media if NOT strictly music (meaning video/generic)
         # If app package detected, prefer Android
         if any(app in q_low for app in APP_PACKAGES):
             preferred_type = "android"
         
         # EXCEPTION: If is_video flag passed (Watch/Video keywords), prefer hardware
         if is_video:
             preferred_type = "android"

    elif intent in ["open_app"]:
        preferred_type = "android"
    
    elif intent in ["turn_on", "turn_off", "toggle"] or intent.startswith("nav_"):
        # Hardware Priority for Power/Nav
        # "Turn on TV" -> Android TV / Hardware
        preferred_type = "hardware"

    log.info(f"Smart Resolving '{query_name}' Intent '{intent}' Pref '{preferred_type}' Candidates {candidates[:3]}...")

    # Standard Preference Logic for generic/remote/hardware
    for eid, integration in candidates:
        if preferred_type == "android" and ("media_player" in eid) and ("androidtv" in integration or "known_hardware" in integration):
             return eid, integration
        
        if preferred_type == "hardware":
             # Prioritize hardware integrations for power
             if integration in ["androidtv", "webostv", "braviatv", "roku", "apple_tv", "samsungtv", "esphome", "tasmota", "shelly", "hue", "lutron_caseta", "kodi", "vlc"]:
                 return eid, integration
             if "music_assistant" not in integration and any(x in eid.lower() for x in ["tv", "projector", "receiver"]):
                 return eid, integration

        if preferred_type == "remote" and ("remote" in eid or "androidtv" in integration):
            return eid, integration

    # Default fallback for generic intents
    return candidates[0]

async def handle_media_command(intent: str, query: str, entity_id: str, user_creds: dict, ha_collection, redis_client):
    """
    Handles media command and ensures a structured dictionary is returned.
    Supports multi-device pattern matching (even/odd/range/list/all).
    """
    q_low = query.lower()
    integration = "unknown"
    
    # --- PATTERN DETECTION FOR MULTI-DEVICE CONTROL ---
    # If no specific entity_id provided, check for patterns
    if not entity_id:
        pattern_type, pattern_data = detect_number_pattern(query)
        if pattern_type:
            log.info(f"[PATTERN] Detected '{pattern_type}' pattern - attempting batch execution")
            entities = await resolve_multiple_entities_with_pattern(query, intent, ha_collection)
            if entities:
                return await execute_batch_command(
                    entities, intent, query, user_creds, ha_collection, redis_client
                )
            else:
                return {
                    'status': 'FAILURE',
                    'message': f'No devices found matching pattern "{pattern_type}"',
                    'service': intent
                }

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

        if intent != original_intent:
            log.info(f"Sanitized intent from '{original_intent}' to '{intent}'")
    # ------------------------------------------------------------------------

    # 1. EARLY MUSIC DETECTION
    music_keywords = ["music", "song", "artist", "album", "track", "playlist", "radio"]
    # NEW: Audiobooks
    audiobook_keywords = ["read", "book", "chapter", "audiobook"]
    video_keywords = ["movie", "film", "show", "video", "youtube", "netflix", "watch", "tv"]
    
    is_music_request = any(x in q_low for x in music_keywords)
    is_audiobook_request = any(x in q_low for x in audiobook_keywords)
    is_video_request = any(x in q_low for x in video_keywords)
    
    # For play_media intent, default to music mode UNLESS explicitly requesting video
    strict_resolution = ((is_music_request or is_audiobook_request) or (intent == "play_media" and not is_video_request))
    is_transport = intent in ["media_next", "media_previous", "stop_media"]

    # --- TRANSPORT SHORT CIRCUIT (High Confidence/Explicit Target) ---
    if is_transport:
        device_match = re.search(r"\b(on|in)\s+(the\s+)?(office|tv|bedroom|kitchen|speaker|remote|media)\b", q_low)

        # 2a. Resolve device name from query if present
        if not entity_id and device_match:
             potential_device_name = q_low.split(device_match.group(1))[-1].strip()
             if potential_device_name:
                 # Pass strict_resolution=True if we are skipping tracks, to prefer MA entities
                 resolved_id, resolved_int = await smart_resolve_entity(potential_device_name, intent, ha_collection, is_music=True, is_video=is_video_request)
                 if resolved_id:
                    log.info(f"Transport Short Circuit: Found explicit device {resolved_id} from query.")
                    entity_id = resolved_id
                    integration = resolved_int

        # 2b. If we have an entity_id now (from Redis or short circuit), check its state
        if entity_id:
             # Check if an MA version exists and is active, swap if needed
             if "music_assistant" not in integration:
                 ma_entity_guess = f"{entity_id}_2"
                 ma_state = await get_entity_state(ma_entity_guess, user_creds)
                 if ma_state in ["playing", "paused"]:
                     log.info(f"Transport Smart Swap: Swapping {entity_id} for active MA player {ma_entity_guess}")
                     entity_id = ma_entity_guess
                     integration = "music_assistant"

             state = await get_entity_state(entity_id, user_creds)
             if state in ["playing", "paused", "buffering"]:
                 log.info(f"Transport Short Circuit: Device {entity_id} is active, proceeding directly.")
                 domain = entity_id.split('.')[0]
                 return await _execute_transport_command(intent, entity_id, domain, user_creds, integration, redis_client)

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
            resolved_id, resolved_int = await smart_resolve_entity(potential_device, intent, ha_collection, is_music=strict_resolution, is_video=is_video_request)

            if resolved_id:
                if strict_resolution and "music_assistant" not in resolved_int and not any(x in resolved_id.lower() for x in ["tv", "chromecast", "shield", "androidtv"]):
                    log.error(f"Strict Resolution failure: Resolved {resolved_id} ({resolved_int}) which is not MA/TV.")
                    return {"status": "FAILURE", "message": f"I couldn't find a Music Assistant device named '{potential_device}'.", "entity_id": potential_device, "service": "media_command"}

                entity_id = resolved_id
                integration = resolved_int
                clean_title = potential_content
                log.info(f"'On' Split Success: Device='{potential_device}' ({entity_id}), Content='{clean_title}'")
            else:
                 return {"status": "FAILURE", "message": f"I couldn't find a device named '{potential_device}' to play media.", "entity_id": potential_device, "service": "media_command"}

    # Standard Resolution
    if not entity_id:
        cleaned_for_res = clean_title
        # --- FIXED: Added transport verbs to cleaning list so 'skip' becomes empty string ---
        for p in ["turn on", "turn off", "toggle", "play", "stop", "open", "launch", "the", " on ", " please ",
                  "skip", "next", "previous", "back", "pause", "resume"]:
            cleaned_for_res = cleaned_for_res.replace(p, " ")
        cleaned_for_res = cleaned_for_res.strip()

        if not cleaned_for_res:
            # THIS triggers the context memory retrieval
            entity_id = get_last_entity(redis_client, user_creds.get("user"))
        else:
            entity_id, integration = await smart_resolve_entity(cleaned_for_res, intent, ha_collection, is_music=strict_resolution, is_video=is_video_request)

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
                     return {"status": "FAILURE", "message": "No active media players found to control.", "entity_id": "N/A", "service": "media_command"}

        domain = entity_id.split('.')[0]
        return await _execute_transport_command(intent, entity_id, domain, user_creds, integration, redis_client)


    if not entity_id and intent not in ["turn_on", "turn_off", "toggle"]:
         return {"status": "FAILURE", "message": "Could not determine which device you mean.", "entity_id": "N/A", "service": "media_command"}

    domain = entity_id.split('.')[0]
    service = intent
    service_data = {}

    # -------------------------------------------------
    # COLOR & BRIGHTNESS CONTROL
    # -------------------------------------------------
    if intent in ["set_color", "set_brightness", "dim", "brighten"]:
        log.debug(f"[COLOR/BRIGHTNESS] Handling intent='{intent}' for {entity_id}")
        
        if domain != "light":
            return {"status": "FAILURE", "message": f"Color/brightness control only works with lights, not {domain} devices.", "entity_id": entity_id, "service": intent}
        
        # Fetch device capabilities
        log.debug(f"[COLOR/BRIGHTNESS] Fetching capabilities for {entity_id}...")
        caps = await get_device_capabilities(entity_id, user_creds, redis_client)
        log.debug(f"[COLOR/BRIGHTNESS] Capabilities retrieved for {entity_id}")
        friendly_name = caps.get("friendly_name", entity_id.split('.')[-1].replace('_', ' ').title())
        
        # Validate color support
        if intent == "set_color":
            if not caps.get("has_color") and not caps.get("has_color_temp"):
                return {
                    "status": "FAILURE", 
                    "message": f"{friendly_name} doesn't support color control. It's a simple on/off or brightness-only light.",
                    "entity_id": entity_id, 
                    "service": "set_color"
                }
            
            # Parse requested color
            color_found = None
            color_name_found = None
            for color_name, rgb in COLOR_MAP.items():
                if color_name in q_low:
                    color_found = rgb
                    color_name_found = color_name
                    break
            
            if not color_found:
                return {"status": "FAILURE", "message": "I couldn't determine which color you want. Try: red, blue, green, warm white, etc.", "entity_id": entity_id, "service": "set_color"}
            
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
                return {
                    "status": "FAILURE",
                    "message": f"{friendly_name} doesn't support full color. Try 'set to warm white' or 'set to cool white' instead.",
                    "entity_id": entity_id,
                    "service": "set_color"
                }
            else:
                # Should not reach here, but safety fallback - try RGB anyway
                service_data = {"rgb_color": color_found}
                log.warning(f"No matching color mode for {entity_id}, trying RGB fallback")
        
        # Validate brightness support
        elif intent in ["set_brightness", "dim", "brighten"]:
            if not caps.get("has_brightness"):
                return {
                    "status": "FAILURE",
                    "message": f"{friendly_name} is an on/off only light and doesn't support brightness control.",
                    "entity_id": entity_id,
                    "service": "set_brightness"
                }
            
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
        
        return await execute_ha_service(domain, service, entity_id, user_creds, service_data, redis_client)

    # -------------------------------------------------
    # POWER, NAVIGATION
    # -------------------------------------------------
    if intent in ["turn_on", "turn_off", "toggle"] or intent.startswith("nav_"):
        if intent.startswith("nav_"):
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
        return await execute_ha_service(domain, service, entity_id, user_creds, service_data, redis_client)

    # -------------------------------------------------
    # MEDIA (PLAY / OPEN APP)
    # -------------------------------------------------
    if intent in ["play_media", "open_app"]:

        # APP LAUNCH
        for app, pkg in APP_PACKAGES.items():
            if app in q_low:
                return await execute_ha_service(
                    "media_player", "play_media", entity_id, user_creds,
                    {"media_content_id": pkg, "media_content_type": "app"},
                    redis_client
                )

        # --- SMART CONTENT TYPE DETECTION ---
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
        
        if is_audiobook_request:
            ctype = "audiobook"
            detected_specific_type = True

        # TV Logic: TVs play video unless music is explicitly requested
        is_tv = any(x in entity_id.lower() for x in ["tv", "chromecast", "shield", "androidtv"])
        if is_tv and not is_music_request:
            ctype = "video"

        # Fallback Logic for Non-MA Devices:
        if detected_specific_type and "music_assistant" not in integration:
            log.info(f"Target {entity_id} is not Music Assistant. Downgrading type '{ctype}' to 'music'.")
            ctype = "music"

        # --- CONTENT CLEANING ---
        original_title = clean_title

        # Only remove control/action words
        clean_title = re.sub(r"\b(play|please|from|on|open|launch|playback|listen to)\b", " ", clean_title)

        # Only remove content TYPE keywords IF the request is for MUSIC
        if is_music_request:
            clean_title = re.sub(r"\b(music|song|album|track|playlist|artist|radio)\b", " ", clean_title)

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
        if "music_assistant" in integration:
            log.info(f"Executing Music Assistant specific Play on {entity_id} Type: {ctype}")
            # MA Service requires: media_id, media_type, enqueue
            ma_service_data = {
                "media_id": clean_title,
                "media_type": ctype,
                "enqueue": "play" 
            }
            # Attempt MA service first
            result = await execute_ha_service("music_assistant", "play_media", entity_id, user_creds, ma_service_data, redis_client)

            # Fallback to 'search' if specific type fails (fuzzy search)
            if result.get("status") == "FAILURE":
                 log.info("MA play_media failed with specific type. Retrying with media_type='search'...")
                 ma_service_data["media_type"] = "search"
                 result = await execute_ha_service("music_assistant", "play_media", entity_id, user_creds, ma_service_data, redis_client)

            return result
        else:
            # Standard Media Player Service
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

async def _execute_transport_command(intent: str, entity_id: str, domain: str, user_creds: dict, integration: str, redis_client):
    """Executes media transport command with self-healing fallback prioritizing remote control. Returns structured dict."""

    if intent == "stop_media":
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

    return {"status": "FAILURE", "message": f"Transport command '{intent}' could not be executed.", "entity_id": entity_id, "service": intent}

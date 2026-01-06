# app/domains/media/devices.py
"""
Media device resolution and management functions.
"""

import json
import logging
import requests
import asyncio
from typing import List, Dict, Optional, Tuple, Any
from app.settings import run_blocking, HA_URL, GlobalResources

log = logging.getLogger(__name__)

def _filter_by_area(candidates: List[dict], query: str) -> List[dict]:
    """
    Filters candidates if the query explicitly mentions an area name found in their metadata.
    Example: "Turn off Living Room" -> Keeps only devices with area_name="Living Room".
    """
    if not candidates:
        return candidates

    query_lower = query.lower()
    
    # Check if any candidate has an area that matches the query
    # We do a 'best effort' match. If multiple areas mentioned? (Simple contains check for now)
    
    # 1. Collect all unique areas present in candidates
    candidate_areas = set(c.get("metadata", {}).get("area_name", "").lower() for c in candidates)
    candidate_areas.discard("") # Remove empty
    
    matched_area = None
    for area in candidate_areas:
        if area in query_lower:
             matched_area = area
             break
    
    if matched_area:
        log.info(f"[AREA MATCH] Query mentions area '{matched_area}'. Filtering candidates.")
        filtered = [
             c for c in candidates 
             if c.get("metadata", {}).get("area_name", "").lower() == matched_area
        ]
        if filtered:
             return filtered
        log.warning(f"[AREA MATCH] Filter returned empty list. Fallback to original.")
        
    return candidates


def safe_similarity_search(collection, query: str, k: int = 5):
    """Safe wrapper for ChromaDB similarity search."""
    docs = collection.similarity_search(query, k=k)
    if not docs:
        log.warning(f"No docs returned from ChromaDB for query '{query}'.")
    return docs


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


def _get_last_media_entity_key(user: str) -> str:
    return f"rag:last_media_entity:{user}"


def _set_last_media_entity(redis_client, user: str, entity_id: str):
    if redis_client and entity_id and entity_id.startswith("media_player."):
        key = _get_last_media_entity_key(user)
        log.info(f"[LAST MEDIA ENTITY] Setting {key} = {entity_id}")
        redis_client.setex(key, 86400, entity_id)


def get_last_media_entity(redis_client, user: str) -> str:
    if redis_client:
        key = _get_last_media_entity_key(user)
        val = redis_client.get(key)
        result = val.decode('utf-8') if isinstance(val, bytes) else val
        log.info(f"[LAST MEDIA ENTITY] Getting {key} = {result}")
        return result
    log.info(f"[LAST MEDIA ENTITY] No redis client")
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


async def find_group_sibling(entity_id: str, match_func, return_meta: bool = False) -> Optional[Any]:
    """
    Generic helper to find a sibling in the same group that matches criteria.
    Uses ChromaDB for group_id lookup.
    
    Args:
        entity_id: The reference entity ID
        match_func: Function taking metadata dict and returning bool
        return_meta: If True, returns (entity_id, metadata)
        
    Returns:
        entity_id (str) or (entity_id, metadata) or None
    """
    try:
        if GlobalResources.ha_collection:
            # 1. Get Group ID for current device
            current_docs = GlobalResources.ha_collection.get(ids=[entity_id], include=["metadatas"])
            if current_docs and current_docs.get("metadatas"):
                current_group_id = current_docs["metadatas"][0].get("group_id")
                
                if current_group_id and current_group_id != "unknown":
                    # 2. Get all members of the group
                    group_docs = GlobalResources.ha_collection._collection.get(
                        where={"group_id": current_group_id},
                        include=["metadatas"]
                    )
                    
                    if group_docs and group_docs.get("metadatas"):
                        for metadata in group_docs["metadatas"]:
                            candidate_id = metadata.get("entity_id")
                            if candidate_id == entity_id: continue
                            
                            if match_func(metadata):
                                log.info(f"[Group Lookup] Found sibling for {entity_id}: {candidate_id}")
                                return (candidate_id, metadata) if return_meta else candidate_id
    except Exception as e:
        log.warning(f"[Group Lookup] Error resolving group for {entity_id}: {e}")
        
    return None


async def smart_power_sync(entity_id: str, user_creds: Dict[str, Any]):
    """
    Ensures the physical TV sibling of a device is powered on.
    Specifically targets hardware siblings in the same group.
    """
    try:
        from app.domains.shared import execute_ha_service
        
        # 1. Define robust TV matcher
        def is_physical_tv(metadata: Dict[str, Any]) -> bool:
            integ = metadata.get("integration", "").lower()
            fname = metadata.get("friendly_name", "").lower()
            caps = metadata.get("capabilities", "").lower()
            attrs_str = metadata.get("attributes", "{}").lower()
            
            # Skip known pure-software wrappers / Queues
            # Music Assistant often mirrors the TV name but is NOT the power control.
            if integ in ["music_assistant", "spotify"] or "mass_player_type" in attrs_str or "music assistant" in fname:
                return False
                
            # Signal 1: Native TV integrations (Strongest Signal)
            if integ in ["androidtv", "webostv", "samsungtv", "braviatv", "roku", "esphome"]:
                return True
                
            # Signal 2: Hardware Traits (Explicit Volume Steps or Remote Control)
            features = int(metadata.get("supported_features", 0))
            has_step = bool(features & 1024)
            has_set = bool(features & 4)
            # Classical Android TV/Hardware hardware pattern or explicit remote
            if (has_step and not has_set) or "remote_control" in caps: 
                return True
            
            # Signal 3: device_class metadata
            if '"device_class": "tv"' in attrs_str:
                return True
                
            # Signal 4: Name Fallback (Only if it looks like a controller)
            if "tv" in fname and ("remote" in fname or "turn_on" in caps):
                return True
                
            return False

        # 2. Find Sibling
        res = await find_group_sibling(entity_id, is_physical_tv, return_meta=True)
        if not res:
            return
            
        tv_id, tv_meta = res
        tv_state = await get_entity_state(tv_id, user_creds)
        
        # [Decision] Power on if completely OFF
        should_turn_on = tv_state in ["off", "standby", "unavailable", "unknown"]
        
        if should_turn_on:
            log.info(f"[SmartPowerSync] Powering ON physical TV sibling: {tv_id} (State: {tv_state})")
            await execute_ha_service("media_player", "turn_on", tv_id, user_creds, {}, None)
            await asyncio.sleep(2) # Wait for command acceptance
            
        # [Wake Pulse] Only pulse if we just turned it ON or if it's an Android TV in a deep sleep state (idle but unresponsive).
        # CRITICAL: Do NOT pulse for video/app intents if the TV is already ON, as it interrupts the session start.
        is_atv = tv_meta.get("integration") == "androidtv" or "remote" in tv_id or "remote" in tv_meta.get("friendly_name", "").lower()
        
        # Skip pulse if already ON/IDLE for video/app requests to prevent navigation interruptions
        skip_pulse = False
        if not should_turn_on and any(x in (str(tv_meta.get("intent", "")) + str(tv_meta.get("query", ""))).lower() for x in ["watch", "video", "youtube", "netflix", "app"]):
             skip_pulse = True

        if is_atv and tv_state != "playing" and not skip_pulse:
            log.info(f"[SmartPowerSync] Waking up hardware {tv_id} via 'Home' pulse.")
            try:
                # Home pulse is the most reliable wake-up trigger for Android TV hardware.
                await execute_ha_service("media_player", "play_media", tv_id, user_creds, {"media_content_id": "home", "media_content_type": "nav"}, None)
            except: pass
            
        if should_turn_on:
            await asyncio.sleep(2) # Additional boot time if we just turned it on
        else:
            log.info(f"[SmartPowerSync] Physical TV sibling {tv_id} is already {tv_state} (Pulse skipped: {skip_pulse})")

    except Exception as e:
        log.warning(f"[SmartPowerSync] Failed for {entity_id}: {e}")

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
                            log.debug(f"[CAPABILITY] ChromaDB color_modes for {entity_id}: {color_modes}")
                            # Override has_color based on color_modes (more authoritative)
                            capabilities["has_color"] = any(m in color_modes for m in ["rgb", "hs", "xy", "rgbw", "rgbww"])
                            capabilities["has_color_temp"] = "color_temp" in color_modes
                            # If light has color modes, it almost certainly supports brightness
                            if color_modes:
                                capabilities["has_brightness"] = True
                                log.debug(f"[CAPABILITY] ChromaDB set has_brightness=True for {entity_id}")

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
            raw_color_modes = attrs.get("supported_color_modes", [])
            log.debug(f"[CAPABILITY] Raw supported_color_modes for {entity_id}: {raw_color_modes} (type: {type(raw_color_modes)})")
            capabilities["color_modes"] = raw_color_modes

            # Additional brightness detection: if light has brightness attribute, it supports brightness
            if "brightness" in attrs:
                capabilities["has_brightness"] = True
                log.debug(f"[CAPABILITY] Set has_brightness=True for {entity_id} due to brightness attribute: {attrs['brightness']}")

            # If color_modes is present, it's more authoritative
            log.debug(f"[CAPABILITY] Checking color_modes for {entity_id}: {capabilities.get('color_modes')} (truthy: {bool(capabilities.get('color_modes'))})")
            if capabilities["color_modes"]:
                log.debug(f"[CAPABILITY] Applying color_modes logic for {entity_id}")
                capabilities["has_color"] = any(m in capabilities["color_modes"] for m in ["rgb", "hs", "xy", "rgbw", "rgbww"])
                capabilities["has_color_temp"] = "color_temp" in capabilities["color_modes"]
                # If light has color modes, it almost certainly supports brightness
                capabilities["has_brightness"] = True
                log.debug(f"[CAPABILITY] Set has_brightness=True for {entity_id} due to color_modes: {capabilities['color_modes']}")

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
    from app.logic.pattern_matching import detect_number_pattern, filter_entities_by_pattern
    detected_patterns = detect_number_pattern(query)

    if not detected_patterns:
        # No pattern - use single entity resolution
        result = await smart_resolve_entity(query, intent, ha_collection)

        if isinstance(result, list):
             return result

        if result and len(result) == 3:
            entity_id, integration, metadata = result
            if entity_id:
                return [(entity_id, integration, metadata)]
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


def _score_candidate_for_intent_and_media_type(candidate, intent: str, is_music: bool, is_video: bool) -> int:
    """
    Scores a candidate entity based on the intent and media type.
    Higher score = better match.
    
    Args:
        candidate: Tuple of (entity_id, integration, metadata) or dict/object with these attributes.
        intent: The intent string (e.g., "turn_off", "play_media").
        is_music: Boolean, true if intent is music-related.
        is_video: Boolean, true if intent is video-related.
        
    Returns:
        Integer score.
    """
    # Normalize candidate input
    metadata = {}
    if isinstance(candidate, (tuple, list)):
        eid = candidate[0]
        integ = candidate[1]
        if len(candidate) > 2: metadata = candidate[2]
    elif isinstance(candidate, dict):
        eid = candidate.get("eid") or candidate.get("entity_id")
        integ = candidate.get("integration", "unknown")
        # Fallback to using candidate itself as metadata if unstructured
        metadata = candidate.get("metadata", candidate) 
    else:
        # Fallback for unexpected types
        return 0

    domain = eid.split('.')[0]
    integ = str(integ).lower()
    friendly_name = str(metadata.get("friendly_name", "")).lower()
    attrs = str(metadata.get("attributes", ""))
    
    # Handle capabilities (could be list or string depending on source)
    caps = metadata.get("capabilities", [])
    if isinstance(caps, str): caps = caps.split(",")
    # If supported_features dict is available, check keys
    supp_feats = metadata.get("supported_features", {})
    if isinstance(supp_feats, dict):
        caps.extend(supp_feats.keys())
    
    caps_set = set(str(c).lower().strip() for c in caps)

    # --- Scoring Logic (Consolidated from _route_by_intent) ---
    score = 0
    
    # 0. BASELINE PREFERENCE (Hardware vs Software)
    # Prefer Hardware TVs (Roku, Android TV, Apple TV, WebOS, Samsung) over Cast/DLNA for most things
    HW_TV_INTEGRATIONS = ["roku", "androidtv", "webostv", "samsungtv", "apple_tv", "braviatv", "firetv", "tv"]
    is_hardware_tv = any(x in integ for x in HW_TV_INTEGRATIONS)
    is_cast = any(x in integ for x in ["cast", "google_cast", "chromecast"])
    is_ma = "music_assistant" in integ or "mass" in integ

    # 1. POWER COMMANDS
    if intent in ["turn_off", "turn_on", "toggle"]:
        if domain == "remote": 
            return 100
        if "remote" in friendly_name: 
            return 95 # High priority for "Office TV Remote"
        
        # High priority for Android TV integrations (Control visible as media_player but acts like remote)
        if integ == "androidtv_remote":
             return 90
        
        # Hardware devices (TVs with remotes) are preferred for power control
        if is_hardware_tv: 
            return 80
        
        if domain == "switch": 
            return 15 # Smart plug?
            
        # Deprioritize cast/chrome for power - User rarely wants to "Turn off Chromecast" (background service)
        # They usually mean the TV itself.
        if is_cast or "_chrome" in eid: 
            return -50
            
        # Music Assistant players are software entities, rarely power controlled
        if is_ma or "dlna" in integ: 
            return -10
        
        if "turn_off" in caps_set: return 10
        return 5

    # 2. MEDIA PLAY / WATCH
    elif intent in ["play_media", "watch_media", "view_content", "media_play"]:
        if is_music:
            # Checking for MA explicit attributes
            has_ma_attr = "mass_player_type" in attrs or "music_assistant" in attrs
            is_speaker = "speaker" in integ or "dlna" in integ or "sonos" in integ
            
            if is_ma: return 200 # Native MA Provider (Best)
            elif has_ma_attr: return 150 # Wrapper with MA capability
            elif is_speaker: return 50
            elif "play_media" in caps_set: return 10
            
            # Penalize Cast/Chrome for music if we have other options (prefer MA)
            # But Cast is still better than a TV for audio sometimes? 
            # Actually user wants to prioritize MA heavily.
            if is_cast: return 5 # Acceptable but low
            
            return 0

        elif is_video:
            # STRICT prohibition on Audio-only devices
            if any(x in integ for x in ["sonos", "music_assistant", "audio", "spotify", "squeeze", "mass"]):
                return -100
            
            if is_hardware_tv:
                return 100
            elif is_cast:
                return 90  # Cast is good for video but prefer native TV
            
            # Generic
            return 5

        else:
            # Ambiguous Intent
            if is_ma: return 50
            if is_hardware_tv: return 20 
            if "play_media" in caps_set: return 10
            return 0

    # 3. REMOTE CONTROL (Navigation)
    elif intent.startswith("nav_"):
        if domain == "remote": return 100
        if "androidtv_remote" in integ: return 95
        # Media players can sometimes handle nav via services, but remotes are best
        if is_hardware_tv: return 50
        return 0

    # 4. APP LAUNCH
    elif intent == "open_app":
        # Prioritize Smart Players that run apps
        if is_hardware_tv or is_cast:
            if domain == "media_player": return 100
        if domain == "remote": return 50
        return 0

    # 5. PLAYBACK CONTROLS (Pause, Resume, Stop)
    elif intent in ["pause", "resume", "media_pause", "media_play", "media_stop", "stop_media", "media_next_track", "media_previous_track", "media_next", "media_previous"]:
        if domain == "media_player":
            # Prioritize Hardware TVs over Cast (so "Stop" stops the TV app, not just the cast background)
            if is_hardware_tv:
                 return 150 # Boost above standard 100
            elif is_cast: 
                 return 110
            return 100
            
        elif domain == "remote":
             return -50 # Remotes often don't support direct service calls like 'media_pause'
        return 0

    # 6. VOLUME CONTROLS
    elif intent in ["volume_up", "volume_down", "volume_mute", "volume_set"]:
         if domain == "media_player": return 100
         if domain == "remote": return 50
         return 0

    # Default fallback
    if is_hardware_tv: 
        return 10
    
    return 5


async def _refine_target_for_volume(entity_id: str, intent: str, redis_client=None) -> str:
    """
    Bidirectional volume targeting:
    - For volume_up/down/mute: Prefer Android TV/Roku (hardware controls)
    - For volume_set: Prefer Cast (percentage support)
    
    IMPORTANT: This function is integration-safe and will NOT incorrectly swap
    Roku devices. Roku is treated as a valid hardware target for step commands.
    """
    log.info(f"[Volume Optimization] ENTRY: entity={entity_id}, intent={intent}")
    try:
        if not intent.startswith("volume_"):
            log.info(f"[Volume Optimization] Not a volume intent, returning {entity_id}")
            return entity_id

        # Check current entity capabilities
        from app.domains.media.devices import get_device_capabilities
        from app.settings import GlobalResources
        import json
        
        # Get current entity metadata
        current_caps = None
        current_integration = None
        try:
            if GlobalResources.ha_collection:
                docs = GlobalResources.ha_collection.get(ids=[entity_id], include=["metadatas"])
                if docs and docs.get("metadatas"):
                    meta = docs["metadatas"][0]
                    current_integration = meta.get("integration", "").lower()
                    
                    # Try to get supported_features from attributes JSON
                    attrs_json = meta.get("attributes", "{}")
                    try:
                        if isinstance(attrs_json, str):
                            attrs = json.loads(attrs_json)
                            current_caps = int(attrs.get("supported_features", 0))
                        else:
                            current_caps = 0
                    except:
                        current_caps = 0
                    
                    log.info(f"[Volume Optimization] Current entity: integration={current_integration}, caps={current_caps}")
        except Exception as e:
            log.warning(f"[Volume Optimization] Could not get current entity caps: {e}")

        # [Sibling Collection & Scoring]
        candidates = []
        try:
            if GlobalResources.ha_collection:
                # 1. Get Group ID
                current_docs = GlobalResources.ha_collection.get(ids=[entity_id], include=["metadatas"])
                if current_docs and current_docs.get("metadatas"):
                    meta = current_docs["metadatas"][0]
                    group_id = meta.get("group_id")
                    current_state = meta.get("state", "off")
                    
                    if group_id and group_id != "unknown":
                        # 2. Get all members
                        group_docs = GlobalResources.ha_collection._collection.get(
                            where={"group_id": group_id},
                            include=["metadatas"]
                        )
                        if group_docs and group_docs.get("metadatas"):
                            for m in group_docs["metadatas"]:
                                if m.get("entity_id") == entity_id: continue
                                
                                # Check capabilities of this sibling
                                integ = m.get("integration", "").lower()
                                eid = m.get("entity_id")
                                state = m.get("state", "off")
                                
                                # Use promoted metadata
                                feats = int(m.get("supported_features", 0))
                                
                                is_cast = any(x in integ for x in ["cast", "google_cast", "chromecast", "music_assistant"])
                                is_hardware = any(x in integ for x in ["androidtv", "roku", "webostv", "braviatv", "samsungtv", "tv"])
                                
                                # Compatibility Check
                                compatible = False
                                if intent == "volume_set":
                                    # volume_set REQUIRES bits (usually bit 4)
                                    compatible = bool(feats & 4)
                                else:
                                    # volume_up/down/mute
                                    if intent in ["volume_mute", "volume_unmute"]:
                                        compatible = bool(feats & 8)
                                    elif intent in ["volume_up", "volume_down"]:
                                        compatible = bool(feats & 1024)
                                
                                if compatible:
                                    # Score based on state (Playing > Paused > On > Off)
                                    state_score = 0
                                    if state == "playing": state_score = 100
                                    elif state == "paused": state_score = 80
                                    elif state == "on" or state == "buffering": state_score = 50
                                    elif state in ["idle", "standby"]: state_score = 10
                                    
                                    # Preference Bonus
                                    pref_bonus = 0
                                    if intent == "volume_set":
                                        if is_cast: pref_bonus = 20 # Prefer Cast for Set
                                        if "music_assistant" in integ: pref_bonus += 10 # Extra pref for MA
                                    else:
                                        if is_hardware: pref_bonus = 20 # Prefer Hardware for Steps
                                    
                                    total_score = state_score + pref_bonus
                                    candidates.append((eid, total_score, state))
                                    log.info(f"[Volume Optimization] Found candidate {eid}: score={total_score} ({state}), integ={integ}")

        except Exception as e:
            log.warning(f"[Volume Optimization] Error collecting siblings: {e}")

        # [Final Decision]
        if candidates:
            # Add self to candidates to evaluate against siblings
            # We need to get current entity's integration info again
            self_score = 0
            # Rough estimate of self score
            if current_state == "playing": self_score = 100
            elif current_state == "paused": self_score = 80
            elif current_state == "on": self_score = 50
            
            candidates.append((entity_id, self_score, current_state))
            
            # Sort by score descending
            candidates.sort(key=lambda x: x[1], reverse=True)
            best_eid, best_score, best_state = candidates[0]
            
            log.info(f"[Volume Optimization] BEST candidate for {intent}: {best_eid} (Score: {best_score}, State: {best_state})")
            return best_eid
        
        log.info(f"[Volume Optimization] No appropriate siblings available, sticking to {entity_id}")
        return entity_id
    except Exception as e:
        log.error(f"[Volume Optimization] Error: {e}", exc_info=True)
        return entity_id
    

async def smart_resolve_entity(query_name: str, intent: str, ha_collection, is_music: bool = False, is_video: bool = False, allow_multiple: bool = False) -> list:
    """
    Resolves the best entity (or entities) based on query and intent.
    When is_music=True, it prioritizes Music Assistant devices.
    
    Returns:
       - If allow_multiple=False: (entity_id, integration, metadata) tuple
       - If allow_multiple=True: List of (entity_id, integration, metadata) tuples
    """
    log.info(f"DEBUG: Entering smart_resolve_entity. Q='{query_name}' Intent='{intent}' Multiple={allow_multiple}")

    # 0. Setup & lazy imports
    try:
        from app.settings import GlobalResources, run_blocking
        from app.logic.pattern_matching import detect_number_pattern, filter_entities_by_pattern
    except ImportError:
        log.error("Could not import dependencies for resolution.")
        return [] if allow_multiple else (None, None, {})

    # 1. Detect Entity Grouping/Pattern (Numbers, Locations, Directions, Plurals)
    patterns = detect_number_pattern(query_name) # Aliased to detect_entity_pattern
    if patterns:
        allow_multiple = True
        log.info(f"Detected grouping pattern: {patterns}")

    # 1.5. EXACT NAME MATCHING (Priority Override)
    # Before doing expensive ChromaDB search, check for exact/prefix matches
    # This prevents "Office TV" from matching to "Living Room TV"
    query_lower = query_name.lower().strip()

    try:
        # Get ALL entities from ChromaDB for exact matching
        # Use passed collection if available, otherwise GlobalResources
        collection_source = ha_collection if ha_collection else GlobalResources.ha_collection
        
        all_results = await run_blocking(
            lambda: collection_source.get()
        )

        if all_results and all_results.get("metadatas"):
            exact_matches = []
            prefix_matches = []

            for meta in all_results["metadatas"]:
                friendly_name = meta.get("friendly_name", "").lower().strip()
                entity_id = meta.get("entity_id", "")
                integration = meta.get("integration", "unknown")
                domain = entity_id.split('.')[0] if entity_id else ""

                # CRITICAL FIX: Check for exact entity ID match FIRST (before friendly name)
                # This handles explicit entity IDs like "media_player.office_tv_chrome_2"
                if entity_id and entity_id.lower() == query_lower:
                    log.info(f"[ENTITY ID EXACT MATCH] '{query_name}' → {entity_id}")
                    exact_matches.append((entity_id, integration, meta))
                    continue  # Skip further checks for this entity

                # Skip non-actionable domains for media intents
                media_intents = ["play_media", "stop_media", "media_next", "media_previous", "pause", "resume", "open_app", "volume_up", "volume_down", "volume_set", "volume_mute", "media_pause", "media_play"]
                if intent in media_intents and domain not in ["media_player", "remote"]:
                    continue
                
                # Skip Music Assistant devices for video requests (go straight to Cast/TV devices)
                if is_video and integration == "music_assistant":
                    log.info(f"[Video Filter] Skipping Music Assistant device: {entity_id}")
                    continue

                # Exact match (highest priority)
                if friendly_name == query_lower or friendly_name.replace(".", "").replace(",", "") == query_lower.replace(".", "").replace(",", ""):
                    exact_matches.append((entity_id, integration, meta))
                    log.info(f"[EXACT MATCH] '{query_name}' → {entity_id}")
                
                # Prefix matches
                elif len(query_lower) > 3:
                    # Normalize both for check
                    fn_norm = friendly_name.replace(".", "").replace(",", "")
                    q_norm = query_lower.replace(".", "").replace(",", "")
                    
                    if friendly_name.startswith(query_lower) or query_lower in friendly_name:
                        prefix_matches.append((entity_id, integration, friendly_name, meta))
                    elif fn_norm.startswith(q_norm) or q_norm in fn_norm:
                         prefix_matches.append((entity_id, integration, friendly_name, meta))

            # Return exact match immediately, but prioritize native integrations if multiple
            if exact_matches:
                # Sort using the shared helper
                def _helper_score(item):
                    # item is (eid, integ, meta)
                    # We pass the Tuple which is handled by _score_candidate_for_intent_and_media_type normalization
                    return _score_candidate_for_intent_and_media_type(item, intent, is_music, is_video)
                
                exact_matches.sort(key=_helper_score, reverse=True)
                
                # Filter out negative scores (strictly filtered out)
                exact_matches = [m for m in exact_matches if _helper_score(m) > -50]

                if not exact_matches:
                    log.info(f"All exact matches filtered out by strict intent rules.")
                    return [] if allow_multiple else (None, None, {})

                best = exact_matches[0]
                log.info(f"Using prioritized exact name match for '{query_name}': {best} (Entity: {best[0]}, Score: {_helper_score(best)})")
                
                # [Volume Optimization] Check for better hardware sibling
                if intent.startswith("volume_"):
                    refined_id = await _refine_target_for_volume(best[0], intent)
                    if refined_id != best[0]:
                         # Look for the refined entity in docs to get full metadata tuple
                         # Or just return simple tuple if strictly internal usage
                         # Better to search docs for metadata continuity
                         return await smart_resolve_entity(refined_id, intent, ha_collection, is_music, is_video, allow_multiple)

                return [best] if allow_multiple else best

            # Return best prefix match
            if prefix_matches and len(query_lower) >= 6:
                prefix_matches.sort(key=lambda x: len(x[2]))
                # [Fix] Return 3-item tuple for prefix match as well
                match_data = prefix_matches[0]
                # match_data for prefix is (eid, integ, friendly, meta)
                if len(match_data) == 4:
                    eid, integ, friendly, meta = match_data
                else: 
                     # Should not happen based on append above but safety first
                    eid, integ, friendly, meta = match_data[0], match_data[1], "Unknown", match_data[2]
                
                best_match = (eid, integ, meta)
                log.info(f"Using prefix match for '{query_name}': {best_match[0]} ({friendly})")
                return [best_match] if allow_multiple else best_match

    except Exception as e:
        log.warning(f"Exact match check failed: {e}, continuing with similarity search")

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

            # Basic Domain Safety - Filter out sensors for actionable intents
            domain = eid.split('.')[0]

            # Exclude sensors/binary_sensors for color/brightness commands
            if intent in ["set_color", "set_brightness", "dim", "brighten"] and domain != "light":
                 continue

            # Exclude read-only domains for power commands
            if intent in ["turn_on", "turn_off", "toggle"] and domain in ["sensor", "binary_sensor", "sun", "weather"]:
                 continue
            # NEW: Strict Domain Filtering for Media Intents (User Request)
            # "Lights do not have media play features" - Strictly enforce appropriate domains.
            media_intents = ["play_media", "watch_media", "view_content", "stop_media", "media_next", "media_previous", "pause", "resume", "open_app", "volume_up", "volume_down", "volume_set", "volume_mute"]
            if intent in media_intents and domain not in ["media_player", "remote"]:
                 log.info(f"Resolution: Skipping {eid} for intent '{intent}' because domain '{domain}' is not a media player or remote.")
                 continue

            # NEW: If query explicitly mentions "TV" or "Television", penalize/exclude Lights/Switches to prevent "Office TV" -> "Office Light"
            query_lower = query_name.lower()
            if ("tv" in query_lower or "television" in query_lower) and domain in ["light", "switch", "fan", "cover", "lock"]:
                 log.info(f"Resolution: Skipping/Penalizing {eid} because query '{query_name}' targets a TV, but this is a {domain}.")
                 # We simply exclude it to be safe, as "Turn on TV" should never mean "Turn on Light" unless it's a specific bias light
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

    # 3.5 AREA FILTERING (New Modular Step)
    raw_candidates = _filter_by_area(raw_candidates, query_name)
    
    if not raw_candidates:
         log.info("Area filtering removed all candidates. Returning None.")
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

    # 5. Standard Priority Logic using Helper
    scored_candidates = []
    for c in raw_candidates:
         # Enrich for helper
         score = _score_candidate_for_intent_and_media_type(c, intent, is_music, is_video)
         scored_candidates.append((score, c))
    
    scored_candidates.sort(key=lambda x: x[0], reverse=True)
    
    if not scored_candidates:
         return [] if allow_multiple else (None, None)
         
    best_score, best_dict = scored_candidates[0]
    log.info(f"Smart Resolution Selected: {best_dict['eid']} (Score: {best_score})")
    
    # Check for hard disqualification
    if best_score <= -100:
         log.warning("Best candidate disqualified by strict intent rules.")
         return [] if allow_multiple else (None, None)

    # ---------------------------------------------------------
    # CAPABILITY / GROUP ROUTING
    # ---------------------------------------------------------
    # Check if the BEST candidate belongs to a group and if we should route to a better member
    # (e.g. "Living Room" -> Group -> Music Assistant Speaker)
    
    top_meta = best_dict["metadata"]
    group_id = top_meta.get("group_id")
    
    if group_id and group_id != "unknown":
        log.info(f"Match found in Group: {group_id} (Entity: {best_dict['eid']})")
        try:
             # Access underlying chromadb collection if available
             if hasattr(ha_collection, "_collection"):
                  group_res = ha_collection._collection.get(where={"group_id": group_id})
                  
                  if group_res and group_res.get("metadatas"):
                       members = group_res["metadatas"]
                       log.info(f"Group {group_id} has {len(members)} members.")

                       # ROUTING LOGIC - Use existing helper
                       selected = _route_by_intent(intent, members, is_music, is_video)
                       if selected:
                            eid = selected.get("entity_id")
                            integ = selected.get("integration", "unknown")
                            log.info(f"Capability Routing diverted {best_dict['eid']} -> {eid} ({integ}) for intent {intent}")
                            
                            qt = (eid, integ, selected)
                            
                            # If allowing multiple, we might want to return the whole group?
                            # For now, if routed, strict return single best.
                            if allow_multiple:
                                return [qt] 
                            return qt
        except Exception as e:
            log.error(f"Group Routing Failed: {e}")

    # Return best matches
    if allow_multiple:
         # Return top 5 valid candidates
         return [(x[1]["eid"], x[1]["integration"], x[1]["metadata"]) for x in scored_candidates if x[0] > -50][:5]

    return (best_dict["eid"], best_dict["integration"], best_dict["metadata"])


def _route_by_intent(intent: str, members: list, is_music: bool, is_video: bool) -> dict:
    """Selects best entity from a group based on intent and capabilities."""
    
    # Score candidates using the shared helper
    scored = []

    for m in members:
        # m is a metadata dict (eid, integration, etc)
        # Helper expects tuple or dict. passing dict m should work.
        score = _score_candidate_for_intent_and_media_type(m, intent, is_music, is_video)
        scored.append((score, m))

    scored.sort(key=lambda x: x[0], reverse=True)
    
    if scored:
        best_score, best_member = scored[0]
        # Only return if it's a positive/neutral match (filtered out negatives)
        if best_score > -50:
            return best_member
            
    return None

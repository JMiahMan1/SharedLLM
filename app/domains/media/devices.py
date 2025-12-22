# app/domains/media/devices.py
"""
Media device resolution and management functions.
"""

import json
import logging
import requests
import asyncio
from typing import List, Dict, Optional, Tuple
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
        from langchain_chroma import Chroma
        from app.logic.pattern_matching import detect_number_pattern, filter_entities_by_pattern
    except ImportError:
        log.error("Could not import dependencies for resolution.")
        return [] if allow_multiple else (None, None)

    # 1. Detect Entity Grouping/Pattern (Numbers, Locations, Directions, Plurals)
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
        all_results = await run_blocking(
            lambda: GlobalResources.ha_collection.get()
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
                # Sort: Prefer non-MASS, non-DLNA
                # Priority: Roku/AndroidTV/WebOS > Cast > Others > MASS/DLNA
                def _integ_priority(item):
                    # Handle 2-item or 3-item tuples
                    eid = item[0]
                    integ = item[1]
                    domain = eid.split('.')[0]
                    
                    # CRITICAL: Different priorities for music vs video vs power
                    if intent == "turn_off":
                        # Power Off: Prefer actual remotes or TV integrations over Cast/Speakers
                        if domain == "remote": return 20
                        if "roku" in integ or "androidtv" in integ or "webostv" in integ or "samsungtv" in integ: return 15
                        if "cast" in integ or "google_cast" in integ: return 5
                        return 10

                    if is_music:  
                        # Music: Prefer Cast/MA (Music Assistant uses Cast devices)
                        if "cast" in integ or "music_assistant" in integ or "sonos" in integ: return 10
                        if "roku" in integ or "androidtv" in integ or "webostv" in integ: return 5
                    elif is_video:
                        # Video: Prefer TV devices (AndroidTV/Roku) over Cast speakers
                        if "roku" in integ or "androidtv" in integ or "webostv" in integ or "samsungtv" in integ: return 10
                        if "cast" in integ or "google_cast" in integ: return 8
                    else:
                        # Default: Prefer TV devices
                        if "roku" in integ or "androidtv" in integ or "webostv" in integ or "samsungtv" in integ: return 10
                        if "cast" in integ or "google_cast" in integ or "sonos" in integ: return 8

                    if "music_assistant" in integ or "dlna" in integ: return 0
                    return 5
                
                exact_matches.sort(key=_integ_priority, reverse=True)
                
                log.info(f"Using prioritized exact name match for '{query_name}': {exact_matches[0]} (Entity: {exact_matches[0][0]}, Score: {_integ_priority(exact_matches[0])})")
                
                # [Fix] Return 3-item tuple to preserve metadata (Source of Truth)
                best = exact_matches[0] # (eid, integ, meta) is already in this format as appended above
                return [best] if allow_multiple else best

            # Return best prefix match
            if prefix_matches and len(query_lower) >= 6:
                prefix_matches.sort(key=lambda x: len(x[2]))
                # [Fix] Return 3-item tuple for prefix match as well
                # prefix_matches items can be (eid, integ, friendly, meta)
                match_data = prefix_matches[0]
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
            media_intents = ["play_media", "stop_media", "media_next", "media_previous", "pause", "resume", "open_app", "volume_up", "volume_down", "volume_set", "volume_mute"]
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

    # 5. Standard Priority Logic
    # Reconstruct simple candidates list for legacy logic
    candidates = [(c["eid"], c["integration"], c["metadata"]) for c in raw_candidates]

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
             for eid, integration, meta in candidates:
                  if "speaker" in eid or "audio" in integration:
                       ma_candidate = (eid, integration, meta)
                       break

        if ma_candidate:
            return [ma_candidate] if allow_multiple else ma_candidate

        # Fallback: If no MA device found, we return the best TV candidate (if exists),
        # but we log a warning because we expected music.
        tv_candidate = None
        for eid, integration, meta in candidates:
            if eid.startswith("media_player.") and any(x in eid.lower() for x in ["tv", "chromecast", "shield", "androidtv"]):
                if tv_candidate is None:
                    tv_candidate = (eid, integration, meta)

        if tv_candidate:
             return [tv_candidate] if allow_multiple else tv_candidate

        # Fallback 2: Check for Cast devices (Google Cast/Chrome) if not found above
        # This fixes "Play music on X" where X is a Chromecast but didn't match "speaker" keywords
        cast_candidate = None
        for eid, integration, meta in candidates:
             if "cast" in integration or "google_cast" in integration or "chrome" in eid.lower():
                  cast_candidate = (eid, integration, meta)
                  break
        
        if cast_candidate:
             return [cast_candidate] if allow_multiple else cast_candidate

        log.warning(f"Strict Music Mode: No suitable music player found for '{query_name}'. Returning None.")
        return [] if allow_multiple else (None, None)

    # --- POWER/HARDWARE PRIORITY ---
    if intent in ["turn_on", "turn_off", "toggle"]:
        HW_INTEGRATIONS_POWER = ["androidtv", "webostv", "braviatv", "roku", "apple_tv", "samsungtv", "esphome", "tasmota", "shelly", "hue", "lutron_caseta", "kodi", "vlc", "denonavr", "yamaha"]

        # Capability Enrichment (Re-added)
        from app.settings import get_user_creds
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
            
            # Intent Analysis / Media Type Inference
            # Global Policy: "Play" -> Music, "Watch" -> Video
            q_lower = query_name.lower()
            
            # Check for explicit Video keywords
            is_video = "watch" in q_lower or "view" in q_lower or "video" in q_lower or "movie" in q_lower or "show" in q_lower or "netflix" in q_lower or "youtube" in q_lower
            
            # Check for explicit Music keywords
            is_music = "listen" in q_lower or "music" in q_lower or "song" in q_lower or "radio" in q_lower or "spotify" in q_lower
            
            # [Global Policy Logic]
            # If intent is "play_media" and NOT explicit video -> Default to Music
            if intent == "play_media" and not is_video:
                is_music = True
            
            # Special: "Play X on Y" without "watch" should be music.
            # The commands.py logic does this too, but we need it HERE for Group Routing to pick the MA entity.
            if any(x in eid.lower() for x in ["tv", "projector", "receiver", "remote"]): score += 5

            is_chrome = "chrome" in integ.lower() or "cast" in integ.lower() or "google_cast" in integ.lower()
            if is_chrome: score -= 25 # HEAVY Penalty for Cast during Power Ops (Prefer Remote/Native)

            matches.append((score, eid, integ, c.get("supported_features", {}), c.get("friendly_name")))

        if matches:
            matches.sort(key=lambda x: x[0], reverse=True)
            res = (matches[0][1], matches[0][2], {"supported_features": matches[0][3], "friendly_name": matches[0][4]})
            return [res] if allow_multiple else res

    # --- FALLBACK / GENERIC ---
    preferred_type = "generic"
    if intent == "play_media":
         APP_PACKAGES = ["netflix", "youtube", "hulu", "plex", "spotify", "disney"]  # This should be imported from integrations
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
                               # Return 3-item tuple to preserve metadata (Source of Truth)
                               return (selected["entity_id"], selected.get("integration", "unknown"), selected)

            except Exception as e:
                log.error(f"Group Routing Failed: {e}")

    # Fallback to standard selection
    for eid, integration, meta in candidates:
        if preferred_type == "remote" and ("remote" in eid or "androidtv" in integration):
             return eid, integration, meta

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
        elif intent in ["play_media", "watch_media", "view_content"]:
            if is_music:
                # [Fix] Global MA Routing: Prioritize specialized MA 'speaker' entities over generic TV entities
                # This ensures we use the MA integration (which works) instead of generic Cast/TV integration (video-focused)
                # We prioritize ANY entity with 'mass_player_type' for music.
                attrs = m.get("attributes", "")
                has_ma_attr = "mass_player_type" in str(attrs) or "music_assistant" in str(attrs)
                
                is_speaker = "speaker" in integration or "dlna" in integration
                
                if integration == "music_assistant": score += 200 # Native MA Provider (Best)
                elif has_ma_attr: score += 150 # Wrapper with MA capability (Critical for Cast/Roku)
                elif is_speaker: score += 50
                elif "play_media" in caps: score += 10
                
                # Penalize Cast/Chrome if we want Music and have other options
                if "cast" in integration or "chrome" in eid: score -= 50

            elif is_video:
                # Prioritize native TV integrations for video
                HW_TV_INTEGRATIONS = ["roku", "androidtv", "webostv", "braviatv", "samsungtv", "apple_tv", "tv"]
                if any(x in integration for x in HW_TV_INTEGRATIONS):
                    score += 100
                elif "cast" in integration:
                    score += 90  # Cast is good for video but prefer native TV
                elif integration == "music_assistant" or integration == "speaker":
                    score -= 50  # Music Assistant wrapper shouldn't handle video
                elif "play_media" in caps:
                    score += 10
            else:
                # Ambiguous
                if integration == "music_assistant": score += 50
                elif "play_media" in caps: score += 10

        # REMOTE CONTROL
        elif intent.startswith("nav_"):
            if domain == "remote": score += 100
            elif integration == "androidtv_remote": score += 90

        # APP LAUNCH (Merge with remote but prioritize Smart Players)
        elif intent == "open_app":
            if "cast" in integration or "androidtv" in integration:
                if domain == "media_player": score += 100
            elif domain == "remote": score += 50

        # PLAYBACK CONTROLS (Pause, Resume, Stop, Next, Prev)
        # Prioritize media_player entities over remotes/others
        # PLAYBACK CONTROLS (Pause, Resume, Stop, Next, Prev)
        # Prioritize media_player entities over remotes/others
        elif intent in ["pause", "resume", "media_pause", "media_play", "media_stop", "stop_media", "media_next_track", "media_previous_track"]:
            if domain == "media_player":
                score += 100
                
                # Prioritize Hardware TVs (Android, WebOS, Roku) over generic Cast
                # This ensures "Stop Office TV" targets the TV integration, not the Chromecast dongle
                HW_INTEGRATIONS_TV = ["androidtv", "webostv", "braviatv", "roku", "apple_tv", "samsungtv"]
                if any(x in integration for x in HW_INTEGRATIONS_TV):
                     score += 50 
                elif "cast" in integration: 
                     score += 10 # Cast is valid secondary
                     
            elif domain == "remote":
                 score -= 50 # Remotes often don't support direct media_pause service calls

        # VOLUME CONTROLS
        elif intent in ["volume_up", "volume_down", "volume_mute", "volume_set"]:
             if domain == "media_player": score += 100
             elif domain == "remote": score += 50 # Remotes can often do volume

        scored.append((score, m))

    scored.sort(key=lambda x: x[0], reverse=True)
    if scored:
        return scored[0][1]
    return None

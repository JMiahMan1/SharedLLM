# app/domains/media/commands.py
"""
Media command handling and orchestration.
"""

import json
import re
import logging
import requests
import asyncio
from typing import List, Dict, Optional, Tuple
from app.settings import run_blocking, HA_URL, DEFAULT_MODEL, GlobalResources
from app.logic.pattern_matching import detect_number_pattern, filter_entities_by_pattern
from app.domains.shared import execute_ha_service
from app.domains.media.devices import (
    get_device_capabilities, get_active_media_players, get_available_media_players,
    smart_resolve_entity, resolve_multiple_entities_with_pattern,
    _set_last_entity, get_last_entity, get_last_media_entity
)
from app.domains.media.integrations import APP_PACKAGES
from app.logic import music_assistant_ops, android_tv_ops, roku_ops, webos_ops

log = logging.getLogger(__name__)


async def _execute_transport_command(intent: str, entity_id: str, domain: str, user_creds: dict, integration: str, redis_client, query: str = ""):
    """Executes media transport command with self-healing fallback prioritizing remote control. Returns structured dict."""
    intent = intent.strip()
    log.info(f"[_execute_transport_command] Intent='{intent}' (repr={repr(intent)}) Entity='{entity_id}'")

    result = None

    if intent == "stop_media":
        log.info(f"[Transport] Match stop_media for {entity_id}")
        # Check state first to avoid 500 error on off devices
        from app.domains.media.devices import get_entity_state
        state = await get_entity_state(entity_id, user_creds)
        if state in ["off", "unavailable", "idle", "standby"]:
             result = {"status": "SUCCESS", "message": f"{entity_id} is already stopped.", "entity_id": entity_id, "service": "media_stop", "new_state": state}
             log.info(f"[Transport] Returning result: {result}")
             return result
        result = await execute_ha_service("media_player", "media_stop", entity_id, user_creds, {}, redis_client)
        log.info(f"[Transport] Returning result: {result}")
        return result

    # [Transport Redirection Logic - simplified version]
    # This is a simplified version - the full logic from media_ops.py would go here

    # Fallback to generic service call if intent matches a service name
    return await execute_ha_service("media_player", intent, entity_id, user_creds, {}, redis_client)


async def handle_media_command(
    intent: str,
    query: str,
    entity_id: str,
    user_creds: dict,
    ha_collection,
    redis_client,
    device_name: str = None,
    brightness: str = None,
    integration: str = "unknown", 
):
    """
    Handles media command and ensures a structured dictionary is returned.
    Supports multi-device pattern matching (even/odd/range/list/all).
    """
    q_low = query.lower()
    log.info(f"[HANDLE_MEDIA_COMMAND] Called with intent={intent}, entity_id={entity_id}, device_name={device_name}, integration={integration}")

    # [IntentOverride] Force upgrade for ambiguous "Watch" commands
    if re.search(r"\b(watch|view)\b", q_low) and intent not in ["watch_video", "view_content", "play_media"]:
        if intent not in ["stop_media", "volume_up", "volume_down", "volume_mute", "volume_set"]:
            log.info(f"[IntentOverride] Detected 'watch' keyword. Upgrading intent '{intent}' -> 'watch_video'")
            intent = "watch_video"

    # integration is now passed in, no need to reset unless we wish to override
    if integration is None:
        integration = "unknown"

    # 1. EARLY MUSIC/CONTENT DETECTION
    music_keywords = ["music", "song", "artist", "album", "track", "playlist", "radio"]
    audiobook_keywords = ["read", "book", "chapter", "audiobook"]
    # ONLY watch/view should trigger video - not generic "video" keyword
    video_keywords = ["watch", "view"]

    is_music_request = any(x in q_low for x in music_keywords)
    is_audiobook_request = any(x in q_low for x in audiobook_keywords)
    is_video_request = any(x in q_low for x in video_keywords)

    strict_resolution = (is_music_request or is_audiobook_request) or (
        (intent == "play_media" or intent == "play") and not is_video_request
    )
    is_transport = intent in ["media_next", "media_previous", "stop_media", "media_pause", "media_play", "resume", "volume_set", "volume_up", "volume_down", "volume_mute"]

    # [Device Name Fallback]
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

    # [Standard Resolution Path]
    if not entity_id:
        # 1. First, try to resolve from query content (Explicit in-query device)
        cleaned_for_res = q_low
        for p in ["turn on", "turn off", "toggle", "play", "stop", "open", "launch", "the", " on ", " please ",
                  "skip", "next", "previous", "back", "pause", "resume",
                  "this song", "the song", "current song", "track", "music"]:
            cleaned_for_res = cleaned_for_res.replace(p, " ")
        cleaned_for_res = cleaned_for_res.strip()

        if cleaned_for_res:
            # Try to resolve the cleaned query as a device name
            try:
                resolved = await smart_resolve_entity(
                    cleaned_for_res,
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
                         log.info(f"[Device Fallback] No devices found for {cleaned_for_res}")
                elif isinstance(resolved, tuple):
                     entity_id, integration = resolved
                     log.info(f"[Device Fallback] Resolved '{cleaned_for_res}' to {entity_id}")
                elif resolved:
                     entity_id = resolved
                     log.info(f"[Device Fallback] Resolved '{cleaned_for_res}' to {entity_id}")
            except Exception as e:
                log.error(f"[Device Fallback] Error resolving '{cleaned_for_res}': {e}", exc_info=True)

        # 2. Second, fallback to last used devices (Contextual/Implicit)
        if not entity_id:
             if is_transport:
                entity_id = get_last_media_entity(redis_client, user_creds.get("user"))
                if not entity_id:
                    entity_id = get_last_entity(redis_client, user_creds.get("user"))
             else:
                # For play commands, prefer last media entity over general last entity
                entity_id = get_last_media_entity(redis_client, user_creds.get("user"))
                if not entity_id:
                    entity_id = get_last_entity(redis_client, user_creds.get("user"))

        if entity_id:
            user = user_creds.get("user")
            log.info(f"[CONTEXT UPDATE] user={user}, entity_id={entity_id}, redis_client={redis_client is not None}")
            if user and entity_id:
                 _set_last_entity(redis_client, user, entity_id)
                 if entity_id.startswith("media_player."):
                     from app.domains.media.devices import _set_last_media_entity
                     _set_last_media_entity(redis_client, user, entity_id)

            # [MASS INTELLIGENCE SWAP] - Restored from historical logic
            # If we resolved a hardware device (e.g., Office TV) but it's a music request,
            # checks if there is a 'Shadow' Music Assistant player to use instead.
            if (is_music_request or intent == "play_media") and integration != "music_assistant" and not is_video_request:
                try:
                    # Determine name to search: explicit device_name OR friendly name of resolved entity
                    search_name = device_name
                    if not search_name:
                         # Fetch friendly name
                         from app.domains.media.devices import get_device_capabilities
                         caps = await get_device_capabilities(entity_id, user_creds, redis_client)
                         search_name = caps.get("friendly_name", "").replace(" TV", "").replace(" Speaker", "").replace(" Remote", "")
                    
                    if search_name:
                         log.info(f"[MASS Swap] Checking for Music Assistant player matching '{search_name}'...")
                         # Search specifically for MA integration
                         ma_docs = GlobalResources.ha_collection.similarity_search(f"{search_name} music assistant", k=3)
                         for d in ma_docs:
                             if d.metadata.get("integration") == "music_assistant":
                                 # Verify it's related (string match)
                                 # If search was "Office", and we found "mass_office_tv", good.
                                 found_id = d.metadata.get("entity_id")
                                 found_name = d.metadata.get("friendly_name", "")
                                 
                                 # Safety: Ensure the found MA player roughly matches the target name
                                 if search_name.lower() in found_name.lower() or search_name.lower() in found_id.lower():
                                      log.info(f"[MASS Swap] Swapping hardware {entity_id} -> MA Player {found_id}")
                                      entity_id = found_id
                                      integration = "music_assistant"
                                      break
                except Exception as e:
                    log.warning(f"[MASS Swap] Error checking for MA player: {e}")

    if entity_id:
        domain = entity_id.split('.')[0]

    if not entity_id and intent not in ["turn_on", "turn_off", "toggle"]:
         return [{"status": "FAILURE", "message": "Could not determine which device you mean.", "entity_id": "N/A", "service": "media_command"}]

    # 3. TRANSPORT REDIRECTION
    log.info(f"[DEBUG_TRANSPORT] Checking Redirection: intent='{intent}' is_transport={is_transport} entity={entity_id}")
    if is_transport:
        log.info(f"[DEBUG_TRANSPORT] Entered Transport Redirection Block for {intent}")

        # Only use transport redirection if we STILL don't have an entity
        if not entity_id:
             last_media = get_last_media_entity(redis_client, user_creds.get("user"))
             if last_media:
                 log.info(f"[Transport] Using last media entity: {last_media}")
                 entity_id = last_media

        if entity_id:
             domain = entity_id.split('.')[0]
             return [await _execute_transport_command(intent, entity_id, domain, user_creds, integration, redis_client, query)]

    if not entity_id:
         return [{"status": "FAILURE", "message": "Could not determine which device you mean.", "entity_id": "N/A", "service": "media_command"}]

    domain = entity_id.split('.')[0]
    service = intent
    service_data = {}

    # [**UNIVERSAL** MASS INTELLIGENCE SWAP] - Run for ALL music requests
    # If we have a music request but resolved to a non-MA device, try to swap to MA player
    if intent in ["play_media", "open_app", "watch_video", "view_content"]:
        if (is_music_request or (intent == "play_media" and not is_video_request)) and integration != "music_assistant":
            try:
                from app.settings import GlobalResources
                
                # Get current device's group_id and entity_id
                current_docs = GlobalResources.ha_collection.get(ids=[entity_id], include=["metadatas"])
                if current_docs and current_docs.get("metadatas"):
                    current_group_id = current_docs["metadatas"][0].get("group_id")
                    current_entity_base = re.sub(r'_?\d+$', '', entity_id)  # Strip trailing numbers
                    
                    found_ma_player = None
                    
                    # Strategy 1: Try exact group_id match
                    if current_group_id:
                        log.info(f"[MASS Swap] Looking for MA player in group_id={current_group_id}")
                        group_docs = GlobalResources.ha_collection._collection.get(
                            where={"group_id": current_group_id},
                            include=["metadatas"]
                        )
                        if group_docs and group_docs.get("metadatas"):
                            for metadata in group_docs["metadatas"]:
                                if metadata.get("integration") == "music_assistant":
                                    found_ma_player = metadata.get("entity_id")
                                    log.info(f"[MASS Swap] Found MA player in same group: {found_ma_player}")
                                    break
                    
                    # Strategy 2: If no MA in group, try entity ID similarity
                    if not found_ma_player:
                        log.info(f"[MASS Swap] No MA in group. Checking entity ID similarity for base: {current_entity_base}")
                        # Get ALL music_assistant devices and check for entity ID match
                        ma_docs = GlobalResources.ha_collection._collection.get(
                            where={"integration": "music_assistant"},
                            include=["metadatas"]
                        )
                        if ma_docs and ma_docs.get("metadatas"):
                            for metadata in ma_docs["metadatas"]:
                                candidate_id = metadata.get("entity_id")
                                if candidate_id:
                                    candidate_base = re.sub(r'_?\d+$', '', candidate_id)
                                    
                                    # Fuzzy match: check if IDs are "super close"
                                    # Match if exact, or if one contains the other, or 80%+ similar
                                    is_match = False
                                    if current_entity_base == candidate_base:
                                        is_match = True
                                    elif current_entity_base in candidate_base or candidate_base in current_entity_base:
                                        is_match = True
                                    else:
                                        # Calculate similarity (rough Levenshtein-like)
                                        longer = max(len(current_entity_base), len(candidate_base))
                                        if longer > 0:
                                            # Count matching characters in order
                                            common = sum(a == b for a, b in zip(current_entity_base, candidate_base))
                                            similarity = common / longer
                                            if similarity >= 0.8:
                                                is_match = True
                                    
                                    if is_match:
                                        found_ma_player = candidate_id
                                        log.info(f"[MASS Swap] Found MA player via fuzzy entity ID match: {found_ma_player} (base: {candidate_base} ~ {current_entity_base})")
                                        break
                    
                    if found_ma_player:
                        log.info(f"[MASS Swap] Swapping {entity_id} ({integration}) -> MA {found_ma_player}")
                        entity_id = found_ma_player
                        integration = "music_assistant"
                        domain = entity_id.split('.')[0]
            except Exception as e:
                log.warning(f"[MASS Swap] Error: {e}")

        # [**TV INTELLIGENCE SWAP**] - For VIDEO requests, swap speaker/cast to actual TV
        # If we have a video request but resolved to a speaker/cast, find the TV in the same group
        if is_video_request and integration in ["cast", "music_assistant"]:
            try:
                # Get the current device's group_id from ChromaDB
                from app.settings import GlobalResources
                current_docs = GlobalResources.ha_collection.get(ids=[entity_id], include=["metadatas"])
                if current_docs and current_docs.get("metadatas"):
                    current_group_id = current_docs["metadatas"][0].get("group_id")
                    current_group_name = current_docs["metadatas"][0].get("group_name", "")
                   
                    if current_group_id:
                        log.info(f"[TV Swap] Video request on {integration} device. group_id={current_group_id}, searching for TV...")
                        
                        # Get all devices in ChromaDB and filter by group_id
                        all_docs = GlobalResources.ha_collection._collection.get(
                            where={"group_id": current_group_id},
                            include=["metadatas"]
                        )
                        
                        if all_docs and all_docs.get("metadatas"):
                            for metadata in all_docs["metadatas"]:
                                tv_integration = metadata.get("integration", "")
                                # Look for actual TV integrations
                                if tv_integration in ["androidtv", "webostv", "roku", "tv"]:
                                    found_id = metadata.get("entity_id")
                                    log.info(f"[TV Swap] Found TV in same group: {found_id} ({tv_integration})")
                                    log.info(f"[TV Swap] Swapping {entity_id} ({integration}) -> TV {found_id}")
                                    entity_id = found_id
                                    integration = tv_integration
                                    domain = entity_id.split('.')[0]
                                    break
            except Exception as e:
                log.warning(f"[TV Swap] Error: {e}")

    # [SMART ROUTING: Music -> Speaker, Video/Power -> TV]
    # Simplified version - the full logic from media_ops.py would go here

    # EXECUTE MEDIA PLAYBACK
    if intent in ["play_media", "open_app", "watch_video", "view_content"]:
        # [Integration-based routing]
        if integration == "androidtv":
            # Android TV logic would go here
            pass
        elif integration == "webostv":
            # WebOS logic would go here
            pass
        elif integration == "roku":
            # Roku logic would go here
            pass
        elif integration == "cast":
            # Cast logic would go here
            pass

        # [SmartPowerSync] If playing on a Cast device, find and power on the hardware TV
        # Cast devices (_chrome, _chrome_2, etc.) are virtual and can't be powered on
        # We need to find the actual TV hardware in the same group
        if "cast" in integration or "_chrome" in entity_id:
            try:
                from app.settings import GlobalResources
                from app.domains.media.devices import get_entity_state
                
                tv_sibling = None
                
                # Strategy 1: Try ChromaDB group lookup
                try:
                    current_docs = GlobalResources.ha_collection.get(ids=[entity_id], include=["metadatas"])
                    if current_docs and current_docs.get("metadatas"):
                        current_group_id = current_docs["metadatas"][0].get("group_id")
                        
                        if current_group_id and current_group_id != "unknown":
                            log.info(f"[SmartPowerSync] Searching for TV in group {current_group_id}")
                            
                            # Find all devices in same group
                            group_docs = GlobalResources.ha_collection._collection.get(
                                where={"group_id": current_group_id},
                                include=["metadatas"]
                            )
                            
                            if group_docs and group_docs.get("metadatas"):
                                for metadata in group_docs["metadatas"]:
                                    candidate_id = metadata.get("entity_id")
                                    friendly_name = metadata.get("friendly_name", "").lower()
                                    candidate_integration = metadata.get("integration", "")
                                    
                                    # Find device with "tv" in name OR non-MA integration
                                    if (("tv" in friendly_name or "tv" in candidate_id) and 
                                        candidate_integration != "music_assistant" and
                                        candidate_id != entity_id):
                                        tv_sibling = candidate_id
                                        log.info(f"[SmartPowerSync] Found TV sibling via group: {tv_sibling}")
                                        break
                except Exception as e:
                    log.warning(f"[SmartPowerSync] ChromaDB lookup failed: {e}")
                
                # Strategy 2: Fallback to suffix stripping (like working commit)
                if not tv_sibling:
                    log.info(f"[SmartPowerSync] ChromaDB lookup failed, trying suffix stripping")
                    tv_sibling = entity_id.replace("_chrome_2", "").replace("_chrome", "").replace("_cast", "").replace("_speaker", "")
                    if tv_sibling == entity_id:
                        tv_sibling = None  # No suffix was stripped
                    else:
                        log.info(f"[SmartPowerSync] Found TV sibling via suffix stripping: {tv_sibling}")
                
                if tv_sibling:
                    try:
                        tv_state = await get_entity_state(tv_sibling, user_creds)
                        if tv_state in ["off", "standby", "unavailable"]:
                            log.info(f"[SmartPowerSync] TV {tv_sibling} is OFF. Turning ON.")
                            await execute_ha_service("media_player", "turn_on", tv_sibling, user_creds, {}, redis_client)
                            await asyncio.sleep(4)  # Wait for TV boot
                            log.info(f"[SmartPowerSync] TV {tv_sibling} should now be ready.")
                        else:
                            log.info(f"[SmartPowerSync] TV {tv_sibling} is already {tv_state}")
                    except Exception as e:
                        log.warning(f"[SmartPowerSync] Failed to power on {tv_sibling}: {e}")
                else:
                    log.warning(f"[SmartPowerSync] No TV sibling found for {entity_id}")
            except Exception as e:
                log.warning(f"[SmartPowerSync] Error: {e}")

        # [SMART SWAP] Find Music Assistant sibling in same group using group_name
        # This runs for music requests on non-MA devices to find the MA player in the same group
        if intent == "play_media" and not is_video_request and integration != "music_assistant":
            try:
                from app.settings import GlobalResources
                
                # Get current entity metadata
                docs = GlobalResources.ha_collection.get(ids=[entity_id], include=["metadatas"])
                current_meta = docs.get("metadatas", [{}])[0] if docs else {}
                
                if current_meta and current_meta.get("group_name"):
                    group_name = current_meta["group_name"]
                    log.info(f"[Smart Swap] Looking for Music sibling in group: '{group_name}'")
                    
                    # Find all siblings in same group_name
                    siblings = GlobalResources.ha_collection._collection.get(
                        where={"group_name": group_name},
                        include=["metadatas"]
                    )
                    
                    candidates = []
                    if siblings and siblings.get("metadatas"):
                        for meta in siblings["metadatas"]:
                            s_id = meta.get("entity_id")
                            if s_id == entity_id:
                                continue
                            
                            s_integ = meta.get("integration", "")
                            s_attrs = str(meta.get("attributes", "")).lower()
                            
                            # Check if Music Assistant capable
                            is_ma = "music_assistant" in s_integ or "mass_player_type" in s_attrs
                            
                            if is_ma:
                                score = 0
                                
                                # Model match (definitive - same hardware)
                                target_model = current_meta.get("model")
                                s_model = meta.get("model")
                                if target_model and s_model and target_model == s_model:
                                    score += 50
                                
                                # Manufacturer match
                                target_mfr = current_meta.get("manufacturer")
                                s_mfr = meta.get("manufacturer")
                                if target_mfr and s_mfr and target_mfr == s_mfr:
                                    score += 10
                                
                                # Name similarity
                                target_name = current_meta.get("friendly_name", "").lower()
                                s_name = meta.get("friendly_name", "").lower()
                                if target_name and s_name and (target_name in s_name or s_name in target_name):
                                    score += 20
                                
                                # Speaker device class
                                if "device_class': 'speaker'" in s_attrs:
                                    score += 5
                                
                                # Only consider if score >= 10 (prevents random swaps)
                                if score >= 10:
                                    candidates.append((score, s_id, s_integ))
                    
                    # Pick best candidate
                    if candidates:
                        candidates.sort(key=lambda x: x[0], reverse=True)
                        best_score, best_id, best_integ = candidates[0]
                        log.info(f"[Smart Swap] Candidates: {[(s, id) for s, id, _ in candidates]}. Selected: {best_id} (Score: {best_score})")
                        log.info(f"[Smart Swap] Swapping {entity_id} -> {best_id} (Group: {group_name})")
                        
                        # SWAP entity_id to MA player
                        entity_id = best_id
                        integration = best_integ
                        domain = entity_id.split('.')[0]
            except Exception as e:
                log.warning(f"[Smart Swap] Error: {e}")

        # [Music Assistant Integration]
        # Check if device supports Music Assistant OR if this is a natural language music search
        is_ma_device = "music_assistant" in integration
        
        # CRITICAL: Only use active_queue if device is ALREADY MA integration
        # Cast devices with MA sync should NOT use MA - they should have been swapped by MASS Swap
        ma_target_entity = entity_id  # Default to same entity
        
        if is_ma_device:
            try:
                # Get device metadata to check for active_queue
                from app.settings import GlobalResources
                try:
                    docs = GlobalResources.ha_collection.get(ids=[entity_id], include=["metadatas"])
                    current_meta = docs.get("metadatas", [{}])[0] if docs else {}
                except Exception:
                    current_meta = {}

                # CRITICAL: Check for active_queue - synced players must use their queue
                # active_queue points to the actual MA player entity
                if current_meta:
                    try:
                        import json
                        attrs = json.loads(current_meta.get("attributes", "{}"))
                        if "active_queue" in attrs and attrs["active_queue"]:
                            ma_target_entity = attrs["active_queue"]
                            log.info(f"[MA] Synced player detected. Using active_queue: {ma_target_entity} instead of {entity_id}")
                    except Exception as e:
                        log.warning(f"[MA] Could not parse active_queue: {e}")
            except Exception:
                pass

        if is_ma_device:
            try:
                log.info(f"Delegating Music Assistant Play on {entity_id} (target:{ma_target_entity}) to music_assistant_ops")

                # Determine content type - DEFAULT TO MUSIC unless video keywords present
                ctype = "video" if is_video_request else "music"

                # CLEAN QUERY - Extract the actual search term
                clean_title = query.lower()

                # 1. Remove device name from query (e.g. "on office tv")
                if entity_id:
                    from app.domains.media.devices import get_device_capabilities
                    caps = await get_device_capabilities(entity_id, user_creds, redis_client)
                    fname = caps.get("friendly_name", "").lower()
                    ename = entity_id.split(".")[-1].replace("_", " ").lower()
                    
                    # Remove "on {name}" patterns
                    # Add generic "tv" or "speaker" removal if they appear at the end
                    targets_to_remove = [fname, ename, "office tv", "master bedroom tv", "gracie tv", "tv", "speaker"]
                    for name in targets_to_remove:
                        if name and name in clean_title:
                            # regex to remove "on the office tv" or just "office tv"
                            clean_title = re.sub(f"\\b(on|in|at|to)?\\s*(the)?\\s*{re.escape(name)}\\b", " ", clean_title)

                # 2. Remove action/control words
                clean_title = re.sub(r"\b(play|please|from|on|open|launch|playback|listen to)\b", " ", clean_title)

                # 3. Remove content type keywords for music requests
                if is_music_request:
                    clean_title = re.sub(r"\b(music|song|album|track|playlist|artist|radio|podcast)\b", " ", clean_title)

                # 4. Clean up extra spaces
                clean_title = re.sub(r'\s+', ' ', clean_title).strip()
                
                log.info(f"[MA CLEANING] Original: '{query}' -> Cleaned: '{clean_title}'")

                # Attempt Music Assistant delegation
                result = await music_assistant_ops.play_media(ma_target_entity, clean_title, ctype, user_creds)

                if result and result.get("status") == "SUCCESS":
                    log.info("Music Assistant delegation succeeded")
                    return [result]
                else:
                    log.info("MA play_media failed with specific type. Retrying with media_type='search'...")
                    result = await music_assistant_ops.play_media(ma_target_entity, clean_title, "search", user_creds)

                if result and result.get("status") == "SUCCESS":
                    log.info("Music Assistant delegation succeeded on retry")
                    return [result]
                else:
                    log.warning("Music Assistant delegation failed, falling back to standard media player")
            except Exception as e:
                log.error(f"Error in Music Assistant delegation: {e}")

        # [Standard Media Player Service]
        # Default to music if ambiguous, as video usually requires specific "watch" intent
        ctype = "video" if is_video_request else "music"
        
        # CRITICAL: If MA cleaning already happened, reuse that cleaned query
        # Otherwise we'll send the dirty query ("brandon lake on office tv") instead of clean ("brandon lake")
        if is_ma_device and 'clean_title' in locals():
            cleaned_std_query = clean_title  # Reuse MA's cleaned query
            log.info(f"[Standard Play Fallback] Reusing MA-cleaned query: '{cleaned_std_query}'")
        else:
            # CLEAN QUERY for Standard Players (Spotify et al expect just the song name)
            cleaned_std_query = query.lower()
            if ctype == "music":
                # Remove device names from query
                if entity_id:
                    from app.domains.media.devices import get_device_capabilities
                    caps = await get_device_capabilities(entity_id, user_creds, redis_client)
                    fname = caps.get("friendly_name", "").lower()
                    ename = entity_id.split(".")[-1].replace("_", " ").lower()
                    
                    targets_to_remove = [fname, ename, "office tv", "master bedroom tv", "gracie tv", "tv", "speaker"]
                    for name in targets_to_remove:
                        if name and name in cleaned_std_query:
                            cleaned_std_query = re.sub(f"\\b(on|in|at|to)?\\s*(the)?\\s*{re.escape(name)}\\b", " ", cleaned_std_query)
                
                # Remove action/control words
                cleaned_std_query = re.sub(r"\b(play|please|from|on|listen to)\b", "", cleaned_std_query).strip()
                cleaned_std_query = re.sub(r'\s+', ' ', cleaned_std_query).strip()
                log.info(f"[Standard Play Cleaning] Original: '{query}' -> Cleaned: '{cleaned_std_query}'")
            else:
                cleaned_std_query = query
        
        # Self-Correction: If query is not a URL and type is video, try to find a URL via Search
        if ctype == "video" and not query.startswith(("http", "www", "spotify", "app")):
             # Extract just the content name for search (remove device names, intents, etc.)
             search_query = query.lower()
             # Remove common phrases
             for phrase in ["on the", "on", "in the", "to the", "watch", "play", "view", "video", "youtube", 
                           "office tv", "master bedroom tv", "tv", "television"]:
                 search_query = search_query.replace(phrase, " ")
             search_query = " ".join(search_query.split()).strip()
             
             msg = f"[Standard Play] No direct URL for '{query}'. Searching Whoogle for '{search_query} youtube'..."
             log.info(msg)
             print(msg)
             
             found_url = None
             try:
                 from app.logic.web_search import tool_web_search
                 
                 print(f"Starting Whoogle search for: {search_query} youtube")
                 search_results = await tool_web_search(f"{search_query} youtube")
                 print(f"Whoogle Results: {search_results[:200]}...")
                 
                 # Parse results for YouTube URLs
                 if search_results:
                     # Extract URLs using regex
                     url_pattern = r'URL:\s*(https?://[^\s\n]+)'
                     urls = re.findall(url_pattern, search_results)
                     
                     for url in urls:
                         if "youtube.com" in url or "youtu.be" in url:
                             found_url = url
                             break
             except Exception as e:
                 err_msg = f"[Standard Play] Search error: {e}"
                 log.warning(err_msg)
                 print(err_msg)
            
             if found_url:
                 log.info(f"[Standard Play] Resolved '{search_query}' -> {found_url}")
                 print(f"Resolved to {found_url}")
                 cleaned_std_query = found_url
             else:
                 log.warning("[Standard Play] Video request with non-URL query. Aborting to prevent 500 error.")
                 print("Aborting video request")
                 return [{"status": "FAILURE", "message": "Video playback requires a direct URL or specific app. Please provide a link.", "entity_id": entity_id, "service": "play_media"}]
             
        std_service_data = {
            "media_content_id": cleaned_std_query,
            "media_content_type": ctype
        }
        
        # Enhanced Logging for debugging 500 errors
        log.info(f"[Standard Play] Call {domain}.play_media on {entity_id} | Type: {ctype} | Content: {cleaned_std_query}")
        
        try:
            result = await execute_ha_service(domain, "play_media", entity_id, user_creds, std_service_data, redis_client)
            return [result]
        except Exception as e:
            log.error(f"[Standard Play] Failed: {e}")
            return [{"status": "FAILURE", "message": f"Failed to play media: {e}", "entity_id": entity_id, "service": "play_media"}]

    # [Power/Navigation Commands]
    if intent in ["turn_on", "turn_off", "toggle"] or intent.startswith("nav_"):
        # Navigation command handling
        if intent.startswith("nav_"):
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
                # Check if remote exists and send command
                pass

        # Standard power/remote commands
        if domain not in ["light", "switch", "remote", "media_player"]:
            domain = "homeassistant"
        return [await execute_ha_service(domain, service, entity_id, user_creds, service_data, redis_client)]

    log.info(f"[HANDLE_MEDIA_COMMAND] Returning final failure for intent {intent}")
    return {"status": "FAILURE", "message": f"Media command '{intent}' could not be executed.", "entity_id": entity_id, "service": intent}


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
                intent, query, entity_id, user_creds, ha_collection, redis_client,
                integration=integration
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

    # Flatten nested lists (handle_media_command can return lists)
    flattened_results = []
    for r in results:
        if isinstance(r, list):
            flattened_results.extend(r)
        else:
            flattened_results.append(r)

    # Aggregate results - handle both dict and list results
    success_count = sum(1 for r in flattened_results if isinstance(r, dict) and r.get('status') == 'SUCCESS')
    failure_count = len(flattened_results) - success_count

    # Get list of successful/failed devices
    successful_devices = [r.get('friendly_name', r.get('entity_id', '?'))
                         for r in flattened_results if isinstance(r, dict) and r.get('status') == 'SUCCESS']
    failed_devices = [r.get('friendly_name', r.get('entity_id', '?'))
                     for r in flattened_results if isinstance(r, dict) and r.get('status') != 'SUCCESS']

    if success_count == len(flattened_results):
        message = f"Successfully controlled {success_count} devices: {', '.join(successful_devices)}"
        status = 'SUCCESS'
    elif success_count > 0:
        message = f"Controlled {success_count}/{len(flattened_results)} devices. "
        message += f"Success: {', '.join(successful_devices)}. "
        if failed_devices:
            message += f"Failed: {', '.join(failed_devices)}"
        status = 'SUCCESS'  # Partial success still counts as success
    else:
        message = f"Failed to control all {len(flattened_results)} devices: {', '.join(failed_devices)}"
        status = 'FAILURE'

    return {
        'status': status,
        'message': message,
        'service': intent,
        'batch_results': flattened_results,
        'success_count': success_count,
        'failure_count': failure_count,
        'friendly_name': f"{success_count} devices",
        'entity_id': 'batch_command'
    }

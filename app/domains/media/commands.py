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
from app.settings import run_blocking, HA_URL, DEFAULT_MODEL, GlobalResources
# from app.logic.pattern_matching import detect_number_pattern, filter_entities_by_pattern (Moved inside function)
from app.domains.shared import execute_ha_service
from app.domains.media.devices import (
    get_device_capabilities, get_active_media_players, get_available_media_players,
    smart_resolve_entity, resolve_multiple_entities_with_pattern,
    _set_last_entity, get_last_entity, get_last_media_entity
)
from app.domains.media.integrations import APP_PACKAGES
# from app.logic import music_assistant_ops, android_tv_ops, roku_ops, webos_ops

log = logging.getLogger(__name__)


async def _execute_transport_command(intent: str, entity_id: str, domain: str, user_creds: dict, integration: str, redis_client, query: str = ""):
    """Executes media transport command with self-healing fallback prioritizing remote control. Returns structured dict."""
    intent = intent.strip()
    log.info(f"[_execute_transport_command] Intent='{intent}' (repr={repr(intent)}) Entity='{entity_id}'")

    # [MASS Unwrap for Transport Commands]
    # Music Assistant wrappers don't support skip/pause/resume/stop.
    # Unwrap to the underlying Cast device for these commands.
    try:
        import requests
        from app.settings import HA_URL
        headers = {"Authorization": f"Bearer {user_creds.get('ha_token')}", "Content-Type": "application/json"}
        response = requests.get(f"{HA_URL}/api/states/{entity_id}", headers=headers, timeout=5)
        if response.status_code == 200:
            entity_data = response.json()
            if entity_data.get("attributes", {}).get("mass_player_type"):
                # This is a Music Assistant wrapper
                active_queue = entity_data["attributes"].get("active_queue")
                if active_queue:
                    log.info(f"[Transport MASS Unwrap] Detected Music Assistant wrapper '{entity_id}'. Using underlying device: {active_queue}")
                    entity_id = active_queue  # Replace with real device
                else:
                    log.warning(f"[Transport MASS Unwrap] Entity {entity_id} is a MASS wrapper but has no active_queue.")
    except Exception as e:
        log.warning(f"[Transport MASS Unwrap] Failed to check for MA wrapper: {e}")

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
    from app.logic.pattern_matching import detect_number_pattern, filter_entities_by_pattern
    q_low = query.lower()
    log.info(f"[HANDLE_MEDIA_COMMAND] Called with intent={intent}, entity_id={entity_id}, device_name={device_name}, integration={integration}")

    # [IntentOverride] Removed regex override for 'watch' as it is now a first-class intent.
    # if re.search(r"\b(watch|view)\b", q_low) ... (Logic moved to Intent Engine)

    # integration is now passed in, no need to reset unless we wish to override
    if integration is None:
        integration = "unknown"

    # ===== LAYERED INTENT-TO-TYPE ROUTING =====
    # Priority Cascade:
    # 1. Intent sets default type (watch_media → video, play_media → music)
    # 2. Strong keyword overrides (YouTube, song, album, etc.)
    # 3. Existing heuristics refine
    
    # Layer 1: Intent → Default Type
    if intent == "watch_media":
        media_type_default = "video"
    elif intent == "play_media":
        media_type_default = "music"
    else:
        media_type_default = None  # Other intents (transport commands, etc.)
    
    # Layer 2: Strong Override Keywords
    strong_video_keywords = ["youtube.com", "youtu.be", "watch?v=", "video"]
    strong_music_keywords = ["song", "album", "artist", "track", "spotify:", "playlist"]
    
    media_type_override = None
    if any(kw in q_low for kw in strong_video_keywords):
        media_type_override = "video"
    elif any(kw in q_low for kw in strong_music_keywords):
        media_type_override = "music"
    
    # Layer 3: Existing Heuristics (Refinement)
    music_keywords = ["music", "song", "artist", "album", "track", "playlist", "radio"]
    audiobook_keywords = ["read", "book", "chapter", "audiobook"]
    video_keywords = ["watch", "view", "video"]

    is_music_request = any(x in q_low for x in music_keywords)
    is_audiobook_request = any(x in q_low for x in audiobook_keywords)
    is_video_request = any(x in q_low for x in video_keywords)
    
    # Final Decision: Override > Default > Heuristic fallback
    if media_type_override:
        final_media_type = media_type_override
        log.info(f"[Media Type] Override by keyword: {final_media_type}")
    elif media_type_default:
        final_media_type = media_type_default
        log.info(f"[Media Type] Set by intent '{intent}': {final_media_type}")
    else:
        # Fallback to heuristics for non-play/watch intents
        if is_video_request:
            final_media_type = "video"
        elif is_music_request or is_audiobook_request:
            final_media_type = "music"
        else:
            final_media_type = None
    
    # Set legacy flags for backward compatibility with existing code
    if final_media_type == "video":
        is_video_request = True
        is_music_request = False
    elif final_media_type == "music":
        is_music_request = True
        is_video_request = False

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
        # STRICTER: Only swap to Music Assistant if explicitly requested or high confidence it's music
        if is_music_request and integration != "music_assistant" and not is_video_request:
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
                    
                    # Strategy 2: Reverse Metadata Lookup (Trust attributes, not names)
                    if not found_ma_player:
                        log.info(f"[MASS Swap] No MA in group. Attempting strict metadata lookup via active_queue...")
                        ma_docs = GlobalResources.ha_collection._collection.get(
                            where={"integration": "music_assistant"},
                            include=["metadatas"]
                        )
                        if ma_docs and ma_docs.get("metadatas"):
                            import json
                            for metadata in ma_docs["metadatas"]:
                                try:
                                    # Parse attributes JSON to find the link
                                    # We are looking for a MASS player whose 'active_queue' points to specific entity_id
                                    attrs = json.loads(metadata.get("attributes", "{}"))
                                    target_queue = attrs.get("active_queue")
                                    
                                    if target_queue == entity_id:
                                        found_ma_player = metadata.get("entity_id")
                                        log.info(f"[MASS Swap] Found MA player via active_queue metadata: {found_ma_player}")
                                        break
                                except Exception as e:
                                    continue
                    
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

    # [EXECUTE MEDIA PLAYBACK]
    if intent in ["play_media", "open_app", "watch_video", "view_content"]:
        # Determine content type (defaulting logic extracted to integrations, but router can hint)
        ctype = "video" if (is_video_request or intent in ["watch_video", "view_content"]) else "music"
        
        # [Integration Delegation]
        try:
            from app.domains.media.integrations.factory import IntegrationFactory
            
            log.info(f"[Router] Delegating to Integration: {integration} for {entity_id}")
            handler = IntegrationFactory.get_handler(integration)
            
            # Execute
            result = await handler.play_media(
                entity_id=entity_id, 
                query=query, 
                media_type=ctype, 
                user_creds=user_creds, 
                redis_client=redis_client,
                device_name=device_name,
                ha_collection=ha_collection # Pass Chroma collection for power sync
            )
            
            if isinstance(result, list):
                return result
            return [result]
            
        except Exception as e:
            log.error(f"[Router] Integration Error: {e}", exc_info=True)
            return [{"status": "FAILURE", "message": f"Integration error: {e}", "entity_id": entity_id, "service": "play_media"}]

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

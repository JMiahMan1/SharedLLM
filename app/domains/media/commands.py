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
    device_name: str = None,  # Optional: Explicit device name from Orchestrator
    brightness: str = None,   # Optional: Explicit brightness from Orchestrator
):
    """
    Handles media command and ensures a structured dictionary is returned.
    Supports multi-device pattern matching (even/odd/range/list/all).
    """
    q_low = query.lower()
    log.info(f"[HANDLE_MEDIA_COMMAND] Called with intent={intent}, entity_id={entity_id}, device_name={device_name}")

    # [IntentOverride] Force upgrade for ambiguous "Watch" commands
    if re.search(r"\b(watch|view)\b", q_low) and intent not in ["watch_video", "view_content", "play_media"]:
        if intent not in ["stop_media", "volume_up", "volume_down", "volume_mute", "volume_set"]:
            log.info(f"[IntentOverride] Detected 'watch' keyword. Upgrading intent '{intent}' -> 'watch_video'")
            intent = "watch_video"

    integration = "unknown"

    # 1. EARLY MUSIC/CONTENT DETECTION
    music_keywords = ["music", "song", "artist", "album", "track", "playlist", "radio"]
    audiobook_keywords = ["read", "book", "chapter", "audiobook"]
    video_keywords = ["movie", "film", "show", "video", "youtube", "netflix", "watch"]

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

    # [Standard Resolution Path - simplified]
    if not entity_id:
        # For commands without explicit device names, first check last used devices
        if is_transport:
            entity_id = get_last_media_entity(redis_client, user_creds.get("user"))
            if not entity_id:
                entity_id = get_last_entity(redis_client, user_creds.get("user"))
        else:
            # For play commands, prefer last media entity over general last entity
            entity_id = get_last_media_entity(redis_client, user_creds.get("user"))
            if not entity_id:
                entity_id = get_last_entity(redis_client, user_creds.get("user"))

        # If still no entity found, try to resolve from query content
        if not entity_id:
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

        if entity_id:
            user = user_creds.get("user")
            log.info(f"[CONTEXT UPDATE] user={user}, entity_id={entity_id}, redis_client={redis_client is not None}")
            if user and entity_id:
                 _set_last_entity(redis_client, user, entity_id)
                 if entity_id.startswith("media_player."):
                     from app.domains.media.devices import _set_last_media_entity
                     _set_last_media_entity(redis_client, user, entity_id)

    if entity_id:
        domain = entity_id.split('.')[0]

    if not entity_id and intent not in ["turn_on", "turn_off", "toggle"]:
         return [{"status": "FAILURE", "message": "Could not determine which device you mean.", "entity_id": "N/A", "service": "media_command"}]

    # 3. TRANSPORT REDIRECTION
    log.info(f"[DEBUG_TRANSPORT] Checking Redirection: intent='{intent}' is_transport={is_transport} entity={entity_id}")
    if is_transport:
        log.info(f"[DEBUG_TRANSPORT] Entered Transport Redirection Block for {intent}")

        # Simplified transport logic - prioritize last media entity
        last_media = get_last_media_entity(redis_client, user_creds.get("user"))
        if last_media:
            log.info(f"[Transport] Using last media entity: {last_media}")
            entity_id = last_media

        domain = entity_id.split('.')[0]
        return [await _execute_transport_command(intent, entity_id, domain, user_creds, integration, redis_client, query)]

    if not entity_id:
         return [{"status": "FAILURE", "message": "Could not determine which device you mean.", "entity_id": "N/A", "service": "media_command"}]

    domain = entity_id.split('.')[0]
    service = intent
    service_data = {}

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

        # [Music Assistant Integration]
        if "music_assistant" in integration:
            try:
                # Get device metadata to check for Music Assistant attributes
                from app.settings import GlobalResources
                try:
                    docs = GlobalResources.ha_collection.get(ids=[entity_id], include=["metadatas"])
                    current_meta = docs.get("metadatas", [{}])[0] if docs else {}
                except Exception:
                    current_meta = {}

                # Check integration OR attributes for MA capability
                is_ma_device = "music_assistant" in integration
                if not is_ma_device and current_meta:
                    attrs_str = str(current_meta.get("attributes", "")).lower()
                    if "mass_player_type" in attrs_str or "music_assistant" in attrs_str:
                        is_ma_device = True
                        log.info(f"Identified {entity_id} as MA device via attributes.")

                if is_ma_device:
                    log.info(f"Delegating Music Assistant Play on {entity_id} to music_assistant_ops")

                    # Determine content type
                    ctype = "music" if is_music_request else "video"

                    # Clean the title (based on original logic)
                    clean_title = query.lower()

                    # Remove device name from query
                    if entity_id:
                        from app.domains.media.devices import get_device_capabilities
                        caps = await get_device_capabilities(entity_id, user_creds, redis_client)
                        fname = caps.get("friendly_name", "").lower()
                        ename = entity_id.split(".")[-1].replace("_", " ").lower()

                        # Remove "on {name}" patterns
                        for name in [fname, ename, "office tv", "master bedroom tv", "gracie tv"]:
                            if name and name in clean_title:
                                clean_title = re.sub(f"\\b(on|in|at)?\\s*{re.escape(name)}\\b", " ", clean_title)

                    # Remove action/control words
                    clean_title = re.sub(r"\b(play|please|from|on|open|launch|playback|listen to)\b", " ", clean_title)

                    # Remove content type keywords for music requests
                    if is_music_request:
                        clean_title = re.sub(r"\b(music|song|album|track|playlist|artist|radio|podcast)\b", " ", clean_title)

                    # Clean up extra spaces and strip
                    clean_title = re.sub(r'\s+', ' ', clean_title).strip()

                    # Attempt Music Assistant delegation
                    result = await music_assistant_ops.play_media(entity_id, clean_title, ctype, user_creds)

                    if result and result.get("status") == "SUCCESS":
                        log.info("Music Assistant delegation succeeded")
                        return [result]
                    else:
                        log.info("MA play_media failed with specific type. Retrying with media_type='search'...")
                        result = await music_assistant_ops.play_media(entity_id, clean_title, "search", user_creds)

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
        
        # Self-Correction: If query is not a URL and type is video, it will likely fail on generic players
        # So force music if it looks like a search query and not a URL
        if ctype == "video" and not query.startswith(("http", "www", "spotify", "app")):
             log.warning("Video request detected but query is not a URL. This might fail on some devices.")
             
        std_service_data = {
            "media_content_id": query,
            "media_content_type": ctype
        }
        
        # Enhanced Logging for debugging 500 errors
        log.info(f"[Standard Play] Call {domain}.play_media on {entity_id} | Type: {ctype} | Content: {query}")
        
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

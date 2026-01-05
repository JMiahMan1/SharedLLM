import logging
import asyncio
import json
import re
from typing import List, Dict, Any, Optional

from app.settings import GlobalResources
from app.domains.media.entities import MediaEntity
from app.domains.media.integrations.factory import IntegrationFactory
from app.domains.media.devices import (
    smart_resolve_entity,
    get_last_media_entity, 
    _set_last_media_entity,
    get_last_entity, 
    _set_last_entity
)

log = logging.getLogger("MediaCommands")

# -----------------------------------------------------------------------------
# CORE COMMAND EXECUTION LOGIC
# -----------------------------------------------------------------------------

async def _execute_transport_command(
    intent: str, 
    entity_id: str, 
    domain: str, 
    user_creds: Dict[str, Any],
    integration: str = "unknown", 
    redis_client = None,
    query: str = "",
    metadata: Dict[str, Any] = None,
    **kwargs
) -> Dict[str, Any]:
    """
    Executes a single media command on a specific entity using the appropriate MediaIntegration.
    """
    try:
        # Get the correct integration handler
        handler = IntegrationFactory.get_handler(integration)
        
        # Add credentials to handler context if needed (e.g. for Spotify/User-specific actions)
        if hasattr(handler, "user_creds"):
            handler.user_creds = user_creds

        log.info(f"[HANDLE_MEDIA_COMMAND] Called with intent={intent}, entity_id={entity_id}, device_name=None, integration={integration}")
        
        # Merge extra kwargs into metadata for the handler to use
        if kwargs and not metadata:
            metadata = kwargs
        elif kwargs and metadata:
            metadata.update(kwargs)

        # Route command based on intent
        if intent == "play_media" or intent == "watch_media" or intent == "view_content":
            # Determine media type (music vs video) based on INTENT
            # play_media -> music (Music Assistant)
            # watch_media/view_content -> video (Cast/Roku video)
            if intent == "play_media":
                media_type = "music"
            elif intent == "watch_media" or intent == "view_content":
                media_type = "video"
            else:
                # Fallback for edge cases
                media_type = "video"
            
            # [Media Type Inference from Metadata]
            # Override to music if device is a speaker/MA device (unless explicitly watching)
            if media_type == "video" and metadata:
                 try:
                     attrs = metadata.get("attributes", {})
                     # 1. Check for MA
                     is_ma = False
                     if isinstance(attrs, str):
                          is_ma = "mass_player_type" in attrs or "music_assistant" in attrs
                     elif isinstance(attrs, dict):
                          is_ma = bool(attrs.get("mass_player_type") or attrs.get("music_assistant"))
                     
                     # 2. Check for Speaker
                     # 'integration' arg is unreliable if it came from generic resolve, but metadata['integration'] is better
                     meta_integ = metadata.get("integration", integration)
                     is_speaker = meta_integ in ["speaker", "sonos", "dlna_dmr", "heos", "bose"]
                     if isinstance(attrs, dict) and "speaker" in attrs.get("device_class", ""):
                         is_speaker = True
                     
                     if (is_ma or is_speaker):
                         # Guard against explicit "Watch"
                         if not ("watch" in query.lower() or "video" in query.lower() or "movie" in query.lower()):
                             log.info(f"[Media Type] Inferred 'music' (Speaker/MA Detected via Metadata). Entity: {entity_id}")
                             media_type = "music"
                 except Exception as e:
                     log.warning(f"Error checking metadata for inference: {e}")
            
            # [Fix] Roku + Music Assistant Routing
            # If we are on Roku but intent is music (explicit or inferred), trigger delegation
            # Note: RokuIntegration must handle this delegation now.
            if integration == "roku" and (media_type == "music" or "music" in query.lower() or "prob_music" in query):
                 media_type = "music"

            log.info(f"[Media Type] Set to '{media_type}' for intent '{intent}'. Entity: {entity_id}")
            
            # Pass metadata to handler so it can decide on delegation (Source of Truth)
            # We unpack metadata as kwargs for the handler, but also pass raw metadata if needed
            call_kwargs = {**kwargs} # Copy
            if metadata: 
                # FIRST: Fix the friendly_name in metadata before unpacking
                # Use the top-level friendly_name from metadata (not from attributes JSON which may have extra text)
                if "friendly_name" in metadata:
                    clean_friendly_name = metadata["friendly_name"]
                    log.info(f"[Media] Using friendly_name from metadata: '{clean_friendly_name}'")
                    # Create a copy of metadata with the clean friendly_name to avoid mutation
                    metadata_copy = {**metadata}
                    metadata_copy["friendly_name"] = clean_friendly_name
                    call_kwargs.update(metadata_copy)
                else:
                    call_kwargs.update(metadata)
            if "entity_id" in call_kwargs: del call_kwargs["entity_id"]
            
            return await handler.play_media(entity_id, query, media_type, user_creds=user_creds, metadata=metadata, redis_client=redis_client, **call_kwargs)
            
        elif intent == "media_pause" or intent == "pause_media":
            return await handler.pause_media(entity_id, user_creds=user_creds)
            
        elif intent == "media_play":
            return await handler.play(entity_id, user_creds=user_creds)
            
        elif intent == "media_stop" or intent == "stop_media":
            return await handler.stop_media(entity_id, user_creds=user_creds)
            
        elif intent == "media_next_track" or intent == "media_next":
            return await handler.next_track(entity_id, user_creds=user_creds)
            
        elif intent == "media_previous_track" or intent == "media_previous":
            return await handler.previous_track(entity_id, user_creds=user_creds)
            
        elif intent == "volume_up":
            # Default step is 0.1 (10%)
            return await handler.volume_up(entity_id, step=0.1, user_creds=user_creds)
            
        elif intent == "volume_down":
            return await handler.volume_down(entity_id, step=0.1, user_creds=user_creds)
            
        elif intent == "volume_down":
            return await handler.volume_down(entity_id, step=0.1, user_creds=user_creds)
            
        elif intent == "volume_mute" or intent == "mute_volume":
            # Toggle mute (or set True if supported, but typically toggle)
            should_mute = True
            if "unmute" in query.lower():
                should_mute = False
            return await handler.volume_mute(entity_id, should_mute, user_creds=user_creds)

        elif intent == "volume_set":
            # Extract volume level from query via simple regex (e.g. "volume 50")
            # This logic should ideally be passed in as a parameter, but extracting here for now.
            volume_level = 0.5 # Default
            match = re.search(r'(\d+)', query)
            if match:
                val = int(match.group(1))
                # Normalize 0-100 to 0.0-1.0
                if val > 1: volume_level = val / 100.0
                else: volume_level = float(val)
            
            return await handler.volume_set(entity_id, volume_level, user_creds=user_creds)
            
        elif intent == "turn_on":
            return await handler.turn_on(entity_id, user_creds=user_creds)
            
        elif intent == "turn_off":
            return await handler.turn_off(entity_id, user_creds=user_creds)
            
        elif intent == "nav_home" or intent == "navhome":
            return await handler.nav_home(entity_id, user_creds=user_creds)
            
        elif intent == "open_app":
            return await handler.open_app(entity_id, query, user_creds=user_creds)
            
        else:
            log.warning(f"Unknown intent '{intent}' for media domain.")
            return {"status": "FAILURE", "message": f"Unknown media command: {intent}", "entity_id": entity_id}

    except Exception as e:
        log.error(f"Error executing media command '{intent}' on '{entity_id}': {e}", exc_info=True)
        return {"status": "FAILURE", "message": f"Error executing command: {str(e)}", "entity_id": entity_id}


async def execute_batch_command(
    entities: List[Any], 
    intent: str, 
    query: str, 
    user_creds: Dict[str, Any],
    ha_collection = None,
    redis_client = None
) -> Dict[str, Any]:
    """
    Executes a command on a list of resolved entities concurrently.
    """
    tasks = []
    
    for entity in entities:
        # Resolve entity details
        metadata = {}
        if isinstance(entity, tuple):
             if len(entity) == 3:
                 entity_id, integration, metadata = entity
             else:
                 entity_id, integration = entity
                 
             # [Source of Truth] Use metadata to refine integration
             # WE PASS METADATA DOWN, BUT DO NOT FORCE SWITCH HERE (User Request)
             pass

        else:
             # If it's a Document object (fallback)
             entity_id = entity.metadata.get("entity_id")
             integration = entity.metadata.get("integration", "unknown")
             
        domain = entity_id.split('.')[0]
        
        # Create execution task
        tasks.append(_execute_transport_command(
            intent, 
            entity_id, 
            domain, 
            user_creds, 
            integration, 
            redis_client, 
            query,
            metadata=metadata
        ))

    # Run all tasks concurrently
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # Process results
    success_count = 0
    failure_count = 0
    clean_results = []
    
    for res in results:
        if isinstance(res, Exception):
            failure_count += 1
            clean_results.append({"status": "FAILURE", "message": str(res)})
        elif isinstance(res, dict):
            if res.get("status") == "SUCCESS":
                success_count += 1
            else:
                failure_count += 1
            clean_results.append(res)
            
    # Formulate compound response
    if success_count > 0:
        if failure_count == 0:
            status = "SUCCESS"
            msg = f"Sent command to {success_count} devices."
        else:
            status = "PARTIAL_SUCCESS"
            msg = f"Sent to {success_count} devices, but {failure_count} failed."
    else:
        status = "FAILURE"
        msg = f"Failed to control all {failure_count} devices: {query}"
        
    return {
        "status": status,
        "message": msg,
        "entity_id": "batch_command",
        "service": intent,
        "friendly_name": f"{len(entities)} devices",
        "batch_results": clean_results,
        "success_count": success_count,
        "failure_count": failure_count
    }


# -----------------------------------------------------------------------------
# MAIN HANDLER
# -----------------------------------------------------------------------------

async def handle_media_command(
    intent: str,
    query: str,
    entity_id: str,
    user_creds: dict,
    ha_collection,
    redis_client,
    device_name: str = None,
    brightness: int = None,
    **kwargs
) -> List[Dict[str, Any]]:
    """
    Orchestrates the resolution and execution of a media command.
    """
    # Pre-cleaning for resolution
    cleaned_for_res = query
    if device_name:
        cleaned_for_res = device_name
        
    # Default Integration
    integration = "home_assistant" 
    
    # Detect if this is a music or video request based on query analysis
    # This helps in smart resolution and integration routing
    is_music_request = "music" in query.lower() or "listen" in query.lower() or "song" in query.lower()
    is_video_request = any(x in query.lower() for x in ["watch", "video", "movie", "show", "youtube", "netflix", "disney", "hulu", "plex"]) or intent in ["open_app", "watch_media", "view_content"]

    # Special handling for "play x" without "on y"
    # If parameters has no device_name BUT query implies a specific media type
    # we might want to default to the last used media player of that type.
    strict_resolution = False
    
    # 1. First, try to resolve a specific entity if not provided or if device_name is in query
    if not entity_id:
        
        # [Optimization] Check if "device_name" is actually a Room/Area name
        # If so, we might want to target the 'best' media player in that room.
        # This logic is partly inside smart_resolve_entity but can be reinforced here.
        
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
                    # [Fix] If only 1 entity resolved, treat as single command to allow Context Update (below)
                    if len(resolved) == 1:
                         single_res = resolved[0]
                         if isinstance(single_res, tuple):
                             if len(single_res) == 3:
                                 entity_id, integration, metadata = single_res
                             else:
                                 entity_id, integration = single_res
                         else:
                             # Document fallback
                             entity_id = single_res.metadata.get("entity_id")
                             integration = single_res.metadata.get("integration", "unknown")
                         
                         log.info(f"[Device Fallback] Resolved single entity from list: {entity_id}")
                         # Proceed to standard execution flow (fall through)
                    else:
                        log.info(f"[Device Fallback] Resolved {len(resolved)} entities. Executing Batch.")
                        return await execute_batch_command(resolved, intent, query, user_creds, ha_collection, redis_client)
                else:
                     log.info(f"[Device Fallback] No devices found for {cleaned_for_res}")
            elif isinstance(resolved, tuple):
                 # Handle new (id, integ, meta) format
                 if len(resolved) == 3:
                     entity_id, integration, metadata = resolved
                     
                     # [Source of Truth] Metadata Logic
                     # We preserve metadata for passing down, but DO NOT switch integration here.
                     pass
                             
                 else:
                     entity_id, integration = resolved
                     
                 log.info(f"[Device Fallback] Resolved '{cleaned_for_res}' to {entity_id}")
            elif resolved:
                 entity_id = resolved
                 log.info(f"[Device Fallback] Resolved '{cleaned_for_res}' to {entity_id}")
        except Exception as e:
            log.error(f"[Device Fallback] Error resolving '{cleaned_for_res}': {e}", exc_info=True)

    # 2. Second, fallback to last used devices (Contextual/Implicit)
    if not entity_id:
         # Are we just doing a transport command (pause/next) or a play command?
         is_transport = intent in ["media_pause", "media_stop", "media_next_track", "media_previous_track", "media_next", "media_previous", "volume_up", "volume_down"]
         
         if is_transport:
            # Priority 1: Check Redis for last explicitly used media entity
            entity_id = get_last_media_entity(redis_client, user_creds.get("user"))
            if not entity_id:
                entity_id = get_last_entity(redis_client, user_creds.get("user"))
            
            if entity_id:
                log.info(f"[Context] Using last media entity from Redis: {entity_id}")
            else:
                # Priority 2: Only if Redis is empty, check for ACTIVE players as fallback
                from app.domains.media.devices import get_active_media_players
                active_players = await get_active_media_players(user_creds)
                if active_players:
                    entity_id = active_players[0]  # Use first active player
                    log.info(f"[Active Player Fallback] No Redis context, using currently playing device: {entity_id}")
                else:
                    log.warning(f"[No Context] No Redis context and no active players found for transport command")
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
        
        # If we didn't get integration from resolver, try to fetch it
        # [CRITICAL] Force Roku devices to use RokuIntegration
        # Check metadata for Roku-specific markers (manufacturer, platform, etc) 
        if entity_id:
            try:
                if ha_collection:
                    docs = ha_collection.get(ids=[entity_id], include=["metadatas"])
                    if docs and docs.get("metadatas") and len(docs["metadatas"]) > 0:
                        meta = docs["metadatas"][0]
                        
                        # [Integration Inference]
                        # Restore variable definitions needed for logic below
                        manufacturer = meta.get("manufacturer", "").lower()
                        model = meta.get("model", "").lower()
                        platform = meta.get("platform", "").lower()
                        found_int = meta.get("integration", "").lower()

                        # Explicit platform check for Android TV
                        if "androidtv" in platform or "androidtv" in found_int or "shield" in model or "sti614" in model:
                             log.info(f"[Integration Inference] Detected Android TV via metadata (Model: {model}). Forcing 'androidtv'.")
                             integration = "androidtv"
                        # Explicit platform check for Roku
                        elif "roku" in platform or "roku" in found_int or "roku" in manufacturer:
                             log.info(f"[Integration Inference] Detected Roku via metadata. Forcing 'roku'.")
                             integration = "roku"

                        # If the resolved entity has a generic integration (remote, switch, etc.),
                        # look for a sibling in the same device group to find the "true" main integration.
                        # This handles "TCL Roku TV" where the remote entity is just "remote" but the media_player is "roku".
                        
                        inferred_integration = found_int
                        if integration == "androidtv": inferred_integration = "androidtv"
                        if integration == "roku": inferred_integration = "roku"
                        
                        if deferred_check := (integration in ["home_assistant", "remote", "unknown", "tv"] or found_int in ["remote", "tv"]):
                             log.info(f"[Integration Inference] Entity {entity_id} has generic integration '{found_int}'. Checking group siblings...")
                             group_id = meta.get("group_id")
                             if group_id and group_id != "unknown":
                                  try:
                                     # Look for siblings
                                     group_docs = ha_collection._collection.get(
                                         where={"group_id": group_id},
                                         include=["metadatas"]
                                     )
                                     if group_docs and group_docs.get("metadatas"):
                                         for sibling in group_docs["metadatas"]:
                                             sib_int = sibling.get("integration", "").lower()
                                             if sib_int in ["androidtv", "roku", "webostv", "samsungtv", "cast", "esphome"]:
                                                 # [Fix] Only adopt sibling if we are NOT currently active
                                                 # If we are Cast and playing/paused, don't switch to AndroidTV sibling
                                                 is_active = False
                                                 try:
                                                      if meta.get("state") in ["playing", "paused", "buffering"]:
                                                           is_active = True
                                                 except: pass

                                                 if not is_active:
                                                      log.info(f"[Integration Inference] Found sibling {sibling.get('entity_id')} with definitive integration '{sib_int}'. Adopting.")
                                                      inferred_integration = sib_int
                                                      break
                                                 else:
                                                      log.info(f"[Integration Inference] Found sibling {sibling.get('entity_id')} ({sib_int}) but current device is ACTIVE/PAUSED. Staying put.")
                                  except Exception as search_err:
                                     log.warning(f"[Integration Inference] Sibling search failed: {search_err}")

                        # Apply Inferred Integration
                        if inferred_integration and inferred_integration != "unknown":
                             if integration != inferred_integration:
                                 log.info(f"[Integration Override] Correcting integration from '{integration}' to '{inferred_integration}' (Source: Metadata/Sibling)")
                                 integration = inferred_integration
            except Exception as e:
                log.warning(f"[Context] Failed to check metadata for {entity_id}: {e}")




        # If it's a script/scene/automation, execute immediately via standard handler
        # (This bypasses the complex media logic below)
        domain = entity_id.split('.')[0]
        if domain in ["script", "scene", "automation", "switch", "light", "input_boolean"]:
             return [await _execute_transport_command(intent, entity_id, domain, user_creds, integration, redis_client, query)]

    # [Patch Removed] Roku integration is now handled via Metadata Inference above.
    # This prevents hardcoding behavior based on entity ID strings.
    
    if not entity_id:
         return [{"status": "FAILURE", "message": "Could not determine which device you mean.", "entity_id": "N/A", "service": "media_command"}]

    domain = entity_id.split('.')[0]
    
    # [Volume Optimization] Refine entity target for volume commands
    # This ensures the right device handles each volume operation regardless of resolution path
    log.info(f"[VOLUME CHECK] intent={intent}, entity_id={entity_id}")
    if intent.startswith("volume_"):
        log.info(f"[VOLUME CHECK] Calling _refine_target_for_volume")
        from app.domains.media.devices import _refine_target_for_volume
        refined_entity = await _refine_target_for_volume(entity_id, intent, redis_client)
        if refined_entity != entity_id:
            log.info(f"[VOLUME REFINEMENT] Switched {entity_id} -> {refined_entity} for {intent}")
            entity_id = refined_entity
            domain = entity_id.split('.')[0]
    
    service = intent
    service_data = {}

    # [**UNIVERSAL** MASS INTELLIGENCE SWAP] - Run for ALL music requests
    # MASS swap logic removed - now handled per-integration in RokuIntegration and CastIntegration

    # [**TV INTELLIGENCE SWAP**] - For VIDEO or NAVIGATION requests, swap speaker/cast to actual TV
    # If we have a video/nav request but resolved to a speaker/cast, find the TV in the same group
    if intent in ["play_media", "open_app", "watch_video", "view_content", "nav_home", "nav_back", "nav_up", "nav_down", "nav_left", "nav_right", "nav_enter"]:
        if (is_video_request or intent.startswith("nav_")) and integration in ["cast", "music_assistant"]:
            try:
                # Get the current device's group_id from ChromaDB
                # GlobalResources is already imported at module level
                current_docs = GlobalResources.ha_collection.get(ids=[entity_id], include=["metadatas"])
                if current_docs and current_docs.get("metadatas"):
                    current_group_id = current_docs["metadatas"][0].get("group_id")
                    current_group_name = current_docs["metadatas"][0].get("group_name", "")
                   
                    if current_group_id:
                        log.info(f"[TV Swap] Video request on {integration} device. group_id={current_group_id}, searching for TV...")
                        
                        # [Optimization] If the current Cast/MA device is actively playing or paused, 
                        # we likely want to Control THAT session, not swap to the TV (which might be idle).
                        # This fixes "Resume" failing because it swapped to the TV which had no active media.
                        should_swap = True
                        if intent in ["play_media", "media_play", "media_pause", "media_stop", "media_next_track", "media_previous_track"]:
                             try:
                                 cur_state = current_docs["metadatas"][0].get("state")
                                 if cur_state in ["playing", "paused", "buffering"]:
                                     log.info(f"[TV Swap] Aborting swap. Current device {entity_id} is {cur_state} - assuming session control.")
                                     should_swap = False
                             except: pass

                        if should_swap:
                            # Find other devices in the same group
                            group_docs = GlobalResources.ha_collection._collection.get(
                                where={"group_id": current_group_id},
                                include=["metadatas"]
                            )
                            
                            found_tv = None
                            
                            if group_docs and group_docs.get("metadatas"):
                                for metadata in group_docs["metadatas"]:
                                    # Look for devices that are likely TVs ( Roku, Android TV, WebOS, etc)
                                    # Hint: integration name or device_class might help, but let's check integration list
                                    other_integration = metadata.get("integration", "").lower()
                                    other_entity = metadata.get("entity_id")
                                    
                                    # Skip self
                                    if other_entity == entity_id: continue
                                    
                                    if other_integration in ["roku", "androidtv", "webostv", "samsungtv", "braviatv", "esphome"]:
                                         found_tv = other_entity
                                         found_integration = other_integration
                                         log.info(f"[TV Swap] Found TV кандидат: {found_tv} ({found_integration})")
                                         break
                                         
                            if found_tv:
                                log.info(f"[TV Swap] SUCCESS! Swapping {entity_id} -> {found_tv} for video playback.")
                                entity_id = found_tv
                                integration = found_integration
                                domain = entity_id.split('.')[0]
                            
            except Exception as e:
                log.warning(f"[TV Swap] Error: {e}")


    log.info(f"[HANDLE_MEDIA_COMMAND] Final Target: {entity_id} ({integration}) Intent: {intent}")

    # Execution
    # Ensure metadata is available for passing
    if 'metadata' not in locals():
        metadata = {}
    
    # Merge kwargs into metadata just in case
    if kwargs:
        metadata.update(kwargs)
        
    # [Fix] Ensure device_name (spoken alias) is passed to integration for proper query cleaning
    if device_name:
        kwargs["device_name"] = device_name
        
    return [await _execute_transport_command(intent, entity_id, domain, user_creds, integration, redis_client, query, metadata=metadata, **kwargs)]

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
            # Determine media type (music vs video) based on query/context
            # Simple heuristic: "watch" -> video, "listen" -> music.
            # Default to "video" for now if ambiguous, or "music" if integration is music-focused.
            media_type = "video"
            
            # [Media Type Inference]
            # Use Metadata (Source of Truth) to default "Play" -> Music for Speakers/MA devices.
            # We do NOT switch the integration here (User Request), only the Intent (media_type).
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
            if integration == "roku" and ("music" in query.lower() or "prob_music" in query):
                 media_type = "music"

            log.info(f"[Media Type] Set by intent '{intent}': {media_type}")
            
            # Pass metadata to handler so it can decide on delegation (Source of Truth)
            # We unpack metadata as kwargs for the handler, but also pass raw metadata if needed
            call_kwargs = {**kwargs} # Copy
            if metadata: call_kwargs.update(metadata)
            if "entity_id" in call_kwargs: del call_kwargs["entity_id"]
            
            return await handler.play_media(entity_id, query, media_type, user_creds=user_creds, metadata=metadata, **call_kwargs)
            
        elif intent == "media_pause":
            return await handler.pause_media(entity_id, user_creds=user_creds)
            
        elif intent == "media_play":
            return await handler.play(entity_id, user_creds=user_creds)
            
        elif intent == "media_stop":
            return await handler.stop_media(entity_id, user_creds=user_creds)
            
        elif intent == "media_next_track":
            return await handler.next_track(entity_id, user_creds=user_creds)
            
        elif intent == "media_previous_track":
            return await handler.previous_track(entity_id, user_creds=user_creds)
            
        elif intent == "volume_up":
            # Default step is 0.1 (10%)
            return await handler.volume_up(entity_id, step=0.1, user_creds=user_creds)
            
        elif intent == "volume_down":
            return await handler.volume_down(entity_id, step=0.1, user_creds=user_creds)
            
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
            
            return await handler.set_volume(entity_id, volume_level, user_creds=user_creds)
            
        elif intent == "turn_on":
            return await handler.turn_on(entity_id, user_creds=user_creds)
            
        elif intent == "turn_off":
            return await handler.turn_off(entity_id, user_creds=user_creds)
            
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
    is_video_request = "watch" in query.lower() or "video" in query.lower() or "movie" in query.lower() or "show" in query.lower()

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
         is_transport = intent in ["media_pause", "media_stop", "media_next_track", "media_previous_track", "volume_up", "volume_down"]
         
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
        
        # If we didn't get integration from resolver, try to fetch it
        if integration == "home_assistant" or integration == "unknown":
            pass # Skipping redundant lookup - reliance on smart_resolve_entity is preferred.

        # If it's a script/scene/automation, execute immediately via standard handler
        # (This bypasses the complex media logic below)
        domain = entity_id.split('.')[0]
        if domain in ["script", "scene", "automation", "switch", "light", "input_boolean"]:
             return [await _execute_transport_command(intent, entity_id, domain, user_creds, integration, redis_client, query)]

    if not entity_id:
         return [{"status": "FAILURE", "message": "Could not determine which device you mean.", "entity_id": "N/A", "service": "media_command"}]

    domain = entity_id.split('.')[0]
    service = intent
    service_data = {}

    # [**UNIVERSAL** MASS INTELLIGENCE SWAP] - Run for ALL music requests
    # MASS swap logic removed - now handled per-integration in RokuIntegration and CastIntegration

    # [**TV INTELLIGENCE SWAP**] - For VIDEO requests, swap speaker/cast to actual TV
    # If we have a video request but resolved to a speaker/cast, find the TV in the same group
    if intent in ["play_media", "open_app", "watch_video", "view_content"]:
        if is_video_request and integration in ["cast", "music_assistant"]:
            try:
                # Get the current device's group_id from ChromaDB
                # GlobalResources is already imported at module level
                current_docs = GlobalResources.ha_collection.get(ids=[entity_id], include=["metadatas"])
                if current_docs and current_docs.get("metadatas"):
                    current_group_id = current_docs["metadatas"][0].get("group_id")
                    current_group_name = current_docs["metadatas"][0].get("group_name", "")
                   
                    if current_group_id:
                        log.info(f"[TV Swap] Video request on {integration} device. group_id={current_group_id}, searching for TV...")
                        
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
        
    return [await _execute_transport_command(intent, entity_id, domain, user_creds, integration, redis_client, query, metadata=metadata, **kwargs)]

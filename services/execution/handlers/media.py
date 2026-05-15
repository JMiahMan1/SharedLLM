# services/execution/handlers/media.py
import logging
import asyncio
import re
try:
    import ha_client
    from schemas import MediaPlayRequest, MediaTransportRequest, TVCastRequest, ExecutionResult
except ImportError:
    import ha_client
    from schemas import MediaPlayRequest, MediaTransportRequest, TVCastRequest, ExecutionResult

log = logging.getLogger("execution.media")

async def resolve_mass_entity(ctx, original_entity: str) -> str:
    """
    Resolve a media_player entity to its Music Assistant variant.
    Many Cast/AndroidTV devices have both a native media_player and a MASS media_player.
    Music playback works best through the MASS entity.
    """
    all_states = await ha_client.get_states(ctx.ha_url, ctx.ha_token)
    if not all_states:
        return original_entity
    
    # Extract the device name from the original entity
    name_part = original_entity.replace("media_player.", "")
    
    # Find the friendly_name of the original entity
    original_friendly = None
    for state in all_states:
        if state.get("entity_id") == original_entity:
            original_friendly = state.get("attributes", {}).get("friendly_name", "")
            break
    
    if not original_friendly:
        return original_entity
    
    search = original_friendly.lower()
    
    # Look for a MASS entity with matching friendly name
    for state in all_states:
        eid = state.get("entity_id", "")
        if not eid.startswith("media_player."):
            continue
        attrs = state.get("attributes", {})
        friendly = attrs.get("friendly_name", "").lower()
        source = attrs.get("source", "").lower()
        integration = attrs.get("integration", "")
        
        # Match by friendly name AND (music_assistant source or integration)
        if search in friendly and ("music assistant" in source or integration == "music_assistant"):
            if eid != original_entity:
                log.info(f"[media/play] Resolved MASS entity: {original_entity} -> {eid}")
                return eid
    
    return original_entity


async def handle_media_play(req: MediaPlayRequest) -> ExecutionResult:
    ctx = req.user_context
    full_entity_id = ha_client.sanitize_entity_id("media_player", req.entity_id)
    log.info(f"[media/play] user={ctx.user} entity={full_entity_id} (original={req.entity_id})")

    # Music Assistant library lookup path.
    if req.query:
        # Resolve to MASS entity if available
        mass_entity = await resolve_mass_entity(ctx, full_entity_id)
        
        # Step 1: Search MASS for the query to get a proper URI
        # Note: music_assistant.search does NOT require an entity_id
        search_result = await ha_client.call_service(
            ctx.ha_url,
            ctx.ha_token,
            "music_assistant",
            "search",
            entity_id="",
            service_data={
                "config_entry_id": "01KMKEW7FVVXHQAB89YMYDZNAT",
                "name": req.query,
                "media_type": ["track", "artist", "album", "playlist", "radio"],
                "limit": 5,
            },
            return_response=True,
        )
        
        if search_result.get("ok") and search_result.get("service_response"):
            # HA wraps the response: {"changed_states": [], "service_response": {actual_results}}
            raw = search_result["service_response"]
            resp = raw.get("service_response", raw)
            # Try tracks first, then albums, artists, playlists, radio
            uri = None
            media_type_label = ""
            for category in ["tracks", "albums", "artists", "playlists", "radio"]:
                items = resp.get(category, [])
                if items:
                    uri = items[0].get("uri")
                    media_type_label = category
                    break
            
            if uri:
                log.info(f"[media/play] MASS search found: {uri} ({media_type_label})")
                result = await ha_client.call_service(
                    ctx.ha_url,
                    ctx.ha_token,
                    "music_assistant",
                    "play_media",
                    mass_entity,
                    {
                        "media_id": uri,
                        "enqueue": "play" if req.enqueue == "replace" else req.enqueue,
                    },
                )
                if result.get("ok"):
                    return ExecutionResult(
                        status="SUCCESS",
                        message=f"Music Assistant playback started for '{req.query}' ({media_type_label}).",
                        service="media_play",
                    )
            else:
                log.warning(f"[media/play] MASS search returned no results for '{req.query}'")

        # Fallback: standard media_player.play_media (for URLs/video casting)
        result = await ha_client.call_service(
            ctx.ha_url,
            ctx.ha_token,
            "media_player",
            "play_media",
            full_entity_id,
            {
                "media": {
                    "media_content_id": req.query,
                    "media_content_type": req.media_content_type or "music"
                },
                "enqueue": "play" if req.enqueue == "replace" else req.enqueue,
            },
        )
        if result.get("ok"):
            return ExecutionResult(
                status="SUCCESS",
                message=f"Playback started for '{req.query}'.",
                service="media_play",
            )

        return ExecutionResult(
            status="FAILURE",
            message=f"Playback failed for '{req.query}'.",
            service="media_play",
        )

    # --- LEGACY PORT: YouTube Deep Linking ---
    url = req.media_content_id or ""
    yt_match = re.search(r"(?:v=|/)([0-9A-Za-z_-]{11}).*", url)
    
    service_data: dict = {}
    if ("youtube" in url or "youtu.be" in url) and yt_match:
        video_id = yt_match.group(1)
        log.info(f"[media/play] Detected YouTube URL. Deep-linking to video {video_id}")
        # If it's a Roku, use the native deep-link format
        if "roku" in req.entity_id:
             service_data = {
                "media_content_id": "837", # YouTube App ID
                "media_content_type": "app",
                "extra": {"content_id": video_id, "media_type": "live"}
             }
        else:
             # Standard Cast / Android TV YouTube format
             service_data["media_content_id"] = video_id
             service_data["media_content_type"] = "video/youtube"
    else:
        if req.media_content_id:
            service_data["media_content_id"] = req.media_content_id
            service_data["media_content_type"] = req.media_content_type or "url"

    if req.enqueue:
        service_data["enqueue"] = req.enqueue

    # Auto-power-on check
    state = await ha_client.get_state(ctx.ha_url, ctx.ha_token, full_entity_id)
    if state and state.get("state") == "off":
        log.info(f"[media/play] Device {full_entity_id} is off. Turning on...")
        await ha_client.call_service(ctx.ha_url, ctx.ha_token, "media_player", "turn_on", full_entity_id)
        await asyncio.sleep(2)

    result = await ha_client.call_service(
        ctx.ha_url, ctx.ha_token,
        "media_player", "play_media",
        full_entity_id, service_data or None,
    )
    
    if result.get("ok"):
        return ExecutionResult(status="SUCCESS", message="Playback started.", service="media_play")
    return ExecutionResult(status="FAILURE", message=f"Playback failed: {result.get('error')}", service="media_play", detail=result)

async def handle_media_transport(req: MediaTransportRequest) -> ExecutionResult:
    ctx = req.user_context
    
    # --- LEGACY PORT: Remote Button Mapping ---
    button_map = {
        "home": "HOME", "back": "BACK", "up": "UP", "down": "DOWN", "left": "LEFT", "right": "RIGHT",
        "select": "SELECT", "enter": "SELECT", "ok": "SELECT", "info": "INFO", "replay": "INSTANT_REPLAY",
        "pause": "media_pause", "resume": "media_play", "stop": "media_stop", 
        "next": "media_next_track", "previous": "media_previous_track",
        "volume_up": "volume_up", "volume_down": "volume_down", "mute": "volume_mute"
    }
    
    service = button_map.get(req.command.lower(), req.command)
    full_entity_id = ha_client.sanitize_entity_id("media_player", req.entity_id)
    domain = full_entity_id.split(".")[0]
    target_entity = full_entity_id
    
    # If it's a "button" command (Remote), switch to remote domain
    if service.isupper():
        domain = "remote"
        service_cmd = "send_command"
        data = {"command": service}
        # Roku: media_player.roku -> remote.roku
        if "media_player" in target_entity:
            target_entity = target_entity.replace("media_player.", "remote.")
    else:
        service_cmd = service
        data = {}

    if req.command in ("volume_up", "volume_down") and req.volume_level is not None:
        service_cmd = "volume_set"
        data = {"volume_level": req.volume_level}

    result = await ha_client.call_service(
        ctx.ha_url, ctx.ha_token,
        domain, service_cmd,
        target_entity, data or None,
    )
    
    if result.get("ok"):
        return ExecutionResult(status="SUCCESS", message=f"Media command '{service}' executed.", service="media_transport")
    return ExecutionResult(status="FAILURE", message=f"Media command failed: {result.get('error')}", service="media_transport", detail=result)

async def handle_tv_cast(req: TVCastRequest) -> ExecutionResult:
    # This is now largely redundant with refined handle_media_play, but keeping for specialized casting
    ctx = req.user_context
    log.info(f"[tv/cast] entity={req.media_player_entity_id} content={req.media_content_id}")
    
    play_req = MediaPlayRequest(
        user_context=ctx,
        entity_id=req.media_player_entity_id,
        media_content_id=req.media_content_id,
        media_content_type=req.media_content_type
    )
    return await handle_media_play(play_req)

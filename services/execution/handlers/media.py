# services/execution/handlers/media.py
import logging
import asyncio
try:
    import ha_client
    from schemas import MediaPlayRequest, MediaTransportRequest, TVCastRequest, ExecutionResult
except ImportError:
    import ha_client
    from schemas import MediaPlayRequest, MediaTransportRequest, TVCastRequest, ExecutionResult

log = logging.getLogger("execution.media")

async def handle_media_play(req: MediaPlayRequest) -> ExecutionResult:
    ctx = req.user_context
    full_entity_id = ha_client.sanitize_entity_id("media_player", req.entity_id)
    log.info(f"[media/play] user={ctx.user} entity={full_entity_id} (original={req.entity_id})")

    # Music Assistant library lookup path.
    if req.query:
        fallback_types = [req.media_content_type or "artist", "search", "music"]
        seen = set()
        ordered_types = []
        for media_type in fallback_types:
            if media_type and media_type not in seen:
                ordered_types.append(media_type)
                seen.add(media_type)

        for media_type in ordered_types:
            result = await ha_client.call_service(
                ctx.ha_url,
                ctx.ha_token,
                "music_assistant",
                "play_media",
                full_entity_id,
                {
                    "media_id": req.query,
                    "media_type": media_type,
                    "enqueue": "play" if req.enqueue == "replace" else req.enqueue,
                },
            )
            if result.get("ok"):
                return ExecutionResult(
                    status="SUCCESS",
                    message=f"Music Assistant playback started for '{req.query}' ({media_type}).",
                    service="media_play",
                )

        return ExecutionResult(
            status="FAILURE",
            message=f"Music Assistant playback failed for '{req.query}'.",
            service="media_play",
        )

    # --- LEGACY PORT: YouTube Deep Linking ---
    import re
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
             service_data["media_content_id"] = video_id
             service_data["media_content_type"] = "youtube"
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

# services/execution/handlers/media.py
import logging
import asyncio
import re
from typing import cast
from services.execution import ha_client
from services.execution.schemas import MediaPlayRequest, ExecutionResult, AudiobookshelfRequest
from services.config import MASS_CONFIG_ENTRY_ID

log = logging.getLogger("execution.media")

# Media type detection patterns
VIDEO_PATTERNS = [r"youtube\.com", r"youtu\.be", r"vimeo\.com", r"rumble\.com", r"dailymotion\.com", r"tiktok\.com", r"twitch\.tv", r"\byoutube\b", r"\bvideo\b"]
AUDIOBOOK_PATTERNS = [r"audiobookshelf", r"abs", r"audiobook", r"book\s+"]
PODCAST_PATTERNS = [r"podcast", r"episode", r"show\s+", r"itunes\.apple\.com", r"open\.spotify\.com/show"]
MUSIC_PATTERNS = [r"spotify\.com/track", r"spotify\.com/album", r"soundcloud\.com", r"bandcamp\.com"]

def detect_media_type(query: str, media_type_hint: str | None = None) -> str:
    """Detect media type from query content and hints."""
    if media_type_hint and media_type_hint in ("music", "video", "podcast", "audiobook", "radio", "url", "announcement"):
        return media_type_hint
    
    if not query:
        return "music"
    
    query_lower = query.lower()
    
    # Check if query is a URL
    if query_lower.startswith(("http://", "https://")):
        for pattern in VIDEO_PATTERNS:
            if re.search(pattern, query_lower):
                return "video"
        for pattern in PODCAST_PATTERNS:
            if re.search(pattern, query_lower):
                return "podcast"
        for pattern in MUSIC_PATTERNS:
            if re.search(pattern, query_lower):
                return "music"
        return "url"
    
    # Check for media type keywords in query
    for pattern in VIDEO_PATTERNS:
        if re.search(pattern, query_lower):
            return "video"
    
    # Check for audiobook indicators
    if any(kw in query_lower for kw in ["audiobook", "read by", "narrated by", "chapter"]):
        return "audiobook"
    
    # Check for podcast indicators
    if any(kw in query_lower for kw in ["podcast", "episode of", "the daily show", "joe rogan"]):
        return "podcast"
    
    # Default to music
    return "music"

async def resolve_entity(req: MediaPlayRequest, ha_url: str, ha_token: str, media_type: str | None = None) -> str:
    """Resolve entity_id from device_name if needed."""
    if req.entity_id:
        return ha_client.sanitize_entity_id("media_player", req.entity_id)
    
    if req.device_name:
        entity_id = await ha_client.resolve_entity_by_name(ha_url, ha_token, req.device_name, "media_player", media_type)
        if entity_id:
            return entity_id
    
    raise ValueError("entity_id or device_name is required")

async def resolve_mass_entity(ctx, original_entity: str) -> str:
    """Resolve a media_player entity to its Music Assistant variant.
    
    Only returns MA players that have an active_queue (i.e., are connected to a MA output).
    """
    all_states = await ha_client.get_states(ctx.ha_url, ctx.ha_token)
    if not all_states:
        return original_entity
    
    original_friendly = None
    for state in all_states:
        if state.get("entity_id") == original_entity:
            original_friendly = state.get("attributes", {}).get("friendly_name", "")
            break
    
    if not original_friendly:
        return original_entity
    
    search = original_friendly.lower()
    
    for state in all_states:
        eid = state.get("entity_id", "")
        if not eid.startswith("media_player."):
            continue
        attrs = state.get("attributes", {})
        friendly = attrs.get("friendly_name", "").lower()
        source = attrs.get("source", "").lower()
        integration = attrs.get("integration", "")
        active_queue = attrs.get("active_queue")
        
        # Must have an active MA queue to be a valid playback target
        if not active_queue:
            continue
        
        if search in friendly and ("music assistant" in source or integration == "music_assistant"):
            if eid != original_entity:
                log.info(f"[media/play] Resolved MASS entity: {original_entity} -> {eid} (queue: {active_queue})")
                return eid
    
    return original_entity

async def play_music(req: MediaPlayRequest, entity_id: str, ctx) -> ExecutionResult:
    """Play music via Music Assistant or fallback."""
    from . import roku as roku_handler
    
    is_roku = await roku_handler.is_roku_device(ctx.ha_url, ctx.ha_token, entity_id)
    
    # Samsung Tizen TV: use MASS search for music, then play URL via play_media
    from . import samsung as samsung_handler
    is_samsung = await samsung_handler.is_samsung_tv(ctx.ha_url, ctx.ha_token, entity_id)
    
    # Resolve MA config entry at runtime if not seeded
    mass_entry = MASS_CONFIG_ENTRY_ID
    if not mass_entry:
        mass_entry = await ha_client.find_mass_config_entry(ctx.ha_url, ctx.ha_token)
    
    if is_roku:
        log.info("[media/play] Detected Roku device, using Roku music handler")
        return await roku_handler.roku_play_music(
            ctx.ha_url, ctx.ha_token, entity_id, req.query or "", mass_entry,
        )
    
    mass_entity = await resolve_mass_entity(ctx, entity_id)
    
    if req.query:
        log.info(f"[media/play] Searching MASS for '{req.query}' on {entity_id}")
        search_result = await ha_client.call_service(
            ctx.ha_url, ctx.ha_token, "music_assistant", "search", entity_id="",
            service_data={
                "config_entry_id": mass_entry,
                "name": req.query,
                "media_type": ["track", "artist", "album", "playlist", "radio"],
                "limit": 5,
            },
            return_response=True,
        )
        
        if search_result.get("ok") and search_result.get("service_response"):
            raw = search_result["service_response"]
            resp = raw.get("service_response", raw)
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
                
                # Samsung TV: play the MA URL directly via play_media
                if is_samsung:
                    log.info("[media/play] Samsung TV detected, playing MASS URL via play_media")
                    return await samsung_handler.play_music(
                        ctx.ha_url, ctx.ha_token, entity_id, uri,
                    )
                
                result = await ha_client.call_service(
                    ctx.ha_url, ctx.ha_token, "music_assistant", "play_media", mass_entity,
                    {"media_id": uri, "enqueue": "play" if req.enqueue == "replace" else req.enqueue},
                )
                if result.get("ok"):
                    return ExecutionResult(status="SUCCESS", message=f"Playing '{req.query}' ({media_type_label}) on {entity_id}.", service="media_play")
        
        # Search returned nothing — try get_library random for generic queries
        log.warning(f"[media/play] MASS search returned 0 results for query='{req.query}' (config_entry={mass_entry}), falling back to library random")
        library_result = await ha_client.call_service(
            ctx.ha_url, ctx.ha_token, "music_assistant", "get_library", entity_id="",
            service_data={
                "config_entry_id": mass_entry,
                "media_type": "track",
                "limit": 1,
                "order_by": "random",
            },
            return_response=True,
        )
        
        if library_result.get("ok") and library_result.get("service_response"):
            raw = library_result["service_response"]
            resp = raw.get("service_response", raw)
            items = resp.get("items", [])
            if items:
                uri = items[0].get("uri")
                track_name = items[0].get("name", "unknown")
                log.info(f"[media/play] Library random fallback selected: '{track_name}' ({uri})")
                
                # Samsung TV: play the MA URL directly via play_media
                if is_samsung:
                    return await samsung_handler.play_music(
                        ctx.ha_url, ctx.ha_token, entity_id, uri,
                    )
                
                result = await ha_client.call_service(
                    ctx.ha_url, ctx.ha_token, "music_assistant", "play_media", mass_entity,
                    {"media_id": uri, "enqueue": "play" if req.enqueue == "replace" else req.enqueue},
                )
                if result.get("ok"):
                    return ExecutionResult(status="SUCCESS", message=f"Playing random track on {entity_id}.", service="media_play")
        
        result = await ha_client.call_service(
            ctx.ha_url, ctx.ha_token, "music_assistant", "play_media", mass_entity,
            {"media_id": req.query, "media_type": "track", "enqueue": "play"},
        )
        if result.get("ok"):
            return ExecutionResult(status="SUCCESS", message=f"Playing '{req.query}' on {entity_id}.", service="media_play")
    
    return ExecutionResult(status="FAILURE", message=f"Could not play '{req.query}' on {entity_id}.", service="media_play")

async def play_video(req: MediaPlayRequest, entity_id: str, ctx) -> ExecutionResult:
    """Play video via yt-dlp stream. Routes through Roku ECP for Roku devices."""
    from . import video as video_handler
    from . import roku as roku_handler

    query = req.query or req.media_content_id or ""

    # Check if already a URL
    video_url = video_handler.extract_video_url(query)
    if not video_url:
        video_url = await video_handler.search_youtube(query)
        if not video_url:
            return ExecutionResult(status="FAILURE", message=f"Could not find video for '{query}'.", service="media_play")

    # Roku: download optimized format with progressive playback, then launch via ECP
    is_roku = await roku_handler.is_roku_device(ctx.ha_url, ctx.ha_token, entity_id)
    if is_roku:
        log.info("[media.video] Detected Roku device, using Roku ECP handler with progressive download")
        # Wake device in parallel with download
        wake_task = asyncio.create_task(roku_handler.roku_wake_device(ctx.ha_url, ctx.ha_token, entity_id))
        media_id, title = await video_handler.download_video_progressive(video_url)
        if not media_id:
            return ExecutionResult(status="FAILURE", message=f"Failed to download video for '{query}'.", service="media_play")
        await wake_task
        from services.config import EXECUTION_EXTERNAL_HOST
        if not EXECUTION_EXTERNAL_HOST:
            return ExecutionResult(status="FAILURE", message="EXECUTION_EXTERNAL_HOST is not configured.", service="media_play")
        stream_url = f"http://{EXECUTION_EXTERNAL_HOST}:8888/media/{media_id}"
        return await roku_handler.roku_play_video(
            ctx.ha_url, ctx.ha_token, entity_id, stream_url, title or query,
        )

    # Android TV: delegate video to Cast sibling (non-MA) for reliable playback
    from . import android_tv as android_tv_handler
    is_android_tv = await android_tv_handler.is_android_tv(ctx.ha_url, ctx.ha_token, entity_id)
    if is_android_tv:
        log.info(f"[media.video] Android TV detected ({entity_id}), delegating to android_tv handler")
        return await android_tv_handler.play_video(ctx.ha_url, ctx.ha_token, entity_id, video_url, query)

    # Samsung Tizen TV: use dedicated handler with WOL wake and play_media
    from . import samsung as samsung_handler
    is_samsung = await samsung_handler.is_samsung_tv(ctx.ha_url, ctx.ha_token, entity_id)
    if is_samsung:
        log.info(f"[media.video] Samsung Tizen TV detected ({entity_id}), using samsung handler")
        download_task = asyncio.create_task(video_handler.download_video_progressive(video_url))
        wake_task = asyncio.create_task(samsung_handler.wake_device(ctx.ha_url, ctx.ha_token, entity_id))

        media_id, title = await download_task
        if not media_id:
            return ExecutionResult(status="FAILURE", message=f"Failed to download video for '{query}'.", service="media_play")
        await wake_task

        from services.config import EXECUTION_EXTERNAL_HOST
        if not EXECUTION_EXTERNAL_HOST:
            return ExecutionResult(status="FAILURE", message="EXECUTION_EXTERNAL_HOST is not configured.", service="media_play")
        stream_url = f"http://{EXECUTION_EXTERNAL_HOST}:8888/media/{media_id}"
        return await samsung_handler.play_video(ctx.ha_url, ctx.ha_token, entity_id, stream_url, title or query)

    # Cast/WebOS: stop active session, ensure TV is on, then play
    # Note: handle_media_play already handles power-on, so we skip redundant turn_on here
    # Start download in parallel with HA setup to save time
    download_task = asyncio.create_task(video_handler.download_video_progressive(video_url))

    # Stop any active session first (e.g., Music Assistant) to prevent conflicts
    log.info(f"[media.video] Stopping active session on {entity_id} before video playback")
    try:
        await ha_client.call_service(ctx.ha_url, ctx.ha_token, "media_player", "media_stop", entity_id)
    except Exception as e:
        log.warning(f"[media.video] media_stop failed (device may not be playing): {e}")
    await asyncio.sleep(1)

    # Volume safeguard: unmute and set to safe level
    state = await ha_client.get_state(ctx.ha_url, ctx.ha_token, entity_id)
    if state:
        attrs = state.get("attributes", {})
        if attrs.get("is_volume_muted"):
            log.info("[media.video] Device is muted, unmuting")
            await ha_client.call_service(ctx.ha_url, ctx.ha_token, "media_player", "volume_mute", entity_id, {"is_volume_muted": False})
            await asyncio.sleep(1)
        vol = attrs.get("volume_level")
        if vol is not None and vol < 0.2:
            log.info(f"[media.video] Volume too low ({vol}), boosting to 20%")
            await ha_client.call_service(ctx.ha_url, ctx.ha_token, "media_player", "volume_set", entity_id, {"volume_level": 0.2})
            await asyncio.sleep(1)

    # Wait for download to complete (may already be done if HA setup took longer)
    media_id, title = await download_task
    if not media_id:
        return ExecutionResult(status="FAILURE", message=f"Failed to download video for '{query}'.", service="media_play")

    from services.config import EXECUTION_EXTERNAL_HOST
    if not EXECUTION_EXTERNAL_HOST:
        return ExecutionResult(status="FAILURE", message="EXECUTION_EXTERNAL_HOST is not configured.", service="media_play")
    stream_url = f"http://{EXECUTION_EXTERNAL_HOST}:8888/media/{media_id}"
    log.info(f"[media.video] Casting video to {entity_id}: {stream_url}")

    result = await ha_client.call_service(
        ctx.ha_url, ctx.ha_token, "media_player", "play_media", entity_id,
        {"media_content_id": stream_url, "media_content_type": "video/mp4"},
    )

    if result.get("ok"):
        return ExecutionResult(status="SUCCESS", message=f"Playing video '{title or query}' on {entity_id}.", service="media_play")

    return ExecutionResult(status="FAILURE", message=f"Failed to play video on {entity_id}.", service="media_play")

async def play_podcast(req: MediaPlayRequest, entity_id: str, ctx) -> ExecutionResult:
    """Play podcast via Music Assistant or URL."""
    from . import roku as roku_handler

    if req.query and req.query.startswith(("http://", "https://")):
        # Direct podcast URL
        is_roku = await roku_handler.is_roku_device(ctx.ha_url, ctx.ha_token, entity_id)
        if is_roku:
            ma_entity = await roku_handler.find_ma_player_sibling(ctx.ha_url, ctx.ha_token, entity_id)
            if ma_entity:
                result = await ha_client.call_service(
                    ctx.ha_url, ctx.ha_token, "music_assistant", "play_media", ma_entity,
                    {"media_id": req.query, "media_type": "track", "enqueue": "play"},
                )
                if result.get("ok"):
                    return ExecutionResult(status="SUCCESS", message=f"Playing podcast on {entity_id}.", service="media_play")
        result = await ha_client.call_service(
            ctx.ha_url, ctx.ha_token, "media_player", "play_media", entity_id,
            {"media_content_id": req.query, "media_content_type": "audio"},
        )
        if result.get("ok"):
            return ExecutionResult(status="SUCCESS", message=f"Playing podcast on {entity_id}.", service="media_play")

    # Try MASS search for podcast
    is_roku = await roku_handler.is_roku_device(ctx.ha_url, ctx.ha_token, entity_id)
    if is_roku:
        return await roku_handler.roku_play_music(
            ctx.ha_url, ctx.ha_token, entity_id, req.query or "", MASS_CONFIG_ENTRY_ID,
        )

    mass_entity = await resolve_mass_entity(ctx, entity_id)
    search_result = await ha_client.call_service(
        ctx.ha_url, ctx.ha_token, "music_assistant", "search", entity_id="",
        service_data={
            "config_entry_id": MASS_CONFIG_ENTRY_ID,
            "name": req.query,
            "media_type": ["track", "album"],
            "limit": 5,
        },
        return_response=True,
    )

    if search_result.get("ok") and search_result.get("service_response"):
        raw = search_result["service_response"]
        resp = raw.get("service_response", raw)
        for category in ["tracks", "albums"]:
            items = resp.get(category, [])
            if items:
                uri = items[0].get("uri")
                result = await ha_client.call_service(
                    ctx.ha_url, ctx.ha_token, "music_assistant", "play_media", mass_entity,
                    {"media_id": uri, "enqueue": "play"},
                )
                if result.get("ok"):
                    return ExecutionResult(status="SUCCESS", message=f"Playing podcast '{req.query}' on {entity_id}.", service="media_play")

    return ExecutionResult(status="FAILURE", message=f"Could not play podcast '{req.query}' on {entity_id}. Try providing a direct URL.", service="media_play")

async def play_audiobook(req: MediaPlayRequest, entity_id: str, ctx) -> ExecutionResult:
    """Play audiobook via Audiobookshelf."""
    from . import audiobookshelf as abs_handler
    
    if not req.query:
        return ExecutionResult(status="FAILURE", message="Audiobook title or query is required.", service="media_play")
    
    # Search ABS
    class _ABSRequest:
        def __init__(self, action, query=None, book_id=None, entity_id=None, limit=10):
            self.user_context = ctx
            self.action = action
            self.query = query
            self.book_id = book_id
            self.entity_id = entity_id
            self.limit = limit

    search_result = await abs_handler.handle_audiobookshelf(
        cast(AudiobookshelfRequest, _ABSRequest("search", query=req.query, limit=5, entity_id=entity_id))
    )
    
    if search_result.status == "SUCCESS" and search_result.detail:
        books = search_result.detail.get("books", [])
        if books:
            book = books[0]
            book_id = book.get("id")
            # Play the book
            play_result = await abs_handler.handle_audiobookshelf(
                cast(AudiobookshelfRequest, _ABSRequest("play", book_id=book_id, entity_id=entity_id))
            )
            return play_result
    
    return ExecutionResult(status="FAILURE", message=f"Could not find audiobook '{req.query}'.", service="media_play")

async def play_url(req: MediaPlayRequest, entity_id: str, ctx) -> ExecutionResult:
    """Play a direct URL on the device."""
    url = req.media_content_id or req.query
    content_type = req.media_content_type or "url"
    
    result = await ha_client.call_service(
        ctx.ha_url, ctx.ha_token, "media_player", "play_media", entity_id,
        {"media_content_id": url, "media_content_type": content_type},
    )
    
    if result.get("ok"):
        return ExecutionResult(status="SUCCESS", message=f"Playing URL on {entity_id}.", service="media_play")
    
    return ExecutionResult(status="FAILURE", message=f"Failed to play URL on {entity_id}.", service="media_play")

async def handle_media_play(req: MediaPlayRequest) -> ExecutionResult:
    """Unified media play handler supporting all content types and devices."""
    ctx = req.user_context
    ha_url = ctx.ha_url
    ha_token = ctx.ha_token
    assert ha_url is not None
    assert ha_token is not None
    
    # Detect media type BEFORE resolving entity (needed for context-aware entity resolution)
    media_type = detect_media_type(req.query or req.media_content_id or "", req.media_type)
    log.info(f"[media/play] Detected media type: {media_type}")
    
    # Resolve entity with media context
    try:
        entity_id = await resolve_entity(req, ha_url, ha_token, media_type)
    except ValueError as e:
        return ExecutionResult(status="FAILURE", message=str(e), service="media_play")
    
    log.info(f"[media/play] user={ctx.user} entity={entity_id} query='{req.query}' type='{req.media_type}'")
    
    # Set volume if requested
    if req.volume is not None:
        await ha_client.call_service(ha_url, ha_token, "media_player", "volume_set", entity_id, {"volume_level": req.volume})
    
    # Power on if needed
    state = await ha_client.get_state(ha_url, ha_token, entity_id)
    if state and state.get("state") in ("off", "unavailable", "standby", "idle"):
        log.info(f"[media/play] Device {entity_id} is {state.get('state')}, turning on...")
        await ha_client.call_service(ha_url, ha_token, "media_player", "turn_on", entity_id)
        await asyncio.sleep(2)
    
    # Route to appropriate handler
    if media_type == "video":
        return await play_video(req, entity_id, ctx)
    elif media_type == "podcast":
        return await play_podcast(req, entity_id, ctx)
    elif media_type == "audiobook":
        return await play_audiobook(req, entity_id, ctx)
    elif media_type == "url":
        return await play_url(req, entity_id, ctx)
    else:
        return await play_music(req, entity_id, ctx)

async def handle_media_transport(req) -> ExecutionResult:
    """Handle media transport commands with TV-specific handling for Android TV, WebOS, Samsung, and Roku."""
    from . import android_tv, webos, samsung, roku
    from ..announce_handlers import detect_tv_type

    ctx = req.user_context
    ha_url = ctx.ha_url
    ha_token = ctx.ha_token
    command = req.command.lower()
    full_entity_id = ha_client.sanitize_entity_id("media_player", req.entity_id)
    
    # Detect TV platform using centralized detection
    state = await ha_client.get_state(ha_url, ha_token, full_entity_id)
    attrs = state.get("attributes", {}) if state else {}
    tv_type = detect_tv_type(full_entity_id, state.get("state", "unknown") if state else "unknown", attrs)
    
    log.info(f"[media/transport] Platform: {tv_type} for {full_entity_id}")
    
    # Route to brand-specific handler
    if tv_type == "android_tv":
        return await android_tv.send_command(ha_url, ha_token, full_entity_id, command)
    elif tv_type == "webos":
        return await webos.send_command(ha_url, ha_token, full_entity_id, command)
    elif tv_type == "samsung":
        return await samsung.send_key(ha_url, ha_token, full_entity_id, command)
    elif tv_type == "roku":
        return await roku.roku_press(ha_url, ha_token, full_entity_id, command)
    
    # Standard media transport commands (all devices)
    button_map = {
        "pause": "media_pause", "resume": "media_play", "stop": "media_stop",
        "next": "media_next_track", "previous": "media_previous_track",
        "volume_up": "volume_up", "volume_down": "volume_down", "mute": "volume_mute"
    }
    
    service = button_map.get(command, command)
    domain = full_entity_id.split(".")[0]
    target_entity = full_entity_id
    
    # Remote button commands (uppercase = remote.send_command)
    remote_buttons = {"home", "back", "up", "down", "left", "right", "select", "enter", "ok", "info", "replay"}
    if command in remote_buttons:
        service_cmd = "send_command"
        data = {"command": command.upper()}
        domain = "remote"
        target_entity = full_entity_id.replace("media_player.", "remote.")
    else:
        service_cmd = service
        assert service_cmd is not None
        data = {}

    if command in ("volume_up", "volume_down") and req.volume_level is not None:
        service_cmd = "volume_set"
        data = {"volume_level": req.volume_level}

    result = await ha_client.call_service(
        ha_url, ha_token, domain, service_cmd, target_entity, data or None,
    )
    
    if result.get("ok"):
        return ExecutionResult(status="SUCCESS", message=f"Media command '{command}' executed on {full_entity_id}.", service="media_transport")
    return ExecutionResult(status="FAILURE", message=f"Media command failed: {result.get('error')}", service="media_transport", detail=result)

async def handle_tv_cast(req) -> ExecutionResult:
    """Handle TV cast requests (legacy compatibility)."""
    from ..schemas import MediaPlayRequest
    play_req = MediaPlayRequest(
        user_context=req.user_context,
        entity_id=req.media_player_entity_id,
        device_name=None,
        query=None,
        media_type=None,
        media_content_id=req.media_content_id,
        media_content_type=req.media_content_type,
        enqueue="replace",
        volume=None,
    )
    return await handle_media_play(play_req)

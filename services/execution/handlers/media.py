# services/execution/handlers/media.py
import logging
import asyncio
import re
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
try:
    import ha_client
    from schemas import MediaPlayRequest, ExecutionResult
    from config import MASS_CONFIG_ENTRY_ID
except ImportError:
    import ha_client
    from schemas import MediaPlayRequest, ExecutionResult
    from config import MASS_CONFIG_ENTRY_ID

log = logging.getLogger("execution.media")

# Media type detection patterns
VIDEO_PATTERNS = [r"youtube\.com", r"youtu\.be", r"vimeo\.com", r"rumble\.com", r"dailymotion\.com", r"tiktok\.com", r"twitch\.tv"]
AUDIOBOOK_PATTERNS = [r"audiobookshelf", r"abs", r"audiobook", r"book\s+"]
PODCAST_PATTERNS = [r"podcast", r"episode", r"show\s+", r"itunes\.apple\.com", r"open\.spotify\.com/show"]
MUSIC_PATTERNS = [r"spotify\.com/track", r"spotify\.com/album", r"soundcloud\.com", r"bandcamp\.com"]

def detect_media_type(query: str, media_type_hint: str = None) -> str:
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

async def resolve_entity(req: MediaPlayRequest, ha_url: str, ha_token: str) -> str:
    """Resolve entity_id from device_name if needed."""
    if req.entity_id:
        return ha_client.sanitize_entity_id("media_player", req.entity_id)
    
    if req.device_name:
        entity_id = await ha_client.resolve_entity_by_name(ha_url, ha_token, req.device_name, "media_player")
        if entity_id:
            return entity_id
    
    raise ValueError("entity_id or device_name is required")

async def resolve_mass_entity(ctx, original_entity: str) -> str:
    """Resolve a media_player entity to its Music Assistant variant."""
    all_states = await ha_client.get_states(ctx.ha_url, ctx.ha_token)
    if not all_states:
        return original_entity
    
    name_part = original_entity.replace("media_player.", "")
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
        
        if search in friendly and ("music assistant" in source or integration == "music_assistant"):
            if eid != original_entity:
                log.info(f"[media/play] Resolved MASS entity: {original_entity} -> {eid}")
                return eid
    
    return original_entity

async def play_music(req: MediaPlayRequest, entity_id: str, ctx) -> ExecutionResult:
    """Play music via Music Assistant or fallback."""
    mass_entity = await resolve_mass_entity(ctx, entity_id)
    
    if req.query:
        search_result = await ha_client.call_service(
            ctx.ha_url, ctx.ha_token, "music_assistant", "search", entity_id="",
            service_data={
                "config_entry_id": MASS_CONFIG_ENTRY_ID,
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
                result = await ha_client.call_service(
                    ctx.ha_url, ctx.ha_token, "music_assistant", "play_media", mass_entity,
                    {"media_id": uri, "enqueue": "play" if req.enqueue == "replace" else req.enqueue},
                )
                if result.get("ok"):
                    return ExecutionResult(status="SUCCESS", message=f"Playing '{req.query}' ({media_type_label}) on {entity_id}.", service="media_play")
        
        # Fallback: standard media_player.play_media
        result = await ha_client.call_service(
            ctx.ha_url, ctx.ha_token, "media_player", "play_media", entity_id,
            {"media": {"media_content_id": req.query, "media_content_type": "music"}, "enqueue": "play"},
        )
        if result.get("ok"):
            return ExecutionResult(status="SUCCESS", message=f"Playing '{req.query}' on {entity_id}.", service="media_play")
    
    return ExecutionResult(status="FAILURE", message=f"Could not play '{req.query}' on {entity_id}.", service="media_play")

async def play_video(req: MediaPlayRequest, entity_id: str, ctx) -> ExecutionResult:
    """Play video via yt-dlp stream."""
    from handlers import video as video_handler
    
    query = req.query or req.media_content_id or ""
    
    # Check if already a URL
    video_url = video_handler.extract_video_url(query)
    if not video_url:
        video_url = await video_handler.search_youtube(query)
        if not video_url:
            return ExecutionResult(status="FAILURE", message=f"Could not find video for '{query}'.", service="media_play")
    
    # Download and stream
    media_id, title = await video_handler.download_video(video_url)
    if not media_id:
        return ExecutionResult(status="FAILURE", message=f"Failed to download video for '{query}'.", service="media_play")
    
    # Get public host for streaming
    from config import EXECUTION_EXTERNAL_HOST
    public_host = EXECUTION_EXTERNAL_HOST or "192.168.2.205"
    stream_url = f"http://{public_host}:8003/media/{media_id}"
    
    # Play on device
    result = await ha_client.call_service(
        ctx.ha_url, ctx.ha_token, "media_player", "play_media", entity_id,
        {"media_content_id": stream_url, "media_content_type": "video/mp4"},
    )
    
    if result.get("ok"):
        return ExecutionResult(status="SUCCESS", message=f"Playing video '{title or query}' on {entity_id}.", service="media_play")
    
    return ExecutionResult(status="FAILURE", message=f"Failed to play video on {entity_id}.", service="media_play")

async def play_podcast(req: MediaPlayRequest, entity_id: str, ctx) -> ExecutionResult:
    """Play podcast via Music Assistant or URL."""
    if req.query and req.query.startswith(("http://", "https://")):
        # Direct podcast URL
        result = await ha_client.call_service(
            ctx.ha_url, ctx.ha_token, "media_player", "play_media", entity_id,
            {"media_content_id": req.query, "media_content_type": "audio"},
        )
        if result.get("ok"):
            return ExecutionResult(status="SUCCESS", message=f"Playing podcast on {entity_id}.", service="media_play")
    
    # Try MASS search for podcast
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
    from handlers import audiobookshelf as abs_handler
    
    if not req.query:
        return ExecutionResult(status="FAILURE", message="Audiobook title or query is required.", service="media_play")
    
    # Search ABS
    search_result = await abs_handler.handle_audiobookshelf(
        type("ABSRequest", (), {"user_context": ctx, "action": "search", "query": req.query, "limit": 5, "entity_id": entity_id})()
    )
    
    if search_result.status == "SUCCESS" and search_result.detail:
        books = search_result.detail.get("results", [])
        if books:
            book = books[0]
            book_id = book.get("id")
            # Play the book
            play_result = await abs_handler.handle_audiobookshelf(
                type("ABSRequest", (), {"user_context": ctx, "action": "play", "book_id": book_id, "entity_id": entity_id})()
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
    
    # Resolve entity
    try:
        entity_id = await resolve_entity(req, ha_url, ha_token)
    except ValueError as e:
        return ExecutionResult(status="FAILURE", message=str(e), service="media_play")
    
    log.info(f"[media/play] user={ctx.user} entity={entity_id} query='{req.query}' type='{req.media_type}'")
    
    # Detect media type
    media_type = detect_media_type(req.query or req.media_content_id or "", req.media_type)
    log.info(f"[media/play] Detected media type: {media_type}")
    
    # Set volume if requested
    if req.volume is not None:
        await ha_client.call_service(ha_url, ha_token, "media_player", "volume_set", entity_id, {"volume_level": req.volume})
    
    # Power on if needed
    state = await ha_client.get_state(ha_url, ha_token, entity_id)
    if state and state.get("state") in ("off", "unavailable", "standby"):
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

async def is_android_tv(ha_url: str, ha_token: str, entity_id: str) -> bool:
    """Check if a media_player entity is an Android TV device."""
    state = await ha_client.get_state(ha_url, ha_token, entity_id)
    if not state:
        return False
    attrs = state.get("attributes", {})
    app_id = (attrs.get("app_id") or "").lower()
    # Android TV indicators: Android package names or known Android TV apps
    android_indicators = ("com.google.android.", "com.google.tv.", "com.android.", 
                          "mediashell", "backdrop", "tvlauncher", "android.tv")
    return any(ind in app_id for ind in android_indicators)

async def is_webos_tv(ha_url: str, ha_token: str, entity_id: str) -> bool:
    """Check if a media_player entity is an LG WebOS TV."""
    state = await ha_client.get_state(ha_url, ha_token, entity_id)
    if not state:
        return False
    attrs = state.get("attributes", {})
    app_id = (attrs.get("app_id") or "").lower()
    entity_id_lower = entity_id.lower()
    # WebOS indicators: webostv entity prefix or com.webos app_id
    webos_indicators = ("com.webos.", "webos.tv", "lg.webos")
    if any(ind in app_id for ind in webos_indicators):
        return True
    # Check entity_id for webostv pattern
    if "webostv" in entity_id_lower:
        return True
    return False

async def is_samsung_tv(ha_url: str, ha_token: str, entity_id: str) -> bool:
    """Check if a media_player entity is a Samsung Tizen TV."""
    state = await ha_client.get_state(ha_url, ha_token, entity_id)
    if not state:
        return False
    attrs = state.get("attributes", {})
    app_id = (attrs.get("app_id") or "").lower()
    entity_id_lower = entity_id.lower()
    # Samsung Tizen indicators: org.tizen app_id or samsungtv entity prefix
    samsung_indicators = ("org.tizen.", "samsung.tv", "tizen.tv")
    if any(ind in app_id for ind in samsung_indicators):
        return True
    # Check entity_id for samsungtv pattern
    if "samsungtv" in entity_id_lower:
        return True
    return False

async def handle_webos_command(ha_url: str, ha_token: str, entity_id: str, command: str) -> ExecutionResult:
    """Send a command to an LG WebOS TV via webostv integration."""
    # WebOS command mapping
    webos_commands = {
        "home": "HOME", "back": "BACK", "enter": "ENTER", "pause": "PAUSE",
        "play": "PLAY", "stop": "STOP", "fast_forward": "FASTFORWARD",
        "rewind": "REWIND", "channel_up": "CHANNELUP", "channel_down": "CHANNELDOWN",
        "volume_up": "VOLUMEUP", "volume_down": "VOLUMEDOWN", "mute": "MUTE",
        "red": "RED", "green": "GREEN", "yellow": "YELLOW", "blue": "BLUE",
        "power_off": "POWER", "power_on": "POWER",
    }
    
    webos_cmd = webos_commands.get(command.lower())
    if not webos_cmd:
        return ExecutionResult(status="FAILURE", message=f"Unsupported WebOS command: {command}", service="media_transport")
    
    log.info(f"[media/webos] Sending command '{webos_cmd}' to {entity_id}")
    result = await ha_client.call_service(
        ha_url, ha_token, "webostv", "command", entity_id,
        {"command": webos_cmd}
    )
    
    if result.get("ok"):
        return ExecutionResult(status="SUCCESS", message=f"Sent '{command}' to {entity_id} (WebOS).", service="media_transport")
    return ExecutionResult(status="FAILURE", message=f"Failed to send '{command}' to WebOS TV: {result.get('error')}", service="media_transport", detail=result)

async def handle_samsung_command(ha_url: str, ha_token: str, entity_id: str, command: str) -> ExecutionResult:
    """Send a command to a Samsung Tizen TV via samsungtv integration."""
    # Samsung key mapping
    samsung_keys = {
        "home": "KEY_HOME", "back": "KEY_RETURN", "enter": "KEY_ENTER",
        "pause": "KEY_PAUSE", "play": "KEY_PLAY", "stop": "KEY_STOP",
        "fast_forward": "KEY_FF", "rewind": "KEY_REWIND",
        "channel_up": "KEY_CHUP", "channel_down": "KEY_CHDOWN",
        "volume_up": "KEY_VOLUP", "volume_down": "KEY_VOLDOWN", "mute": "KEY_MUTE",
        "power_off": "KEY_POWER", "power_on": "KEY_POWER",
        "up": "KEY_UP", "down": "KEY_DOWN", "left": "KEY_LEFT", "right": "KEY_RIGHT",
        "info": "KEY_INFO", "menu": "KEY_MENU", "tools": "KEY_TOOLS",
        "exit": "KEY_EXIT", "source": "KEY_SOURCE",
    }
    
    samsung_key = samsung_keys.get(command.lower())
    if not samsung_key:
        return ExecutionResult(status="FAILURE", message=f"Unsupported Samsung command: {command}", service="media_transport")
    
    log.info(f"[media/samsung] Sending key '{samsung_key}' to {entity_id}")
    result = await ha_client.call_service(
        ha_url, ha_token, "samsungtv", "send_key", entity_id,
        {"key": samsung_key}
    )
    
    if result.get("ok"):
        return ExecutionResult(status="SUCCESS", message=f"Sent '{command}' to {entity_id} (Samsung).", service="media_transport")
    return ExecutionResult(status="FAILURE", message=f"Failed to send '{command}' to Samsung TV: {result.get('error')}", service="media_transport", detail=result)

async def handle_media_transport(req) -> ExecutionResult:
    """Handle media transport commands with TV-specific handling for Android TV, WebOS, and Samsung."""
    ctx = req.user_context
    ha_url = ctx.ha_url
    ha_token = ctx.ha_token
    command = req.command.lower()
    full_entity_id = ha_client.sanitize_entity_id("media_player", req.entity_id)
    
    # Detect TV type
    tv_type = "standard"
    if await is_android_tv(ha_url, ha_token, full_entity_id):
        tv_type = "android"
    elif await is_webos_tv(ha_url, ha_token, full_entity_id):
        tv_type = "webos"
    elif await is_samsung_tv(ha_url, ha_token, full_entity_id):
        tv_type = "samsung"
    
    log.info(f"[media/transport] Device type: {tv_type} for {full_entity_id}")
    
    # Android TV-specific command handling
    if tv_type == "android":
        if command in ("stop", "home", "close"):
            log.info(f"[media/transport] Android TV HOME command for {full_entity_id}")
            result = await ha_client.call_service(
                ha_url, ha_token, "androidtv_remote", "send_command", full_entity_id,
                {"command": "home"}
            )
            if result.get("ok"):
                return ExecutionResult(status="SUCCESS", message=f"Returned to home screen on {full_entity_id}.", service="media_transport")
            return ExecutionResult(status="FAILURE", message=f"Failed to return home: {result.get('error')}", service="media_transport", detail=result)
        
        if command in ("power_off", "turn_off"):
            log.info(f"[media/transport] Android TV POWER OFF for {full_entity_id}")
            result = await ha_client.call_service(
                ha_url, ha_token, "androidtv_remote", "send_command", full_entity_id,
                {"command": "sleep"}
            )
            if result.get("ok"):
                return ExecutionResult(status="SUCCESS", message=f"Powered off {full_entity_id}.", service="media_transport")
            return ExecutionResult(status="FAILURE", message=f"Failed to power off: {result.get('error')}", service="media_transport", detail=result)
        
        if command == "back":
            log.info(f"[media/transport] Android TV BACK command for {full_entity_id}")
            result = await ha_client.call_service(
                ha_url, ha_token, "androidtv_remote", "send_command", full_entity_id,
                {"command": "back"}
            )
            if result.get("ok"):
                return ExecutionResult(status="SUCCESS", message=f"Sent back command to {full_entity_id}.", service="media_transport")
            return ExecutionResult(status="FAILURE", message=f"Failed to send back: {result.get('error')}", service="media_transport", detail=result)
    
    # WebOS-specific command handling
    elif tv_type == "webos":
        webos_result = await handle_webos_command(ha_url, ha_token, full_entity_id, command)
        if webos_result.status == "SUCCESS":
            return webos_result
        # Fallback to standard commands if WebOS command not supported
        log.info(f"[media/transport] WebOS command failed, falling back to standard for {command}")
    
    # Samsung-specific command handling
    elif tv_type == "samsung":
        samsung_result = await handle_samsung_command(ha_url, ha_token, full_entity_id, command)
        if samsung_result.status == "SUCCESS":
            return samsung_result
        # Fallback to standard commands if Samsung command not supported
        log.info(f"[media/transport] Samsung command failed, falling back to standard for {command}")
    
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
    from schemas import MediaPlayRequest
    play_req = MediaPlayRequest(
        user_context=req.user_context,
        entity_id=req.media_player_entity_id,
        media_content_id=req.media_content_id,
        media_content_type=req.media_content_type
    )
    return await handle_media_play(play_req)

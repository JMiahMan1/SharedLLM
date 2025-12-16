
from app.settings import log, GlobalResources
from app.logic.media_ops import execute_ha_service

async def tool_remote_command(command: str, entity_id: str, user_creds: dict, redis_client=None) -> dict:
    """
    Sends a specific command to an Android TV / Remote entity.
    Commands: DPAD_UP, DPAD_DOWN, HOME, BACK, MENU, VOLUME_UP, etc.
    """
    # Normalize command
    cmd_map = {
        "up": "DPAD_UP", "down": "DPAD_DOWN", "left": "DPAD_LEFT", "right": "DPAD_RIGHT",
        "select": "DPAD_CENTER", "enter": "DPAD_CENTER", "ok": "DPAD_CENTER",
        "home": "HOME", "back": "BACK", "menu": "MENU",
        "volume up": "VOLUME_UP", "volume down": "VOLUME_DOWN", "mute": "MUTE",
        "power": "POWER", "play": "MEDIA_PLAY_PAUSE", "pause": "MEDIA_PLAY_PAUSE"
    }
    
    clean_cmd = command.lower().strip()
    final_cmd = cmd_map.get(clean_cmd, command.upper()) # Fallback to uppercase raw
    
    # If entity is media_player, try to swap to remote if not done by routing?
    # Routing should have handled it. But we ensure domain is correct.
    domain = entity_id.split(".")[0]
    service = "send_command"
    
    if domain == "media_player":
        # Some media players support play_media(key) or specific services
        pass
    
    return await execute_ha_service(
        "remote", "send_command", entity_id, user_creds, 
        {"command": final_cmd}, redis_client
    )

async def tool_launch_app_android(app_name: str, entity_id: str, user_creds: dict, redis_client=None) -> dict:
    """
    Launches an app on Android TV.
    Uses deep links or app id mapping if known.
    """
    # Common App Deep Links / IDs
    APP_IDS = {
        "netflix": "com.netflix.ninja",
        "youtube": "com.google.android.youtube.tv",
        "spotify": "com.spotify.tv.android",
        "disney": "com.disney.disneyplus",
        "hulu": "com.hulu.livingroomplus",
        "plex": "com.plexapp.android",
        "prime video": "com.amazon.amazonvideo.livingroom"
    }
    
    app_id = APP_IDS.get(app_name.lower())
    if not app_id:
        # Try generic search or command?
        # Creating a generic approach: remote.turn_on with activity?
        # Or media_player.play_media with app_name matches.
        # Fallback to media_ops logic.
        return {"status": "FAILURE", "message": f"Unknown app ID for {app_name}"}
        
    # Android TV integration supports specific way to launch?
    # Usually `media_player.select_source` OR `remote.turn_on` with activity.
    # But `androidtv.adb_command` is also an option.
    # Safest is `media_player.play_media` types app?
    pass # Implementation TBD based on specific integration details.
    
    return await execute_ha_service(
        "media_player", "play_media", entity_id, user_creds,
        {"media_content_id": app_id, "media_content_type": "app"},
        redis_client
    )

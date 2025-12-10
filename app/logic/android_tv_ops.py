
import logging
import urllib.parse
from settings import GlobalResources, log
from logic.media_ops import execute_ha_service
from logic.web_search import tool_web_search
import re

# App ID Mapping
APP_IDS = {
    "youtube": "com.google.android.youtube.tv",
    "youtube tv": "com.google.android.youtube.tvunplugged",
    "netflix": "com.netflix.ninja",
    "spotify": "com.spotify.tv.android",
    "disney+": "com.disney.disneyplus",
    "disney plus": "com.disney.disneyplus",
    "hulu": "com.hulu.livingroomplus",
    "plex": "com.plexapp.android",
    "prime video": "com.amazon.amazonvideo.livingroom",
    "twitch": "tv.twitch.android.app"
}

async def launch_app(entity_id: str, app_name: str, user_creds: dict, redis_client=None) -> dict:
    """Launches an app on Android TV by name or ID."""
    app_id = APP_IDS.get(app_name.lower())
    
    # If not a known alias, assume it might be the ID itself or try best effort
    if not app_id:
        app_id = app_name 
    
    log.info(f"[Android TV] Launching app '{app_name}' ({app_id}) on {entity_id}")
    
    # Use media_player.play_media with content_type='app' if supported, 
    # OR androidtv.adb_command.
    # The standard HA Android TV integration uses play_media(media_content_id=app_id, media_content_type='app')
    
    # Attempt 1: Standard play_media (works for some integrations)
    result = await execute_ha_service(
        "media_player", "play_media", entity_id, user_creds,
        {"media_content_id": app_id, "media_content_type": "app"},
        redis_client
    )
    
    if result.get("status") == "SUCCESS":
        return result
        
    log.info(f"[Android TV] play_media failed/unsupported. Retrying with ADB 'monkey' command for {app_id}")
    
    # Attempt 2: ADB Monkey Command (Launch by package)
    adb_cmd = f"monkey -p {app_id} -c android.intent.category.LAUNCHER 1"
    return await execute_ha_service(
        "androidtv", "adb_command", entity_id, user_creds,
        {"command": adb_cmd},
        redis_client
    )

async def play_video(entity_id: str, video_url: str, user_creds: dict, redis_client=None) -> dict:
    """Plays a specific video URL (Deep Linking)."""
    log.info(f"[Android TV] Playing video '{video_url}' on {entity_id}")
    
    # Attempt 1: Standard play_media with type 'url'
    result = await execute_ha_service(
        "media_player", "play_media", entity_id, user_creds,
        {"media_content_id": video_url, "media_content_type": "url"},
        redis_client
    )

    if result.get("status") == "SUCCESS":
        return result

    log.info(f"[Android TV] play_media failed. Retrying with ADB deep link...")

    # Attempt 2: ADB AM Start (YouTube specific optimization)
    # This is highly specific to YouTube but that's the primary use case requested.
    if "youtube.com" in video_url or "youtu.be" in video_url:
         adb_cmd = f"am start -a android.intent.action.VIEW -d \"{video_url}\" -n com.google.android.youtube.tv/com.google.android.apps.youtube.tv.activity.ShellActivity"
         return await execute_ha_service(
            "androidtv", "adb_command", entity_id, user_creds,
            {"command": adb_cmd},
            redis_client
        )
    
    return result # Return original failure if not YouTube

async def search_and_play(entity_id: str, query: str, user_creds: dict, redis_client=None) -> dict:
    """
    Searches for a video URL using web_search and then plays it.
    Targeting YouTube keys primarily.
    """
    log.info(f"[Android TV] Search & Play: '{query}' on {entity_id}")
    
    # 1. Search for a YouTube URL
    search_q = f"site:youtube.com/watch {query}"
    search_result = await tool_web_search(search_q)
    
    # 2. Extract URL
    # Look for https://www.youtube.com/watch?v=...
    match = re.search(r'(https?://www\.youtube\.com/watch\?v=[\w-]+)', search_result)
    
    if match:
        url = match.group(1)
        log.info(f"[Android TV] Found URL: {url}")
        return await play_video(entity_id, url, user_creds, redis_client)
    
    return {"status": "FAILURE", "message": f"No video URL found for '{query}'"}

async def control_device(entity_id: str, command: str, user_creds: dict, redis_client=None) -> dict:
    """
    Sends remote commands (power, home, back, etc.)
    """
    # Map friendly commands to ADB/Remote integration commands
    cmd_map = {
        "home": "HOME",
        "back": "BACK",
        "menu": "MENU",
        "up": "DPAD_UP",
        "down": "DPAD_DOWN",
        "left": "DPAD_LEFT",
        "right": "DPAD_RIGHT",
        "select": "DPAD_CENTER",
        "enter": "DPAD_CENTER",
        "play": "MEDIA_PLAY",
        "pause": "MEDIA_PAUSE",
        "stop": "MEDIA_STOP",
        "next": "MEDIA_NEXT",
        "previous": "MEDIA_PREVIOUS",
        "power": "POWER",
        "sleep": "SLEEP",
        "wake": "WAKEUP",
        "turon_on": "turn_on", # Service, not key
        "turn_off": "turn_off" # Service, not key
    }
    
    clean_cmd = command.lower().strip()
    
    # Handle Power Explicitly
    if clean_cmd in ["turn_on", "turn_off", "toggle"]:
         return await execute_ha_service(
             "media_player", clean_cmd, entity_id, user_creds, {}, redis_client
         )
         
    final_cmd = cmd_map.get(clean_cmd)
    if not final_cmd:
        return {"status": "FAILURE", "message": f"Unknown command: {command}"}
    
    # Determine if we use media_player or remote domain
    # Usually androidTV integration entities are media_player, but `androidtv.learn_sendevent` etc exist.
    # But `remote.send_command` is the standard for the `remote` entity associated with the android tv.
    # HOWEVER, the 'androidtv' media_player integration *also* accepts ADB commands via `androidtv.adb_command`.
    
    # If the user passed a media_player entity, we use `androidtv.adb_command` for keys?
    # Actually, standard media_player services cover play/pause/stop/next/prev.
    
    std_transport = ["play", "pause", "stop", "next", "previous"]
    if clean_cmd in std_transport:
        service_map = {
            "play": "media_play", "pause": "media_pause", "stop": "media_stop",
            "next": "media_next_track", "previous": "media_previous_track"
        }
        return await execute_ha_service(
            "media_player", service_map[clean_cmd], entity_id, user_creds, {}, redis_client
        )
        
    # For Keys (Home, Back, DPAD), we need `androidtv.adb_command` with "input keyevent {KEY}" OR `remote.send_command` if we have the remote entity.
    # Assumption: We are operating on the `media_player` entity ID.
    
    # Use ADB Command service
    adb_cmd = final_cmd # The integration often accepts raw keys like 'HOME', 'BACK' in command
    
    return await execute_ha_service(
        "androidtv", "adb_command", entity_id, user_creds,
        {"command": adb_cmd},
        redis_client
    )

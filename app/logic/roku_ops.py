import logging
from app.settings import log
from app.logic.media_ops import execute_ha_service

async def launch_app(entity_id: str, app_name_or_id: str, user_creds: dict, redis_client=None) -> dict:
    """
    Launches an app on Roku by name or app ID.
    Uses media_player.select_source.
    """
    log.info(f"[Roku] Launching app '{app_name_or_id}' on {entity_id}")
    
    return await execute_ha_service(
        "media_player", "select_source", entity_id, user_creds,
        {"source": app_name_or_id},
        redis_client
    )

async def play_channel(entity_id: str, channel: str, user_creds: dict, redis_client=None) -> dict:
    """
    Tunes to a TV channel on Roku TV (requires OTA antenna).
    Channel format: "5.1" for subchannel or "5" for main channel.
    """
    log.info(f"[Roku] Tuning to channel '{channel}' on {entity_id}")
    
    return await execute_ha_service(
        "media_player", "play_media", entity_id, user_creds,
        {"media_content_id": channel, "media_content_type": "channel"},
        redis_client
    )

async def play_media_url(entity_id: str, url: str, user_creds: dict, redis_client=None, 
                        format: str = "mp4", name: str = None, thumbnail: str = None) -> dict:
    """
    Plays a direct media URL on Roku.
    Intelligently handles YouTube and Rumble URLs by converting them to app deep-links.
    Otherwise uses PlayOnRoku for direct video files.
    """
    import re
    
    # 1. YouTube Deep Linking
    # Matches: youtube.com/watch?v=ID, youtu.be/ID, youtube.com/shorts/ID
    yt_match = re.search(r"(?:v=|/)([0-9A-Za-z_-]{11}).*", url)
    if "youtube" in url or "youtu.be" in url:
        if yt_match:
            video_id = yt_match.group(1)
            log.info(f"[Roku] Detected YouTube URL. Deep-linking to video {video_id}")
            # YouTube App ID: 837
            return await deep_link(entity_id, "837", video_id, "live", user_creds, redis_client)
            
    # 2. Rumble Deep Linking (Basic support)
    # Rumble deep linking usually requires specific content IDs which are hard to extract from URL
    # But checking if we can launch the app at least.
    # For now, we'll try to let standard URL playback handle it or just launch app if generic.
    if "rumble.com" in url:
        # Rumble App ID: 233120 (approx, may vary by region or app version, using search fallback if needed)
        # Deep linking is complex, so we might just launch the app for now.
        pass

    # 3. Standard Direct Media (PlayOnRoku)
    log.info(f"[Roku] Playing URL on {entity_id}: {url}")
    
    data = {
        "media_content_id": url,
        "media_content_type": "url",
        "extra": {"format": format}
    }
    
    if name:
        data["extra"]["name"] = name
    if thumbnail:
        data["extra"]["thumbnail"] = thumbnail
    
    return await execute_ha_service(
        "media_player", "play_media", entity_id, user_creds,
        data,
        redis_client
    )

async def deep_link(entity_id: str, app_id: str, content_id: str, media_type: str, 
                   user_creds: dict, redis_client=None) -> dict:
    """
    Deep-links to specific content within an app.
    
    Args:
        app_id: The Roku app ID (e.g., "12" for Netflix)
        content_id: Content-specific ID
        media_type: Type of content (movie, episode, season, series, shortFormVideo, special, live)
    """
    log.info(f"[Roku] Deep-linking to {media_type} in app {app_id} on {entity_id}")
    
    return await execute_ha_service(
        "media_player", "play_media", entity_id, user_creds,
        {
            "media_content_id": app_id,
            "media_content_type": "app",
            "extra": {
                "content_id": content_id,
                "media_type": media_type
            }
        },
        redis_client
    )

async def send_button(entity_id: str, button: str, user_creds: dict, redis_client=None) -> dict:
    """
    Sends a remote button command to Roku.
    Uses remote.send_command service.
    
    Available buttons:
    - Navigation: HOME, BACK, UP, DOWN, LEFT, RIGHT, SELECT
    - Media: PLAY, PAUSE, REW, FWD
    - Volume: VOLUME_UP, VOLUME_DOWN, VOLUME_MUTE
    - Channel: CHANNEL_UP, CHANNEL_DOWN
    - Additional: INFO, INSTANT_REPLAY, POWER_OFF
    """
    # Map common names to Roku button names
    button_map = {
        "home": "HOME",
        "back": "BACK",
        "up": "UP",
        "down": "DOWN",
        "left": "LEFT",
        "right": "RIGHT",
        "select": "SELECT",
        "enter": "SELECT",
        "ok": "SELECT",
        "play": "PLAY",
        "pause": "PAUSE",
        "rewind": "REW",
        "fast forward": "FWD",
        "fwd": "FWD",
        "rew": "REW",
        "volume up": "VOLUME_UP",
        "volume down": "VOLUME_DOWN",
        "mute": "VOLUME_MUTE",
        "channel up": "CHANNEL_UP",
        "channel down": "CHANNEL_DOWN",
        "info": "INFO",
        "replay": "INSTANT_REPLAY",
        "power off": "POWER_OFF",
        "power": "POWER_OFF"
    }
    
    clean_button = button.lower().strip()
    roku_button = button_map.get(clean_button, button.upper())
    
    log.info(f"[Roku] Sending button '{roku_button}' to {entity_id}")
    
    # Roku uses remote entity, need to convert media_player entity to remote
    # media_player.roku -> remote.roku
    remote_entity = entity_id.replace("media_player.", "remote.")
    
    return await execute_ha_service(
        "remote", "send_command", remote_entity, user_creds,
        {"command": roku_button},
        redis_client
    )

async def search(entity_id: str, keyword: str, user_creds: dict, redis_client=None) -> dict:
    """
    Opens the Roku search screen with the specified keyword.
    Uses roku.search service.
    """
    log.info(f"[Roku] Searching for '{keyword}' on {entity_id}")
    
    return await execute_ha_service(
        "roku", "search", entity_id, user_creds,
        {"keyword": keyword},
        redis_client
    )

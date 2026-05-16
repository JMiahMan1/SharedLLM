"""TV-specific announcement handlers for different smart TV platforms."""
import asyncio
import logging
from typing import Dict, Any, Optional

log = logging.getLogger("execution.announce")

# Supported feature flags (from HA media_player)
SUPPORT_SELECT_SOURCE = 1
SUPPORT_PLAY_MEDIA = 16384
SUPPORT_TURN_ON = 128
SUPPORT_TURN_OFF = 256
SUPPORT_VOLUME_SET = 4
SUPPORT_BROWSE_MEDIA = 131072

def detect_tv_type(entity_id: str, state: str, attributes: dict) -> str:
    """Detect TV platform type using multiple context clues.
    
    Uses entity_id patterns, app_id, device_class, supported_features,
    source_list content, and state to determine the platform.
    """
    eid = entity_id.lower()
    app_id = (attributes.get("app_id") or "").lower()
    device_class = (attributes.get("device_class") or "").lower()
    supported_features = attributes.get("supported_features", 0)
    source_list = [s.lower() for s in (attributes.get("source_list") or [])]
    
    # 1. Cast devices: app_id matches Cast receiver IDs, or entity contains 'chrome'/'cast'
    cast_app_ids = {"cc1ad845", "9ac10326", "4475d545"}  # Default Media Receiver, YouTube, etc.
    if "chrome" in eid or "_cast" in eid or app_id in cast_app_ids:
        return "cast"
    
    # 2. Roku: entity contains 'roku', or source_list has Roku-specific apps
    roku_apps = {"netflix", "hulu", "roku media player", "the roku channel", "home", "live tv"}
    has_roku_sources = bool(roku_apps & set(source_list))
    if "roku" in eid or has_roku_sources:
        return "roku"
    
    # 3. Android TV: app_id is Android package, or entity contains 'android'
    android_packages = {"com.google.android.", "com.google.tv.", "com.android."}
    is_android_app = any(app_id.startswith(pkg) for pkg in android_packages)
    android_indicators = ["mediashell", "backdrop", "tvlauncher", "android.tv"]
    is_android_indicator = any(ind in app_id for ind in android_indicators)
    if is_android_app or is_android_indicator or ("android" in eid and device_class == "tv"):
        return "android_tv"
    
    # 4. webOS (LG): entity contains 'lg' or 'webos'
    if "lg_" in eid or "webos" in eid or "web_os" in eid:
        return "webos"
    
    # 5. Samsung Tizen: entity contains 'samsung' or 'tizen'
    if "samsung" in eid or "tizen" in eid:
        return "samsung"
    
    # 6. Sony Bravia: entity contains 'bravia' or 'sony'
    if "bravia" in eid or "sony" in eid:
        return "bravia"
    
    # 7. Music Assistant: app_id is 'music_assistant'
    if app_id == "music_assistant":
        return "music_assistant"
    
    # 8. Generic TV: device_class=tv with source_list containing TV inputs
    tv_inputs = {"live tv", "tv", "hdmi", "hdmi 1", "hdmi 2", "av"}
    has_tv_inputs = bool(tv_inputs & set(source_list))
    if device_class == "tv" or has_tv_inputs:
        return "generic_tv"
    
    # 9. Speaker: device_class=speaker
    if device_class == "speaker":
        return "speaker"
    
    # 10. Fallback
    return "generic"

async def announce_cast(ha_url: str, ha_token: str, entity_id: str, media_url: str, volume: float) -> Dict[str, Any]:
    """Cast devices support direct URL playback."""
    from ha_client import call_service
    log.info(f"[announce.cast] Playing URL on {entity_id}: {media_url[:60]}")
    return await call_service(ha_url, ha_token, "media_player", "play_media", entity_id, {
        "media_content_id": media_url,
        "media_content_type": "url"
    })

async def announce_roku(ha_url: str, ha_token: str, entity_id: str, media_url: str, volume: float) -> Dict[str, Any]:
    """Roku uses Media Assistant app (ID 782875)."""
    from ha_client import call_service
    log.info(f"[announce.roku] Launching Media Assistant on {entity_id}")
    result = await call_service(ha_url, ha_token, "media_player", "play_media", entity_id, {
        "media_content_id": "782875",
        "media_content_type": "app",
        "extra": {"content_id": media_url, "media_type": "audio/wav"}
    })
    if result.get("ok"):
        await asyncio.sleep(2.0)
    return result

async def announce_webos(ha_url: str, ha_token: str, entity_id: str, media_url: str, volume: float) -> Dict[str, Any]:
    """webOS TV: try webostv.notify, then media_player as fallback."""
    from ha_client import call_service
    log.info(f"[announce.webos] Trying webostv.notify on {entity_id}")
    
    result = await call_service(ha_url, ha_token, "webostv", "notify", entity_id, {
        "message": media_url,
        "icon": "https://ha.sumemail.com/local/kokoro-icon.png"
    })
    
    if result.get("ok"):
        return result
    
    log.info(f"[announce.webos] Falling back to media_player.play_media")
    return await call_service(ha_url, ha_token, "media_player", "play_media", entity_id, {
        "media_content_id": media_url,
        "media_content_type": "url"
    })

async def announce_android_tv(ha_url: str, ha_token: str, entity_id: str, media_url: str, volume: float) -> Dict[str, Any]:
    """Android TV: try media_player, then ADB commands."""
    from ha_client import call_service
    log.info(f"[announce.android_tv] Trying play_media on {entity_id}")
    
    result = await call_service(ha_url, ha_token, "media_player", "play_media", entity_id, {
        "media_content_id": media_url,
        "media_content_type": "url"
    })
    
    if result.get("ok"):
        return result
    
    # Try ADB to launch VLC or default media player
    log.info(f"[announce.android_tv] Trying ADB command to launch media")
    await call_service(ha_url, ha_token, "androidtv", "adb_command", entity_id, {"command": "HOME"})
    await asyncio.sleep(1)
    
    return await call_service(ha_url, ha_token, "androidtv", "adb_command", entity_id, {
        "command": f"am start -d '{media_url}' -a android.intent.action.VIEW"
    })

async def announce_samsung(ha_url: str, ha_token: str, entity_id: str, media_url: str, volume: float) -> Dict[str, Any]:
    """Samsung Tizen TV."""
    from ha_client import call_service
    log.info(f"[announce.samsung] Trying play_media on {entity_id}")
    
    result = await call_service(ha_url, ha_token, "media_player", "play_media", entity_id, {
        "media_content_id": media_url,
        "media_content_type": "url"
    })
    
    if result.get("ok"):
        return result
    
    return await call_service(ha_url, ha_token, "samsungtv", "send_command", entity_id, {
        "method": "ms.remote.control",
        "params": {"Cmd": "Play"}
    })

async def announce_bravia(ha_url: str, ha_token: str, entity_id: str, media_url: str, volume: float) -> Dict[str, Any]:
    """Sony Bravia TV."""
    from ha_client import call_service
    log.info(f"[announce.bravia] Trying play_media on {entity_id}")
    return await call_service(ha_url, ha_token, "media_player", "play_media", entity_id, {
        "media_content_id": media_url,
        "media_content_type": "url"
    })

async def announce_music_assistant(ha_url: str, ha_token: str, entity_id: str, media_url: str, volume: float) -> Dict[str, Any]:
    """Music Assistant integration."""
    from ha_client import call_service
    log.info(f"[announce.mass] Using mass.play_announcement on {entity_id}")
    return await call_service(ha_url, ha_token, "mass", "play_announcement", entity_id, {
        "url": media_url,
        "use_pre_announcement": False
    })

async def announce_generic_tv(ha_url: str, ha_token: str, entity_id: str, media_url: str, volume: float) -> Dict[str, Any]:
    """Generic TV fallback."""
    from ha_client import call_service
    log.info(f"[announce.generic_tv] Trying play_media on {entity_id}")
    return await call_service(ha_url, ha_token, "media_player", "play_media", entity_id, {
        "media_content_id": media_url,
        "media_content_type": "url"
    })

async def announce_speaker(ha_url: str, ha_token: str, entity_id: str, media_url: str, volume: float) -> Dict[str, Any]:
    """Generic speaker."""
    from ha_client import call_service
    log.info(f"[announce.speaker] Trying play_media on {entity_id}")
    return await call_service(ha_url, ha_token, "media_player", "play_media", entity_id, {
        "media_content_id": media_url,
        "media_content_type": "url"
    })

async def announce_generic(ha_url: str, ha_token: str, entity_id: str, media_url: str, volume: float) -> Dict[str, Any]:
    """Last resort fallback."""
    from ha_client import call_service
    log.info(f"[announce.generic] Trying play_media on {entity_id}")
    return await call_service(ha_url, ha_token, "media_player", "play_media", entity_id, {
        "media_content_id": media_url,
        "media_content_type": "url"
    })

TV_HANDLER_MAP = {
    "cast": announce_cast,
    "roku": announce_roku,
    "webos": announce_webos,
    "android_tv": announce_android_tv,
    "samsung": announce_samsung,
    "bravia": announce_bravia,
    "music_assistant": announce_music_assistant,
    "generic_tv": announce_generic_tv,
    "speaker": announce_speaker,
    "generic": announce_generic,
}

async def dispatch_announce(ha_url: str, ha_token: str, entity_id: str, media_url: str, volume: float, state: str, attributes: dict) -> Dict[str, Any]:
    """Dispatch announcement to the appropriate TV handler based on device detection."""
    tv_type = detect_tv_type(entity_id, state, attributes)
    handler = TV_HANDLER_MAP.get(tv_type, announce_generic)
    
    log.info(f"[announce] Detected type: {tv_type} for {entity_id} (app_id={attributes.get('app_id', '?')}, device_class={attributes.get('device_class', '?')})")
    return await handler(ha_url, ha_token, entity_id, media_url, volume)

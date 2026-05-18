"""TV-specific announcement handlers for different smart TV platforms."""
import asyncio
import logging
from typing import Dict, Any, Optional, Set

log = logging.getLogger("execution.announce")

# Supported feature flags (from HA media_player)
SUPPORT_SELECT_SOURCE = 1
SUPPORT_PLAY_MEDIA = 16384
SUPPORT_TURN_ON = 128
SUPPORT_TURN_OFF = 256
SUPPORT_VOLUME_SET = 4
SUPPORT_BROWSE_MEDIA = 131072

# Cast receiver app IDs
CAST_APP_IDS = {"cc1ad845", "9ac10326", "4475d545", "e8c61a77", "53c9a77e"}

# Android TV package prefixes
ANDROID_PACKAGE_PREFIXES = ("com.google.android.", "com.google.tv.", "com.android.", "com.sonymobile.android.")

# Android TV app_id indicators
ANDROID_INDICATORS = ("mediashell", "backdrop", "tvlauncher", "android.tv", "atv", "chromecast")

# Roku-specific source names
ROKU_SOURCES = {"home", "roku media player", "the roku channel", "roku tv intro"}

# Common streaming apps (present on many platforms, but Roku has them as sources)
STREAMING_APPS = {"netflix", "hulu", "disney plus", "prime video", "youtube", "youtube tv", "peacock tv", "paramount plus", "tubi", "fandango at home"}

# Known manufacturer/model patterns in entity_id or attributes
MANUFACTURER_PATTERNS = {
    "lg": "webos",
    "webos": "webos",
    "samsung": "samsung",
    "tizen": "samsung",
    "sony": "bravia",
    "bravia": "bravia",
    "roku": "roku",
    "hisense": "android_tv",
    "philips": "android_tv",
    "sharp": "roku",
    "tcl": "roku",
    "vizio": "generic_tv",
    "panasonic": "generic_tv",
    "toshiba": "generic_tv",
}

def detect_tv_type(entity_id: str, state: str, attributes: dict, loaded_components: Optional[Set[str]] = None) -> str:
    """Detect TV platform type using multiple context clues in priority order.
    
    Uses:
    1. entity_id patterns (integration naming conventions)
    2. app_id (Cast receiver IDs, Android packages, etc.)
    3. device_class (tv, speaker, receiver)
    4. supported_features (bitmask of capabilities)
    5. source_list content (Roku apps, TV inputs)
    6. loaded_components (HA integration list)
    """
    eid = entity_id.lower()
    app_id = (attributes.get("app_id") or "").lower()
    device_class = (attributes.get("device_class") or "").lower()
    supported_features = attributes.get("supported_features", 0)
    source_list = [s.lower().strip() for s in (attributes.get("source_list") or [])]
    
    # 1. Cast devices: entity contains 'chrome'/'cast', or app_id matches known Cast receivers
    if "chrome" in eid or "_cast" in eid or app_id in CAST_APP_IDS:
        return "cast"
    
    # 2. Roku: entity contains 'roku', OR source_list has Roku-specific entries
    #    OR MA player with Roku active_queue (MA wraps Roku as mass_player_type=player)
    has_roku_sources = bool(ROKU_SOURCES & set(source_list))
    has_many_streaming = len(ROKU_SOURCES & set(source_list)) >= 1 or len(STREAMING_APPS & set(source_list)) >= 5
    active_queue = (attributes.get("active_queue") or "").lower()
    is_ma_roku = app_id == "music_assistant" and ("roku" in active_queue or "roku" in eid)
    if "roku" in eid or has_roku_sources or has_many_streaming or is_ma_roku:
        return "roku"
    
    # 3. Android TV: app_id is Android package name, or contains Android indicators
    is_android_app = any(app_id.startswith(pkg) for pkg in ANDROID_PACKAGE_PREFIXES)
    is_android_indicator = any(ind in app_id for ind in ANDROID_INDICATORS)
    if is_android_app or is_android_indicator:
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
    
    # 7. ESPHome: entity contains 'esphome'
    if "esphome" in eid:
        return "esphome"
    
    # 8. DLNA: entity contains 'dlna'
    if "dlna" in eid:
        return "dlna"
    
    # 9. Music Assistant: app_id is 'music_assistant'
    if app_id == "music_assistant":
        return "music_assistant"
    
    # 10. Generic TV: device_class=tv with TV inputs in source_list
    tv_inputs = {"live tv", "tv", "hdmi", "hdmi 1", "hdmi 2", "hdmi 3", "av", "component"}
    has_tv_inputs = bool(tv_inputs & set(source_list))
    if device_class == "tv" or has_tv_inputs:
        return "generic_tv"
    
    # 11. Speaker: device_class=speaker
    if device_class == "speaker":
        return "speaker"
    
    # 12. Use loaded components as additional signal
    if loaded_components:
        if "cast.media_player" in loaded_components and supported_features & SUPPORT_PLAY_MEDIA:
            return "cast"
        if "roku" in loaded_components and supported_features & SUPPORT_BROWSE_MEDIA:
            return "roku"
        if "webostv.media_player" in loaded_components:
            return "webos"
        if "samsungtv.media_player" in loaded_components:
            return "samsung"
        if "androidtv_remote.media_player" in loaded_components:
            return "android_tv"
        if "dlna_dmr.media_player" in loaded_components:
            return "dlna"
    
    # 12. Fallback: unknown device
    return "unknown"

async def search_device_type(entity_id: str, attributes: dict, loaded_components: Optional[Set[str]] = None) -> Optional[str]:
    """Search the web to identify an unknown device type using available clues.
    
    Uses entity_id patterns, app_id, supported_features, and loaded components
    to construct a search query and determine the device platform.
    """
    clues = []
    
    # Extract clues from entity_id
    eid_parts = entity_id.lower().replace("media_player.", "").replace("_", " ").split()
    clues.extend(eid_parts)
    
    # Extract clues from app_id
    app_id = attributes.get("app_id", "")
    if app_id:
        clues.append(app_id)
    
    # Extract clues from supported_features
    features = attributes.get("supported_features", 0)
    if features & SUPPORT_BROWSE_MEDIA:
        clues.append("browse media")
    if features & SUPPORT_SELECT_SOURCE:
        clues.append("source select")
    
    # Extract clues from loaded components
    if loaded_components:
        related = [c for c in loaded_components if "media_player" in c or "tv" in c or "cast" in c or "roku" in c]
        clues.extend(related)
    
    if not clues:
        return None
    
    # Construct search query
    query = f"home assistant media_player {' '.join(clues[:5])} integration type"
    
    try:
        from websearch import web_search
        results = await web_search(query, num_results=3)
        
        # Analyze results for platform indicators
        combined = " ".join([r.get("snippet", "") + " " + r.get("title", "") for r in results]).lower()
        
        platform_keywords = {
            "cast": ["google cast", "chromecast", "cast integration"],
            "roku": ["roku integration", "roku media player"],
            "android_tv": ["android tv", "androidtv_remote", "adb"],
            "webos": ["lg webos", "webostv"],
            "samsung": ["samsung tv", "tizen", "smartthings"],
            "bravia": ["sony bravia"],
            "dlna": ["dlna", "dlna_dmr"],
        }
        
        for platform, keywords in platform_keywords.items():
            if any(kw in combined for kw in keywords):
                log.info(f"[announce.search] Web search suggests {platform} for {entity_id}")
                return platform
    except Exception as e:
        log.warning(f"[announce.search] Web search failed: {e}")
    
    return None

async def announce_cast(ha_url: str, ha_token: str, entity_id: str, media_url: str, volume: float, state: str = "unknown", attributes: dict = None) -> Dict[str, Any]:
    """Cast devices support direct URL playback."""
    from ha_client import call_service
    log.info(f"[announce.cast] Playing URL on {entity_id}: {media_url[:60]}")
    return await call_service(ha_url, ha_token, "media_player", "play_media", entity_id, {
        "media_content_id": media_url,
        "media_content_type": "url"
    })

async def announce_roku(ha_url: str, ha_token: str, entity_id: str, media_url: str, volume: float, state: str = "unknown", attributes: dict = None, message: str = "") -> Dict[str, Any]:
    """Roku: wake display via ECP, launch Media Assistant with audio URL.
    
    Based on Media Assistant docs and main branch flow:
    1. Wake display via ECP Home key
    2. Launch Media Assistant via ECP with t=a, u=[Media URL]
    """
    from ha_client import call_service
    import device_discovery
    
    # 1. Wake display via ECP Home key
    log.info(f"[announce.roku] Waking display for {entity_id}...")
    
    roku_result = await device_discovery.discover_device(entity_id, ha_url, ha_token, device_type="roku")
    roku_ip = roku_result.get("ip") if roku_result else None
    
    if roku_ip:
        import httpx
        try:
            log.info(f"[announce.roku] Sending Home key via ECP to {roku_ip}")
            async with httpx.AsyncClient(verify=False, timeout=5) as client:
                resp = await client.post(f"http://{roku_ip}:8060/keypress/Home")
                log.info(f"[announce.roku] ECP Home key response: {resp.status_code}")
        except Exception as e:
            log.warning(f"[announce.roku] ECP Home key failed: {e}")
    
    await call_service(ha_url, ha_token, "media_player", "turn_on", entity_id, {})
    await asyncio.sleep(3)
    
    # 2. Launch Media Assistant with audio URL via ECP (matching main branch video flow)
    if roku_ip:
        import httpx
        try:
            ecp_url = f"http://{roku_ip}:8060/launch/782875"
            params = {
                "t": "a",
                "u": media_url,
                "songName": message or "SharedLLM Announcement",
                "songFormat": "wav",
                "autoplay": "true"
            }
            log.info(f"[announce.roku] ECP launch: {ecp_url} params={params}")
            async with httpx.AsyncClient(verify=False, timeout=15) as client:
                resp = await client.post(ecp_url, params=params)
                log.info(f"[announce.roku] ECP response: {resp.status_code}")
                if resp.status_code in (200, 204):
                    return {"ok": True}
        except Exception as e:
            log.warning(f"[announce.roku] ECP launch failed: {e}")
    
    # Fallback: try media_player.play_media
    log.info(f"[announce.roku] Fallback: play_media with URL")
    return await call_service(ha_url, ha_token, "media_player", "play_media", entity_id, {
        "media_content_id": media_url,
        "media_content_type": "url"
    })

async def announce_webos(ha_url: str, ha_token: str, entity_id: str, media_url: str, volume: float, state: str = "unknown", attributes: dict = None) -> Dict[str, Any]:
    """webOS TV: try webostv.notify, then media_player as fallback."""
    from ha_client import call_service
    log.info(f"[announce.webos] Trying webostv.notify on {entity_id}")
    
    result = await call_service(ha_url, ha_token, "webostv", "notify", entity_id, {
        "message": media_url,
        "icon": "https://ha.sumemail.com/local/kokoro-icon.png"
    })
    
    if result.get("ok"):
        return result
    
    log.info("[announce.webos] Falling back to media_player.play_media")
    return await call_service(ha_url, ha_token, "media_player", "play_media", entity_id, {
        "media_content_id": media_url,
        "media_content_type": "url"
    })

async def announce_android_tv(ha_url: str, ha_token: str, entity_id: str, media_url: str, volume: float, state: str = "unknown", attributes: dict = None) -> Dict[str, Any]:
    """Android TV: try media_player, then ADB commands."""
    from ha_client import call_service
    log.info(f"[announce.android_tv] Trying play_media on {entity_id}")
    
    result = await call_service(ha_url, ha_token, "media_player", "play_media", entity_id, {
        "media_content_id": media_url,
        "media_content_type": "url"
    })
    
    if result.get("ok"):
        return result
    
    # Try ADB to launch media player
    log.info("[announce.android_tv] Trying ADB command to launch media")
    await call_service(ha_url, ha_token, "androidtv", "adb_command", entity_id, {"command": "HOME"})
    await asyncio.sleep(1)
    
    return await call_service(ha_url, ha_token, "androidtv", "adb_command", entity_id, {
        "command": f"am start -d '{media_url}' -a android.intent.action.VIEW"
    })

async def announce_samsung(ha_url: str, ha_token: str, entity_id: str, media_url: str, volume: float, state: str = "unknown", attributes: dict = None) -> Dict[str, Any]:
    """Samsung Tizen TV: try play_media, then samsungtv.send_key fallback."""
    from ha_client import call_service
    log.info(f"[announce.samsung] Trying play_media on {entity_id}")
    
    result = await call_service(ha_url, ha_token, "media_player", "play_media", entity_id, {
        "media_content_id": media_url,
        "media_content_type": "url"
    })
    
    if result.get("ok"):
        return result
    
    # Fallback: use samsungtv send_key to trigger playback
    log.info(f"[announce.samsung] Falling back to samsungtv.send_key on {entity_id}")
    return await call_service(ha_url, ha_token, "samsungtv", "send_key", entity_id, {
        "key": "KEY_PLAY"
    })

async def announce_bravia(ha_url: str, ha_token: str, entity_id: str, media_url: str, volume: float, state: str = "unknown", attributes: dict = None) -> Dict[str, Any]:
    """Sony Bravia TV."""
    from ha_client import call_service
    log.info(f"[announce.bravia] Trying play_media on {entity_id}")
    return await call_service(ha_url, ha_token, "media_player", "play_media", entity_id, {
        "media_content_id": media_url,
        "media_content_type": "url"
    })

async def announce_music_assistant(ha_url: str, ha_token: str, entity_id: str, media_url: str, volume: float, state: str = "unknown", attributes: dict = None) -> Dict[str, Any]:
    """Music Assistant integration."""
    from ha_client import call_service
    log.info(f"[announce.mass] Using media_player.play_media on {entity_id}")
    return await call_service(ha_url, ha_token, "media_player", "play_media", entity_id, {
        "media_content_id": media_url,
        "media_content_type": "url"
    })

async def announce_esphome(ha_url: str, ha_token: str, entity_id: str, media_url: str, volume: float, state: str = "unknown", attributes: dict = None) -> Dict[str, Any]:
    """ESPHome media player."""
    from ha_client import call_service
    log.info(f"[announce.esphome] Trying play_media on {entity_id}")
    return await call_service(ha_url, ha_token, "media_player", "play_media", entity_id, {
        "media_content_id": media_url,
        "media_content_type": "url"
    })

async def announce_dlna(ha_url: str, ha_token: str, entity_id: str, media_url: str, volume: float, state: str = "unknown", attributes: dict = None) -> Dict[str, Any]:
    """DLNA renderer."""
    from ha_client import call_service
    log.info(f"[announce.dlna] Trying play_media on {entity_id}")
    return await call_service(ha_url, ha_token, "media_player", "play_media", entity_id, {
        "media_content_id": media_url,
        "media_content_type": "url"
    })

async def announce_generic_tv(ha_url: str, ha_token: str, entity_id: str, media_url: str, volume: float, state: str = "unknown", attributes: dict = None) -> Dict[str, Any]:
    """Generic TV fallback."""
    from ha_client import call_service
    log.info(f"[announce.generic_tv] Trying play_media on {entity_id}")
    return await call_service(ha_url, ha_token, "media_player", "play_media", entity_id, {
        "media_content_id": media_url,
        "media_content_type": "url"
    })

async def announce_speaker(ha_url: str, ha_token: str, entity_id: str, media_url: str, volume: float, state: str = "unknown", attributes: dict = None) -> Dict[str, Any]:
    """Generic speaker."""
    from ha_client import call_service
    log.info(f"[announce.speaker] Trying play_media on {entity_id}")
    return await call_service(ha_url, ha_token, "media_player", "play_media", entity_id, {
        "media_content_id": media_url,
        "media_content_type": "url"
    })

async def announce_unknown(ha_url: str, ha_token: str, entity_id: str, media_url: str, volume: float, state: str = "unknown", attributes: dict = None) -> Dict[str, Any]:
    """Unknown device: try generic play_media, log all attributes for later identification."""
    from ha_client import call_service
    log.info(f"[announce.unknown] Unknown device type for {entity_id}, trying generic play_media")
    log.info(f"[announce.unknown] Playing media on {entity_id}: {media_url[:60]} (app_id={attributes.get('app_id', '?')})")
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
    "esphome": announce_esphome,
    "dlna": announce_dlna,
    "generic_tv": announce_generic_tv,
    "speaker": announce_speaker,
    "unknown": announce_unknown,
}

async def dispatch_announce(ha_url: str, ha_token: str, entity_id: str, media_url: str, volume: float, state: str, attributes: dict, loaded_components: Optional[Set[str]] = None, message: str = "") -> Dict[str, Any]:
    """Dispatch announcement to the appropriate TV handler based on device detection."""
    tv_type = detect_tv_type(entity_id, state, attributes, loaded_components)
    
    # If unknown, try web search fallback
    if tv_type == "unknown":
        log.info(f"[announce] Unknown device type for {entity_id}, attempting web search...")
        search_result = await search_device_type(entity_id, attributes, loaded_components)
        if search_result:
            tv_type = search_result
            log.info(f"[announce] Web search identified type as: {tv_type}")
        else:
            log.warning(f"[announce] Could not identify device type for {entity_id}, using unknown handler")
    
    handler = TV_HANDLER_MAP.get(tv_type, announce_unknown)
    
    log.info(f"[announce] Detected type: {tv_type} for {entity_id} (app_id={attributes.get('app_id', '?')}, device_class={attributes.get('device_class', '?')}, features={attributes.get('supported_features', '?')})")
    return await handler(ha_url, ha_token, entity_id, media_url, volume, state, attributes, message=message)

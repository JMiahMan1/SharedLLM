# services/execution/handlers/roku.py
"""
Roku media playback and transport commands via Home Assistant's roku integration.

Roku uses a two-part approach for music:
  1. ECP launch of Media Assistant (782875) for the rich music UI
  2. Music Assistant service call on the MA player sibling for actual audio

For transport:
  - roku.launch: Launch an app by app_id
  - roku.press: Send a key press (HOME, BACK, PLAY, etc.)
  - remote.send_command: Alternative transport via remote entity
"""
import logging
import asyncio
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
try:
    import ha_client
    from schemas import ExecutionResult
    import device_registry
    import device_discovery
except ImportError:
    import ha_client
    from schemas import ExecutionResult
    import device_registry
    import device_discovery

log = logging.getLogger("execution.roku")

ROKU_KEYS = {
    "home": "Home", "back": "Back", "enter": "Select", "select": "Select",
    "up": "Up", "down": "Down", "left": "Left", "right": "Right",
    "play": "Play", "pause": "Play", "stop": "Stop",
    "fast_forward": "Fwd", "rewind": "Rev",
    "info": "Info", "backspace": "Backspace",
    "volume_up": "VolumeUp", "volume_down": "VolumeDown", "mute": "VolumeMute",
    "power_off": "PowerOff", "power_on": "PowerOn",
    "input_av1": "InputAV1", "input_hdmi1": "InputHDMI1",
    "input_hdmi2": "InputHDMI2", "input_hdmi3": "InputHDMI3",
    "input_hdmi4": "InputHDMI4", "input_tuner": "InputTuner",
}

ROKU_APPS = {
    "netflix": "12", "youtube": "837", "hulu": "2285",
    "disney_plus": "291097", "prime_video": "13", "spotify": "22297",
    "plex": "13535", "tubi": "26079", "peacock": "427192",
    "paramount_plus": "428927", "hbo_max": "301921", "apple_tv": "472192",
    "media_assistant": "782875",
}

MEDIA_ASSISTANT_CHANNEL_ID = "782875"


async def is_roku_device(ha_url: str, ha_token: str, entity_id: str) -> bool:
    """Check if a media_player entity is a Roku device."""
    state = await ha_client.get_state(ha_url, ha_token, entity_id)
    if not state:
        return False
    attrs = state.get("attributes", {})
    app_id = (attrs.get("app_id") or "").lower()
    entity_id_lower = entity_id.lower()
    roku_indicators = ("roku.", "com.roku.", "roku media player")
    if any(ind in app_id for ind in roku_indicators):
        return True
    if "roku" in entity_id_lower:
        return True
    source_list = [s.lower() for s in (attrs.get("source_list") or [])]
    roku_sources = {"home", "roku media player", "the roku channel"}
    if roku_sources & set(source_list):
        return True
    return False


async def get_roku_ip(ha_url: str, ha_token: str, entity_id: str) -> str | None:
    """Discover Roku IP via unified discovery pipeline."""
    result = await device_discovery.discover_device(
        entity_id, ha_url, ha_token, device_type="roku"
    )
    if result:
        return result.get("ip")
    return None


async def find_ma_player_sibling(ha_url: str, ha_token: str, roku_entity: str) -> str | None:
    """Find the Music Assistant player entity that is a sibling of the Roku entity.
    
    Only returns MA players that have an active_queue (i.e., are connected to a MA output).
    """
    all_states = await ha_client.get_states(ha_url, ha_token)
    if not all_states:
        return None

    roku_friendly = ""
    for state in all_states:
        if state.get("entity_id") == roku_entity:
            roku_friendly = state.get("attributes", {}).get("friendly_name", "").lower()
            break

    if not roku_friendly:
        return None

    for state in all_states:
        eid = state.get("entity_id", "")
        if not eid.startswith("media_player.") or eid == roku_entity:
            continue
        attrs = state.get("attributes", {})
        friendly = attrs.get("friendly_name", "").lower()
        source = attrs.get("source", "").lower()
        integration = attrs.get("integration", "")
        active_queue = attrs.get("active_queue")

        # Must have an active MA queue to be a valid playback target
        if not active_queue:
            continue

        is_ma = ("music_assistant" in str(integration).lower() or
                 "active_queue" in attrs or
                 "mass_player_type" in attrs or
                 "music_assistant" in source)

        if is_ma and (roku_friendly in friendly or friendly in roku_friendly):
            log.info(f"[roku] Found MA player sibling: {eid} (queue: {active_queue})")
            return eid

    log.warning(f"[roku] No MA player sibling with active queue found for {roku_entity}")
    return None


async def roku_play_music(ha_url: str, ha_token: str, roku_entity: str, query: str,
                          mass_config_entry_id: str = "") -> ExecutionResult:
    """
    Play music on Roku using the two-part approach:
    1. Launch Media Assistant (782875) via ECP for the UI
    2. Call music_assistant/play_media on the MA player sibling for audio
    """
    log.info(f"[roku.music] Playing '{query}' on {roku_entity}")

    roku_ip = await get_roku_ip(ha_url, ha_token, roku_entity)
    if not roku_ip:
        log.warning("[roku.music] Could not find Roku IP, falling back to MA-only")
        ma_entity = await find_ma_player_sibling(ha_url, ha_token, roku_entity)
        if ma_entity:
            result = await ha_client.call_service(
                ha_url, ha_token, "music_assistant", "play_media", ma_entity,
                {"media_id": query, "media_type": "track", "enqueue": "play"},
            )
            if result.get("ok"):
                return ExecutionResult(status="SUCCESS", message=f"Playing '{query}' on {roku_entity} (MA only).", service="roku_music")

    ma_entity = await find_ma_player_sibling(ha_url, ha_token, roku_entity)
    if not ma_entity:
        return ExecutionResult(status="FAILURE", message=f"Could not find Music Assistant player for {roku_entity}.", service="roku_music")

    import httpx
    params = {"t": "a", "autoplay": "true"}

    search_result = await ha_client.call_service(
        ha_url, ha_token, "music_assistant", "search", entity_id="",
        service_data={
            "config_entry_id": mass_config_entry_id,
            "name": query,
            "media_type": ["track", "artist", "album", "playlist"],
            "limit": 1,
        },
        return_response=True,
    )

    song_name = query
    artist_name = ""
    ma_media_id = query
    ma_media_type = "track"

    if search_result.get("ok") and search_result.get("service_response"):
        raw = search_result["service_response"]
        resp = raw.get("service_response", raw)
        for category in ["tracks", "albums", "artists", "playlists"]:
            items = resp.get(category, [])
            if items:
                item = items[0]
                song_name = item.get("name", item.get("title", query))
                artist_name = item.get("artist", {}).get("name", "") if isinstance(item.get("artist"), dict) else ""
                ma_media_id = item.get("uri", query)
                ma_media_type = category.rstrip("s")
                params["songName"] = song_name
                if artist_name:
                    params["artistName"] = artist_name
                if item.get("image", {}).get("path"):
                    params["albumArt"] = item["image"]["path"]
                log.info(f"[roku.music] MA search match: {song_name} by {artist_name}")
                break
    else:
        params["songName"] = song_name

    ecp_url = f"http://{roku_ip}:8060/launch/{MEDIA_ASSISTANT_CHANNEL_ID}"
    try:
        log.info(f"[roku.music] Launching Media Assistant via ECP: {ecp_url}")
        async with httpx.AsyncClient(verify=False, timeout=10) as client:
            resp = await client.post(ecp_url, params=params)
            log.info(f"[roku.music] ECP response: {resp.status_code}")
            if resp.status_code in (200, 204):
                await asyncio.sleep(3)
            else:
                log.warning(f"[roku.music] ECP launch returned {resp.status_code}")
    except Exception as e:
        log.warning(f"[roku.music] ECP launch failed: {e}")
        device_registry.invalidate_device(roku_entity)

    log.info(f"[roku.music] Delegating audio to MA: {ma_entity} media_id={ma_media_id} type={ma_media_type}")
    result = await ha_client.call_service(
        ha_url, ha_token, "music_assistant", "play_media", ma_entity,
        {"media_id": ma_media_id, "media_type": ma_media_type, "enqueue": "play"},
    )

    if result.get("ok"):
        return ExecutionResult(status="SUCCESS", message=f"Playing '{song_name}' on {roku_entity}.", service="roku_music")

    return ExecutionResult(status="FAILURE", message=f"Failed to play music on {roku_entity}: {result.get('error')}", service="roku_music", detail=result)


async def roku_press(ha_url: str, ha_token: str, entity_id: str, key: str) -> ExecutionResult:
    """Send a key press to a Roku device."""
    roku_key = ROKU_KEYS.get(key.lower(), key)
    log.info(f"[roku] Pressing '{roku_key}' on {entity_id}")
    result = await ha_client.call_service(
        ha_url, ha_token, "roku", "press", entity_id,
        {"key": roku_key}
    )
    if result.get("ok"):
        return ExecutionResult(status="SUCCESS", message=f"Sent '{key}' to {entity_id} (Roku).", service="roku_transport")
    return ExecutionResult(status="FAILURE", message=f"Failed to send '{key}' to Roku: {result.get('error')}", service="roku_transport", detail=result)


async def roku_launch(ha_url: str, ha_token: str, entity_id: str, app: str) -> ExecutionResult:
    """Launch an app on a Roku device."""
    app_id = ROKU_APPS.get(app.lower(), app)
    log.info(f"[roku] Launching app '{app_id}' on {entity_id}")
    result = await ha_client.call_service(
        ha_url, ha_token, "roku", "launch", entity_id,
        {"app_id": app_id}
    )
    if result.get("ok"):
        return ExecutionResult(status="SUCCESS", message=f"Launched '{app}' on {entity_id} (Roku).", service="roku_transport")
    return ExecutionResult(status="FAILURE", message=f"Failed to launch '{app}' on Roku: {result.get('error')}", service="roku_transport", detail=result)


async def roku_home(ha_url: str, ha_token: str, entity_id: str) -> ExecutionResult:
    """Return Roku to home screen."""
    return await roku_press(ha_url, ha_token, entity_id, "home")


async def roku_back(ha_url: str, ha_token: str, entity_id: str) -> ExecutionResult:
    """Send back command on Roku."""
    return await roku_press(ha_url, ha_token, entity_id, "back")


async def roku_power_off(ha_url: str, ha_token: str, entity_id: str) -> ExecutionResult:
    """Power off Roku device."""
    return await roku_press(ha_url, ha_token, entity_id, "power_off")


async def roku_play_pause(ha_url: str, ha_token: str, entity_id: str, command: str) -> ExecutionResult:
    """Play or pause on Roku (same key toggles)."""
    return await roku_press(ha_url, ha_token, entity_id, "play")


async def roku_stop(ha_url: str, ha_token: str, entity_id: str) -> ExecutionResult:
    """Stop playback on Roku."""
    return await roku_press(ha_url, ha_token, entity_id, "stop")

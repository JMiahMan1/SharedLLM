# services/execution/handlers/android_tv.py
"""
Android TV media transport commands via Home Assistant's androidtv_remote integration.

Services:
  - androidtv_remote.send_command: Send remote key (home, back, sleep, etc.)
  - media_player.*: Standard media controls (play, pause, stop, etc.)
"""
import logging

try:
    import ha_client
    from schemas import ExecutionResult
except ImportError:
    from .. import ha_client
    from ..schemas import ExecutionResult

log = logging.getLogger("execution.android_tv")

ANDROID_TV_COMMANDS = {
    "home": "home", "back": "back", "sleep": "sleep",
    "power_off": "sleep", "turn_off": "sleep",
    "power_on": "wake", "turn_on": "wake",
    "up": "up", "down": "down", "left": "left", "right": "right",
    "enter": "enter", "select": "enter", "ok": "enter",
    "play": "media_play", "pause": "media_pause", "stop": "media_stop",
    "fast_forward": "fast_forward", "rewind": "rewind",
    "volume_up": "volume_up", "volume_down": "volume_down", "mute": "volume_mute",
}


async def is_android_tv(ha_url: str, ha_token: str, entity_id: str) -> bool:
    """Check if a media_player entity is an Android TV device."""
    state = await ha_client.get_state(ha_url, ha_token, entity_id)
    if not state:
        return False
    attrs = state.get("attributes", {})

    # Signal 1: app_id contains Android indicators (when device is on)
    app_id = (attrs.get("app_id") or "").lower()
    android_indicators = ("com.google.android.", "com.google.tv.", "com.android.",
                          "mediashell", "backdrop", "tvlauncher", "android.tv")
    if any(ind in app_id for ind in android_indicators):
        return True

    # Signal 2: device_class == "tv" without Cast/MA attributes
    # (Cast devices don't have device_class=tv, MA wrappers have device_class=speaker)
    if attrs.get("device_class") == "tv" and not attrs.get("mass_player_type") and "cast" not in entity_id.lower():
        return True

    # Signal 3: corresponding remote entity exists (androidtv_remote creates both)
    remote_entity = entity_id.replace("media_player.", "remote.")
    try:
        remote_state = await ha_client.get_state(ha_url, ha_token, remote_entity)
        if remote_state and remote_state.get("state") in ("on", "off"):
            return True
    except Exception:
        pass

    return False


async def send_command(ha_url: str, ha_token: str, entity_id: str, command: str) -> ExecutionResult:
    """Send a remote command to an Android TV device."""
    atv_cmd = ANDROID_TV_COMMANDS.get(command.lower(), command.lower())
    log.info(f"[android_tv] Sending command '{atv_cmd}' to {entity_id}")
    result = await ha_client.call_service(
        ha_url, ha_token, "androidtv_remote", "send_command", entity_id,
        {"command": atv_cmd}
    )
    if result.get("ok"):
        return ExecutionResult(status="SUCCESS", message=f"Sent '{command}' to {entity_id} (Android TV).", service="android_tv_transport")
    return ExecutionResult(status="FAILURE", message=f"Failed to send '{command}' to Android TV: {result.get('error')}", service="android_tv_transport", detail=result)


async def home(ha_url: str, ha_token: str, entity_id: str) -> ExecutionResult:
    """Return Android TV to home screen."""
    return await send_command(ha_url, ha_token, entity_id, "home")


async def back(ha_url: str, ha_token: str, entity_id: str) -> ExecutionResult:
    """Send back command on Android TV."""
    return await send_command(ha_url, ha_token, entity_id, "back")


async def power_off(ha_url: str, ha_token: str, entity_id: str) -> ExecutionResult:
    """Put Android TV to sleep."""
    return await send_command(ha_url, ha_token, entity_id, "power_off")


async def _find_cast_sibling(ha_url: str, ha_token: str, atv_entity_id: str) -> str | None:
    """Find a non-MA Cast sibling for the given Android TV entity.

    Multiple integrations control the same physical device, each with their own entity.
    Uses capability-based detection from HA state attributes:
    1. Exclude MA wrappers (app_id, mass_player_type)
    2. Require play_media capability (supported_features & 8424)
    3. Require Cast signals (entity_id hints, friendly name, cast_type)
    4. Single candidate = confident match
    """
    all_states = await ha_client.get_states(ha_url, ha_token)
    if not all_states:
        return None

    atv_exists = False
    atv_friendly = ""
    for s in all_states:
        if s.get("entity_id") == atv_entity_id:
            atv_friendly = s.get("attributes", {}).get("friendly_name", "")
            atv_exists = True
            break

    if not atv_exists:
        log.debug(f"[android_tv] ATV entity {atv_entity_id} not found in HA states")
        return None

    candidates = []

    for s in all_states:
        eid = s.get("entity_id", "")
        if not eid.startswith("media_player.") or eid == atv_entity_id:
            continue
        s_attrs = s.get("attributes", {})
        s_entity_lower = eid.lower()

        # Exclude Music Assistant wrappers (primary filter)
        s_app_id = str(s_attrs.get("app_id", "")).lower()
        s_mass_type = s_attrs.get("mass_player_type")
        s_active_queue = s_attrs.get("active_queue")
        s_device_class = str(s_attrs.get("device_class", "")).lower()

        if s_app_id == "music_assistant" or s_mass_type:
            continue
        if s_device_class == "speaker" and s_active_queue:
            continue

        # Capability checks - must support play_media
        supported_features = int(s_attrs.get("supported_features", 0))
        has_play_media = bool(supported_features & 8424)
        if not has_play_media:
            continue

        # Cast device signals
        s_cast_type = str(s_attrs.get("cast_type", "")).lower()
        is_cast_type = s_cast_type in ("cast", "audio", "group", "chromecast")
        has_cast_hint = any(x in s_entity_lower for x in ["_chrome", "_cast", "_chromecast"])
        s_friendly = s_attrs.get("friendly_name", "")
        has_friendly_cast_hint = "cast" in s_friendly.lower() or "chromecast" in s_friendly.lower() or "chrome" in s_friendly.lower()

        if not (is_cast_type or has_cast_hint or has_friendly_cast_hint):
            continue

        candidates.append((eid, s_friendly))

    # Name-based matching for disambiguation
    if atv_friendly:
        for eid, s_friendly in candidates:
            if s_friendly == atv_friendly:
                log.info(f"[android_tv] Found Cast sibling (exact name) for {atv_entity_id}: {eid}")
                return eid
            if atv_friendly.lower() in s_friendly.lower() or s_friendly.lower() in atv_friendly.lower():
                log.info(f"[android_tv] Found Cast sibling (name hint) for {atv_entity_id}: {eid}")
                return eid

    # Single candidate = confident match
    if len(candidates) == 1:
        eid, _ = candidates[0]
        log.info(f"[android_tv] Found Cast sibling (single candidate) for {atv_entity_id}: {eid}")
        return eid

    return None


async def _ensure_volume_safe(ha_url: str, ha_token: str, entity_id: str) -> None:
    """Ensure device is unmuted and volume is at least 20%."""
    try:
        state = await ha_client.get_state(ha_url, ha_token, entity_id)
        if not state:
            return
        attrs = state.get("attributes", {})
        if attrs.get("is_volume_muted"):
            log.info(f"[android_tv] Device {entity_id} is muted, unmuting")
            await ha_client.call_service(ha_url, ha_token, "media_player", "volume_mute", entity_id, {"is_volume_muted": False})
            await __import__("asyncio").sleep(1)
        vol = attrs.get("volume_level")
        if vol is not None and vol < 0.2:
            log.info(f"[android_tv] Volume too low ({vol}), boosting to 20%")
            await ha_client.call_service(ha_url, ha_token, "media_player", "volume_set", entity_id, {"volume_level": 0.2})
            await __import__("asyncio").sleep(1)
    except Exception as e:
        log.warning(f"[android_tv] Volume safeguard failed: {e}")


async def play_video(ha_url: str, ha_token: str, entity_id: str, video_url: str, query: str) -> ExecutionResult:
    """
    Play video on Android TV by delegating to a Cast sibling.

    Android TV's play_media with local stream URLs often fails (HA 500).
    The proven approach: download video locally, serve via HTTP, then cast
    to the Cast sibling (e.g., media_player.office_tv_chrome) which handles
    video/mp4 streaming reliably.
    """
    from . import video as video_handler

    log.info(f"[android_tv/video] Delegating video playback for {entity_id}")

    # Power on Android TV
    try:
        await ha_client.call_service(ha_url, ha_token, "media_player", "turn_on", entity_id)
        await __import__("asyncio").sleep(2)
    except Exception as e:
        log.warning(f"[android_tv/video] turn_on failed: {e}")

    # Find Cast sibling for actual video playback
    cast_entity = await _find_cast_sibling(ha_url, ha_token, entity_id)
    target_entity = cast_entity if cast_entity else entity_id

    if cast_entity:
        log.info(f"[android_tv/video] Delegating from Android TV {entity_id} to Cast {cast_entity}")
        # Send nav_home to Android TV to ensure it's on the right screen
        try:
            await home(ha_url, ha_token, entity_id)
            await __import__("asyncio").sleep(1)
        except Exception:
            pass
        # Stop any active session on Cast target
        try:
            await ha_client.call_service(ha_url, ha_token, "media_player", "media_stop", cast_entity)
            await __import__("asyncio").sleep(1)
        except Exception:
            pass
    else:
        log.warning(f"[android_tv/video] No Cast sibling found for {entity_id}, will attempt direct playback")

    # Download video with progressive streaming
    media_id, title = await video_handler.download_video_progressive(video_url)
    if not media_id:
        return ExecutionResult(status="FAILURE", message=f"Failed to download video for '{query}'.", service="android_tv_video")

    from services.config import EXECUTION_EXTERNAL_HOST
    if not EXECUTION_EXTERNAL_HOST:
        return ExecutionResult(status="FAILURE", message="EXECUTION_EXTERNAL_HOST is not configured.", service="android_tv_video")

    stream_url = f"http://{EXECUTION_EXTERNAL_HOST}:8888/media/{media_id}"
    log.info(f"[android_tv/video] Streaming URL: {stream_url} -> {target_entity}")

    # Volume safeguard on target
    await _ensure_volume_safe(ha_url, ha_token, target_entity)

    # Play video on target entity
    result = await ha_client.call_service(
        ha_url, ha_token, "media_player", "play_media", target_entity,
        {"media_content_id": stream_url, "media_content_type": "video/mp4"},
    )

    if result.get("ok"):
        return ExecutionResult(status="SUCCESS", message=f"Playing video '{title or query}' on {entity_id}.", service="android_tv_video")

    return ExecutionResult(status="FAILURE", message=f"Failed to play video on {entity_id}.", service="android_tv_video")

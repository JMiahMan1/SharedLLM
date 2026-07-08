# services/execution/handlers/samsung.py
"""
Samsung Tizen TV media playback and transport commands via Home Assistant's samsungtv integration.

Services:
  - remote.send_command: Send remote key via the companion remote entity
  - media_player.*: Standard media controls (play, pause, stop, play_media, etc.)
  - media_player.turn_on: Wake-on-LAN power on (requires MAC address configured)

Samsung Tizen TVs support:
  - Power on via WOL (Wake-on-LAN) through media_player.turn_on
  - URL playback via media_player.play_media (video/mp4, audio/mpeg, etc.)
  - Remote key codes via remote.send_command for transport controls
  - ~15 second boot time from off state
"""
import asyncio
import logging

try:
    import device_discovery
    import ha_client
    from schemas import ExecutionResult
except ImportError:
    from .. import device_discovery, ha_client
    from ..schemas import ExecutionResult

log = logging.getLogger("execution.samsung")

SAMSUNG_KEYS = {
    "home": "Home", "back": "Return", "return": "Return",
    "enter": "Enter", "select": "Enter", "ok": "Enter",
    "play": "Play", "pause": "Pause", "stop": "Stop",
    "fast_forward": "FastForward", "rewind": "Rewind",
    "channel_up": "ChannelUp", "channel_down": "ChannelDown",
    "volume_up": "VolumeUp", "volume_down": "VolumeDown", "mute": "VolumeMute",
    "power_off": "Power", "power_on": "Power",
    "up": "Up", "down": "Down", "left": "Left", "right": "Right",
    "info": "Info", "menu": "Menu", "tools": "Tools",
    "exit": "Exit", "source": "Source", "guide": "Guide",
    "red": "Red", "green": "Green", "yellow": "Yellow", "blue": "Blue",
}

SAMSUNG_BOOT_TIME = 15
MEDIA_VERIFY_TIMEOUT = 30
MEDIA_VERIFY_INTERVAL = 2


async def _find_remote_entity(ha_url: str, ha_token: str, media_entity_id: str) -> str | None:
    """Find the remote companion entity for a media_player."""
    all_states = await ha_client.get_states(ha_url, ha_token)
    if not all_states:
        return None

    media_friendly = ""
    for state in all_states:
        if state.get("entity_id") == media_entity_id:
            media_friendly = state.get("attributes", {}).get("friendly_name", "").lower()
            break

    for state in all_states:
        eid = state.get("entity_id", "")
        if not eid.startswith("remote."):
            continue
        attrs = state.get("attributes", {})
        friendly = attrs.get("friendly_name", "").lower()
        if media_friendly and media_friendly in friendly:
            return eid
        media_short = media_entity_id.split(".")[-1].replace("_", " ")
        if media_short and media_short in friendly:
            return eid

    base = media_entity_id.replace("media_player.", "remote.")
    for state in all_states:
        if state.get("entity_id") == base:
            return base

    return None


async def is_samsung_tv(ha_url: str, ha_token: str, entity_id: str) -> bool:
    """Check if a media_player entity is a Samsung Tizen TV."""
    state = await ha_client.get_state(ha_url, ha_token, entity_id)
    if not state:
        return False
    attrs = state.get("attributes", {})
    app_id = (attrs.get("app_id") or "").lower()
    entity_id_lower = entity_id.lower()
    samsung_indicators = ("org.tizen.", "samsung.tv", "tizen.tv")
    if any(ind in app_id for ind in samsung_indicators):
        return True
    if "samsungtv" in entity_id_lower:
        return True
    return False


async def get_samsung_ip(ha_url: str, ha_token: str, entity_id: str) -> str | None:
    """Discover Samsung TV IP via unified discovery pipeline."""
    result = await device_discovery.discover_device(
        entity_id, ha_url, ha_token, device_type="samsung"
    )
    if result:
        return result.get("ip")
    return None


async def wake_device(ha_url: str, ha_token: str, entity_id: str) -> ExecutionResult:
    """Wake Samsung TV from off/standby state."""
    state = await ha_client.get_state(ha_url, ha_token, entity_id)
    if not state:
        return ExecutionResult(status="FAILURE", message=f"Cannot get state for {entity_id}", service="samsung_wake")

    current_state = state.get("state", "unknown")
    if current_state not in ("off", "unavailable", "standby"):
        log.info(f"[samsung.wake] {entity_id} is already {current_state}, no wake needed")
        return ExecutionResult(status="SUCCESS", message=f"{entity_id} is already {current_state}", service="samsung_wake")

    log.info(f"[samsung.wake] Waking {entity_id} from {current_state} via WOL...")
    result = await ha_client.call_service(
        ha_url, ha_token, "media_player", "turn_on", entity_id, {}
    )

    if not result.get("ok"):
        return ExecutionResult(
            status="FAILURE",
            message=f"Failed to wake {entity_id}: {result.get('error')}",
            service="samsung_wake",
            detail=result,
        )

    log.info(f"[samsung.wake] Waiting {SAMSUNG_BOOT_TIME}s for TV to boot...")
    await asyncio.sleep(SAMSUNG_BOOT_TIME)

    state = await ha_client.get_state(ha_url, ha_token, entity_id)
    if state and state.get("state") not in ("off", "unavailable", "standby"):
        return ExecutionResult(status="SUCCESS", message=f"{entity_id} woke up successfully", service="samsung_wake")

    return ExecutionResult(status="SUCCESS", message=f"Wake command sent to {entity_id}, waiting for boot", service="samsung_wake")


async def power_on(ha_url: str, ha_token: str, entity_id: str) -> ExecutionResult:
    """Power on Samsung TV via WOL."""
    return await wake_device(ha_url, ha_token, entity_id)


async def power_off(ha_url: str, ha_token: str, entity_id: str) -> ExecutionResult:
    """Power off Samsung TV."""
    return await send_key(ha_url, ha_token, entity_id, "power_off")


async def send_key(ha_url: str, ha_token: str, entity_id: str, command: str) -> ExecutionResult:
    """Send a key press to a Samsung Tizen TV via remote.send_command."""
    remote_entity = await _find_remote_entity(ha_url, ha_token, entity_id)
    if not remote_entity:
        log.warning(f"[samsung.send_key] No remote entity found for {entity_id}, trying direct")
        remote_entity = entity_id.replace("media_player.", "remote.")

    samsung_key = SAMSUNG_KEYS.get(command.lower(), command)
    log.info(f"[samsung] Sending key '{samsung_key}' to {remote_entity}")
    result = await ha_client.call_service(
        ha_url, ha_token, "remote", "send_command", remote_entity,
        {"command": samsung_key}
    )
    if result.get("ok"):
        return ExecutionResult(status="SUCCESS", message=f"Sent '{command}' to {entity_id} (Samsung).", service="samsung_transport")
    return ExecutionResult(status="FAILURE", message=f"Failed to send '{command}' to Samsung TV: {result.get('error')}", service="samsung_transport", detail=result)


async def home(ha_url: str, ha_token: str, entity_id: str) -> ExecutionResult:
    """Return Samsung TV to home screen."""
    return await send_key(ha_url, ha_token, entity_id, "home")


async def back(ha_url: str, ha_token: str, entity_id: str) -> ExecutionResult:
    """Send back command on Samsung TV."""
    return await send_key(ha_url, ha_token, entity_id, "back")


async def _verify_media_playing(ha_url: str, ha_token: str, entity_id: str,
                                 timeout: int = MEDIA_VERIFY_TIMEOUT,
                                 interval: int = MEDIA_VERIFY_INTERVAL) -> tuple[bool, str]:
    """Poll entity state until it shows media is playing.

    Returns (success, state_description).
    """
    elapsed = 0
    while elapsed < timeout:
        state = await ha_client.get_state(ha_url, ha_token, entity_id)
        if not state:
            log.warning(f"[samsung.verify] Cannot get state for {entity_id}")
            await asyncio.sleep(interval)
            elapsed += interval
            continue

        current = state.get("state", "unknown")
        attrs = state.get("attributes", {})
        media_content = attrs.get("media_content_id", "")
        media_title = attrs.get("media_title", "")

        if current == "playing":
            log.info(f"[samsung.verify] {entity_id} is playing: {media_title or media_content or 'unknown'}")
            return True, f"playing ({media_title or media_content or 'unknown'})"

        if current in ("off", "unavailable"):
            log.warning(f"[samsung.verify] {entity_id} went {current} during playback")
            return False, current

        log.info(f"[samsung.verify] {entity_id} state={current}, waiting... ({elapsed}s/{timeout}s)")
        await asyncio.sleep(interval)
        elapsed += interval

    state = await ha_client.get_state(ha_url, ha_token, entity_id)
    final_state = state.get("state", "unknown") if state else "unknown"
    log.warning(f"[samsung.verify] {entity_id} did not reach 'playing' within {timeout}s (state={final_state})")
    return False, final_state


async def play_media(ha_url: str, ha_token: str, entity_id: str, media_url: str,
                     media_type: str = "url", wake: bool = True) -> ExecutionResult:
    """Play media URL on Samsung Tizen TV with state verification."""
    if wake:
        wake_result = await wake_device(ha_url, ha_token, entity_id)
        if wake_result.status == "FAILURE":
            return wake_result

    log.info(f"[samsung.play_media] Playing {media_type} on {entity_id}: {media_url[:80]}")
    result = await ha_client.call_service(
        ha_url, ha_token, "media_player", "play_media", entity_id,
        {"media_content_id": media_url, "media_content_type": media_type}
    )

    if not result.get("ok"):
        return ExecutionResult(
            status="FAILURE",
            message=f"Failed to play media on {entity_id}: {result.get('error')}",
            service="samsung_play_media",
            detail=result,
        )

    success, state_desc = await _verify_media_playing(ha_url, ha_token, entity_id)
    if success:
        return ExecutionResult(status="SUCCESS", message=f"Playing {media_type} on {entity_id} (verified: {state_desc}).", service="samsung_play_media")

    return ExecutionResult(
        status="FAILURE",
        message=f"play_media command accepted but {entity_id} did not start playing (state: {state_desc}). URL: {media_url[:80]}",
        service="samsung_play_media",
    )


async def play_music(ha_url: str, ha_token: str, entity_id: str, audio_url: str) -> ExecutionResult:
    """Play audio/music on Samsung Tizen TV via media_player.play_media."""
    return await play_media(ha_url, ha_token, entity_id, audio_url, media_type="audio/mpeg")


async def play_video(ha_url: str, ha_token: str, entity_id: str, video_url: str,
                     title: str = "") -> ExecutionResult:
    """Play video on Samsung Tizen TV via media_player.play_media."""
    return await play_media(ha_url, ha_token, entity_id, video_url, media_type="video/mp4")

# services/execution/handlers/samsung.py
"""
Samsung Tizen TV media playback and transport commands via Home Assistant's samsungtv integration.

Services:
  - samsungtv.send_key: Send remote key (KEY_HOME, KEY_RETURN, etc.)
  - media_player.*: Standard media controls (play, pause, stop, play_media, etc.)
  - media_player.turn_on: Wake-on-LAN power on (requires MAC address configured)

Samsung Tizen TVs support:
  - Power on via WOL (Wake-on-LAN) through media_player.turn_on
  - URL playback via media_player.play_media (video/mp4, audio/mpeg, etc.)
  - Remote key codes via samsungtv.send_key for transport controls
  - ~15 second boot time from off state
"""
import logging
import asyncio
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
try:
    import ha_client
    from schemas import ExecutionResult
    import device_discovery
except ImportError:
    import ha_client
    from schemas import ExecutionResult
    import device_discovery

log = logging.getLogger("execution.samsung")

SAMSUNG_KEYS = {
    "home": "KEY_HOME", "back": "KEY_RETURN", "return": "KEY_RETURN",
    "enter": "KEY_ENTER", "select": "KEY_ENTER", "ok": "KEY_ENTER",
    "play": "KEY_PLAY", "pause": "KEY_PAUSE", "stop": "KEY_STOP",
    "fast_forward": "KEY_FF", "rewind": "KEY_REWIND",
    "channel_up": "KEY_CHUP", "channel_down": "KEY_CHDOWN",
    "volume_up": "KEY_VOLUP", "volume_down": "KEY_VOLDOWN", "mute": "KEY_MUTE",
    "power_off": "KEY_POWER", "power_on": "KEY_POWER",
    "up": "KEY_UP", "down": "KEY_DOWN", "left": "KEY_LEFT", "right": "KEY_RIGHT",
    "info": "KEY_INFO", "menu": "KEY_MENU", "tools": "KEY_TOOLS",
    "exit": "KEY_EXIT", "source": "KEY_SOURCE", "guide": "KEY_GUIDE",
    "red": "KEY_RED", "green": "KEY_GREEN", "yellow": "KEY_YELLOW", "blue": "KEY_BLUE",
}

SAMSUNG_BOOT_TIME = 15  # seconds for TV to fully boot from off state


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
    """Wake Samsung TV from off/standby state.

    Uses media_player.turn_on which triggers WOL (Wake-on-LAN).
    Waits for TV to fully boot (~15 seconds).
    """
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

    # Verify TV is on
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
    """Send a key press to a Samsung Tizen TV."""
    samsung_key = SAMSUNG_KEYS.get(command.lower(), f"KEY_{command.upper()}")
    log.info(f"[samsung] Sending key '{samsung_key}' to {entity_id}")
    result = await ha_client.call_service(
        ha_url, ha_token, "samsungtv", "send_key", entity_id,
        {"key": samsung_key}
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


async def play_media(ha_url: str, ha_token: str, entity_id: str, media_url: str,
                     media_type: str = "url", wake: bool = True) -> ExecutionResult:
    """Play media URL on Samsung Tizen TV.

    Supports video/mp4, audio/mpeg, and generic URLs.
    Wakes the TV first if wake=True.
    """
    if wake:
        wake_result = await wake_device(ha_url, ha_token, entity_id)
        if wake_result.status == "FAILURE":
            return wake_result

    log.info(f"[samsung.play_media] Playing {media_type} on {entity_id}: {media_url[:80]}")
    result = await ha_client.call_service(
        ha_url, ha_token, "media_player", "play_media", entity_id,
        {"media_content_id": media_url, "media_content_type": media_type}
    )

    if result.get("ok"):
        return ExecutionResult(status="SUCCESS", message=f"Playing {media_type} on {entity_id}.", service="samsung_play_media")

    return ExecutionResult(
        status="FAILURE",
        message=f"Failed to play media on {entity_id}: {result.get('error')}",
        service="samsung_play_media",
        detail=result,
    )


async def play_music(ha_url: str, ha_token: str, entity_id: str, audio_url: str) -> ExecutionResult:
    """Play audio/music on Samsung Tizen TV via media_player.play_media."""
    return await play_media(ha_url, ha_token, entity_id, audio_url, media_type="audio/mpeg")


async def play_video(ha_url: str, ha_token: str, entity_id: str, video_url: str,
                     title: str = "") -> ExecutionResult:
    """Play video on Samsung Tizen TV via media_player.play_media.

    Samsung Tizen TVs support MP4/H.264 playback via play_media.
    """
    return await play_media(ha_url, ha_token, entity_id, video_url, media_type="video/mp4")

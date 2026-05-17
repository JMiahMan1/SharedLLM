# services/execution/handlers/android_tv.py
"""
Android TV media transport commands via Home Assistant's androidtv_remote integration.

Services:
  - androidtv_remote.send_command: Send remote key (home, back, sleep, etc.)
  - media_player.*: Standard media controls (play, pause, stop, etc.)
"""
import logging
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
try:
    import ha_client
    from schemas import ExecutionResult
except ImportError:
    import ha_client
    from schemas import ExecutionResult

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
    app_id = (attrs.get("app_id") or "").lower()
    android_indicators = ("com.google.android.", "com.google.tv.", "com.android.",
                          "mediashell", "backdrop", "tvlauncher", "android.tv")
    return any(ind in app_id for ind in android_indicators)


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

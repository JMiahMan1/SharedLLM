# services/execution/handlers/roku.py
"""
Roku media playback and transport commands via Home Assistant's roku integration.

Roku uses the 'roku' domain with services:
  - roku.launch: Launch an app by app_id
  - roku.press: Send a key press (HOME, BACK, PLAY, etc.)
  - media_player.play_media: Play media URL (limited support)

Unlike Android TV/WebOS/Samsung, Roku does NOT support direct URL streaming
via media_player.play_media. Use yt-dlp + local streaming or cast instead.
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

log = logging.getLogger("execution.roku")

# Roku key mappings
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

# Common Roku app IDs
ROKU_APPS = {
    "netflix": "12", "youtube": "837", "hulu": "2285",
    "disney_plus": "291097", "prime_video": "13", "spotify": "22297",
    "plex": "13535", "tubi": "26079", "peacock": "427192",
    "paramount_plus": "428927", "hbo_max": "301921", "apple_tv": "472192",
    "media_assistant": "782875",  # Used for TTS announcements
}


async def is_roku_device(ha_url: str, ha_token: str, entity_id: str) -> bool:
    """Check if a media_player entity is a Roku device."""
    state = await ha_client.get_state(ha_url, ha_token, entity_id)
    if not state:
        return False
    attrs = state.get("attributes", {})
    app_id = (attrs.get("app_id") or "").lower()
    entity_id_lower = entity_id.lower()
    # Roku indicators
    roku_indicators = ("roku.", "com.roku.", "roku media player")
    if any(ind in app_id for ind in roku_indicators):
        return True
    if "roku" in entity_id_lower:
        return True
    # Check source_list for Roku-specific apps
    source_list = [s.lower() for s in (attrs.get("source_list") or [])]
    roku_sources = {"home", "roku media player", "the roku channel"}
    if roku_sources & set(source_list):
        return True
    return False


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

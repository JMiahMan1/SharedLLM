# services/execution/handlers/samsung.py
"""
Samsung Tizen TV media transport commands via Home Assistant's samsungtv integration.

Services:
  - samsungtv.send_key: Send remote key (KEY_HOME, KEY_RETURN, etc.)
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


async def power_off(ha_url: str, ha_token: str, entity_id: str) -> ExecutionResult:
    """Power off Samsung TV."""
    return await send_key(ha_url, ha_token, entity_id, "power_off")

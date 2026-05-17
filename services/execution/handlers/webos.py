# services/execution/handlers/webos.py
"""
LG WebOS TV media transport commands via Home Assistant's webostv integration.

Services:
  - webostv.command: Send WebOS command (HOME, BACK, PLAY, etc.)
  - webostv.select_sound_output: Change audio output
  - webostv.button: Simulate remote button press
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

log = logging.getLogger("execution.webos")

WEBOS_COMMANDS = {
    "home": "HOME", "back": "BACK", "enter": "ENTER",
    "play": "PLAY", "pause": "PAUSE", "stop": "STOP",
    "fast_forward": "FASTFORWARD", "rewind": "REWIND",
    "channel_up": "CHANNELUP", "channel_down": "CHANNELDOWN",
    "volume_up": "VOLUMEUP", "volume_down": "VOLUMEDOWN", "mute": "MUTE",
    "power_off": "POWER", "power_on": "POWER",
    "up": "UP", "down": "DOWN", "left": "LEFT", "right": "RIGHT",
    "red": "RED", "green": "GREEN", "yellow": "YELLOW", "blue": "BLUE",
    "info": "INFO", "menu": "MENU", "exit": "EXIT",
}


async def is_webos_tv(ha_url: str, ha_token: str, entity_id: str) -> bool:
    """Check if a media_player entity is an LG WebOS TV."""
    state = await ha_client.get_state(ha_url, ha_token, entity_id)
    if not state:
        return False
    attrs = state.get("attributes", {})
    app_id = (attrs.get("app_id") or "").lower()
    entity_id_lower = entity_id.lower()
    webos_indicators = ("com.webos.", "webos.tv", "lg.webos")
    if any(ind in app_id for ind in webos_indicators):
        return True
    if "webostv" in entity_id_lower:
        return True
    return False


async def send_command(ha_url: str, ha_token: str, entity_id: str, command: str) -> ExecutionResult:
    """Send a command to an LG WebOS TV."""
    webos_cmd = WEBOS_COMMANDS.get(command.lower(), command.upper())
    log.info(f"[webos] Sending command '{webos_cmd}' to {entity_id}")
    result = await ha_client.call_service(
        ha_url, ha_token, "webostv", "command", entity_id,
        {"command": webos_cmd}
    )
    if result.get("ok"):
        return ExecutionResult(status="SUCCESS", message=f"Sent '{command}' to {entity_id} (WebOS).", service="webos_transport")
    return ExecutionResult(status="FAILURE", message=f"Failed to send '{command}' to WebOS TV: {result.get('error')}", service="webos_transport", detail=result)


async def home(ha_url: str, ha_token: str, entity_id: str) -> ExecutionResult:
    """Return WebOS TV to home screen."""
    return await send_command(ha_url, ha_token, entity_id, "home")


async def back(ha_url: str, ha_token: str, entity_id: str) -> ExecutionResult:
    """Send back command on WebOS TV."""
    return await send_command(ha_url, ha_token, entity_id, "back")


async def power_off(ha_url: str, ha_token: str, entity_id: str) -> ExecutionResult:
    """Power off WebOS TV."""
    return await send_command(ha_url, ha_token, entity_id, "power_off")

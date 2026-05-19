# services/execution/handlers/webos.py
"""
LG WebOS TV media transport commands via Home Assistant's webostv integration.

Services:
  - webostv.command: Send WebOS command (HOME, BACK, PLAY, etc.)
  - webostv.select_sound_output: Change audio output
  - webostv.button: Simulate remote button press
"""
import logging
import socket
import struct
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


def _send_wol(mac: str, ip: str = "255.255.255.255", port: int = 9) -> bool:
    """Send Wake-on-LAN magic packet."""
    if len(mac) == 17:
        mac = mac.replace(":", "").replace("-", "")
    mac_bytes = bytes.fromhex(mac)
    if len(mac_bytes) != 6:
        raise ValueError(f"Invalid MAC address: {mac}")
    packet = b"\xff" * 6 + mac_bytes * 16
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.sendto(packet, (ip, port))
    sock.close()
    return True


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


async def _get_webos_device_info(ha_url: str, ha_token: str, entity_id: str) -> dict:
    """Get WebOS device info (IP, MAC) via HomeKit diagnostics or device registry."""
    import httpx
    headers = {"Authorization": f"Bearer {ha_token}"}
    state = await ha_client.get_state(ha_url, ha_token, entity_id)
    if not state:
        return {}

    friendly_name = state.get("attributes", {}).get("friendly_name", "")
    entity_lower = entity_id.lower()
    friendly_lower = friendly_name.lower()

    async with httpx.AsyncClient(verify=False, timeout=10) as client:
        # Get device registry to find webostv device model
        dev_resp = await client.get(f"{ha_url}/api/config/device_registry/list", headers=headers)
        webos_model = ""
        if dev_resp.status_code == 200:
            for dev in dev_resp.json():
                dev_name = (dev.get("name") or "").lower()
                dev_name_by_user = (dev.get("name_by_user") or "").lower()
                for identifier in dev.get("identifiers", []):
                    if identifier and identifier[0] == "webostv":
                        # Check if this device matches our entity
                        if (any(part in dev_name for part in friendly_lower.split() if len(part) > 2) or
                            any(part in dev_name_by_user for part in friendly_lower.split() if len(part) > 2) or
                            any(part in dev_name for part in entity_lower.split(".") if len(part) > 2)):
                            webos_model = (dev.get("model") or "").lower()
                            break
                if webos_model:
                    break

        # Get all config entries
        entries_resp = await client.get(f"{ha_url}/api/config/config_entries/entry", headers=headers)
        if entries_resp.status_code != 200:
            return {}

        for entry in entries_resp.json():
            if entry.get("domain") != "homekit_controller":
                continue
            entry_id = entry.get("entry_id")
            if not entry_id:
                continue
            diag_resp = await client.get(f"{ha_url}/api/diagnostics/config_entry/{entry_id}", headers=headers)
            if diag_resp.status_code != 200:
                continue
            diag_data = diag_resp.json()
            config_entry = diag_data.get("data", {}).get("config-entry", {})
            accessory_data = config_entry.get("data", {})
            accessory_ips = accessory_data.get("AccessoryIPs", [])
            pairing_id = accessory_data.get("AccessoryPairingID", "")
            title = (config_entry.get("title") or "").lower()

            if not accessory_ips:
                continue

            # Match by model number (most reliable)
            if webos_model and webos_model in title:
                return {
                    "ip": accessory_ips[0],
                    "mac": pairing_id.replace(":", ""),
                    "source": "homekit_diagnostics",
                }

            # Fallback: match by title words (skip short words)
            title_words = [w for w in title.split() if len(w) > 2]
            friendly_words = [w for w in friendly_lower.split() if len(w) > 2]
            if any(w in title for w in friendly_words) or any(w in title for w in entity_lower.split(".") if len(w) > 2):
                return {
                    "ip": accessory_ips[0],
                    "mac": pairing_id.replace(":", ""),
                    "source": "homekit_diagnostics",
                }
    return {}


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


async def power_on(ha_url: str, ha_token: str, entity_id: str) -> ExecutionResult:
    """Power on WebOS TV via WOL magic packet."""
    device_info = await _get_webos_device_info(ha_url, ha_token, entity_id)
    ip = device_info.get("ip")
    mac = device_info.get("mac")

    if not mac:
        return ExecutionResult(
            status="FAILURE",
            message=f"Cannot power on {entity_id}: no MAC address found. Ensure HomeKit controller is paired.",
            service="webos_power",
        )

    log.info(f"[webos] Sending WOL to {entity_id} (MAC={mac}, IP={ip})")
    try:
        _send_wol(mac, ip or "255.255.255.255")
        return ExecutionResult(
            status="SUCCESS",
            message=f"Sent WOL packet to {entity_id} (WebOS). TV should power on.",
            service="webos_power",
        )
    except Exception as e:
        return ExecutionResult(
            status="FAILURE",
            message=f"Failed to send WOL to {entity_id}: {e}",
            service="webos_power",
        )


async def power_off(ha_url: str, ha_token: str, entity_id: str) -> ExecutionResult:
    """Power off WebOS TV."""
    return await send_command(ha_url, ha_token, entity_id, "power_off")

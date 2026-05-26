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
import json
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

_send_wol  # pyright: ignore[reportUnusedExpression]


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


async def _get_webos_device_info(ha_url: str, ha_token: str, entity_id: str) -> dict:  # pyright: ignore[reportUnusedFunction]
    """Get WebOS device info (IP, MAC) via HomeKit diagnostics or device registry."""
    # noqa: F811 - kept for potential use
    import httpx
    import websockets
    headers = {"Authorization": f"Bearer {ha_token}"}
    state = await ha_client.get_state(ha_url, ha_token, entity_id)
    if not state:
        return {}

    friendly_name = state.get("attributes", {}).get("friendly_name", "")
    entity_lower = entity_id.lower()
    friendly_lower = friendly_name.lower()

    # Get device registry via WebSocket to find webostv device and its model
    webos_model = ""
    homekit_entry_ids = []
    ws_url = ha_url.replace("https://", "wss://").replace("http://", "ws://") + "/api/websocket"
    try:
        async with websockets.connect(ws_url, ssl=True) as ws:
            await ws.send('{"type": "auth", "access_token": "' + ha_token + '"}')
            await ws.recv()
            await ws.send('{"id": 1, "type": "config/device_registry/list"}')
            while True:
                resp = await ws.recv()
                data = json.loads(resp)
                if data.get("id") == 1:
                    if data.get("success"):
                        for dev in data.get("result", []):
                            dev_name = (dev.get("name") or "").lower()
                            dev_name_by_user = (dev.get("name_by_user") or "").lower()
                            dev_model = (dev.get("model") or "").lower()
                            
                            # Check if this is our webostv device
                            for identifier in dev.get("identifiers", []):
                                if identifier and identifier[0] == "webostv":
                                    if (any(part in dev_name for part in friendly_lower.split() if len(part) > 2) or
                                        any(part in dev_name_by_user for part in friendly_lower.split() if len(part) > 2) or
                                        any(part in dev_name for part in entity_lower.split(".") if len(part) > 2)):
                                        webos_model = dev_model
                                    break
                            
                            # Collect homekit_controller device entry IDs that share the same model
                            for identifier in dev.get("identifiers", []):
                                if identifier and identifier[0].startswith("homekit_controller"):
                                    if webos_model and dev_model == webos_model:
                                        for ce_id in dev.get("config_entries", []):
                                            homekit_entry_ids.append(ce_id)
                                    break
                    break
    except Exception as e:
        log.warning(f"[webos] Device registry WebSocket failed: {e}")

    if not webos_model:
        return {}

    # Get HomeKit diagnostics for matching entry IDs
    async with httpx.AsyncClient(verify=False, timeout=10) as client:
        for hk_entry_id in homekit_entry_ids:
            diag_resp = await client.get(f"{ha_url}/api/diagnostics/config_entry/{hk_entry_id}", headers=headers)
            if diag_resp.status_code != 200:
                continue
            diag_data = diag_resp.json()
            config_entry = diag_data.get("data", {}).get("config-entry", {})
            accessory_data = config_entry.get("data", {})
            accessory_ips = accessory_data.get("AccessoryIPs", [])
            pairing_id = accessory_data.get("AccessoryPairingID", "")

            if accessory_ips:
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


async def _find_homekit_entity(ha_url: str, ha_token: str, entity_id: str) -> str | None:
    """Find the HomeKit controller sibling for a webostv entity."""
    import httpx
    import websockets
    headers = {"Authorization": f"Bearer {ha_token}"}
    state = await ha_client.get_state(ha_url, ha_token, entity_id)
    if not state:
        return None

    friendly_name = state.get("attributes", {}).get("friendly_name", "")
    entity_lower = entity_id.lower()
    friendly_lower = friendly_name.lower()

    # Get webostv device model via WebSocket device registry
    webos_model = ""
    ws_url = ha_url.replace("https://", "wss://").replace("http://", "ws://") + "/api/websocket"
    try:
        async with websockets.connect(ws_url, ssl=True) as ws:
            await ws.send('{"type": "auth", "access_token": "' + ha_token + '"}')
            await ws.recv()
            await ws.send('{"id": 1, "type": "config/device_registry/list"}')
            while True:
                resp = await ws.recv()
                data = json.loads(resp)
                if data.get("id") == 1:
                    if data.get("success"):
                        for dev in data.get("result", []):
                            dev_name = (dev.get("name") or "").lower()
                            dev_name_by_user = (dev.get("name_by_user") or "").lower()
                            for identifier in dev.get("identifiers", []):
                                if identifier and identifier[0] == "webostv":
                                    if (any(part in dev_name for part in friendly_lower.split() if len(part) > 2) or
                                        any(part in dev_name_by_user for part in friendly_lower.split() if len(part) > 2) or
                                        any(part in dev_name for part in entity_lower.split(".") if len(part) > 2)):
                                        webos_model = (dev.get("model") or "").lower()
                                    break
                    break
    except Exception:
        pass

    if not webos_model:
        return None

    # Find HomeKit controller media_player with matching model
    async with httpx.AsyncClient(verify=False, timeout=10) as client:
        states = await ha_client.get_states(ha_url, ha_token)
        for s in states:
            eid = s.get("entity_id", "")
            if not eid.startswith("media_player."):
                continue
            attrs = s.get("attributes", {})
            fn = (attrs.get("friendly_name") or "").lower()
            # Check if this is a HomeKit entity matching our model
            if webos_model in fn or any(part in fn for part in friendly_lower.split() if len(part) > 2):
                # Verify it's homekit_controller via entity registry
                try:
                    r = await client.get(f"{ha_url}/api/config/entity_registry", headers=headers)
                    if r.status_code == 200:
                        for e in r.json():
                            if e.get("entity_id") == eid and e.get("platform") == "homekit_controller":
                                return eid
                except Exception:
                    pass
    return None


async def power_on(ha_url: str, ha_token: str, entity_id: str) -> ExecutionResult:
    """Power on WebOS TV via HomeKit controller or HA turn_on."""
    hk_entity = await _find_homekit_entity(ha_url, ha_token, entity_id)
    target = hk_entity or entity_id
    log.info(f"[webos] Powering on {entity_id} via {target}")
    result = await ha_client.call_service(
        ha_url, ha_token, "media_player", "turn_on", target, {}
    )
    if result.get("ok"):
        return ExecutionResult(
            status="SUCCESS",
            message=f"Sent power-on to {entity_id} (WebOS).",
            service="webos_power",
        )
    return ExecutionResult(
        status="FAILURE",
        message=f"Failed to power on {entity_id}: {result.get('error')}",
        service="webos_power",
    )


async def power_off(ha_url: str, ha_token: str, entity_id: str) -> ExecutionResult:
    """Power off WebOS TV via HomeKit controller or webostv command."""
    hk_entity = await _find_homekit_entity(ha_url, ha_token, entity_id)
    target = hk_entity or entity_id
    log.info(f"[webos] Powering off {entity_id} via {target}")
    result = await ha_client.call_service(
        ha_url, ha_token, "media_player", "turn_off", target, {}
    )
    if result.get("ok"):
        return ExecutionResult(
            status="SUCCESS",
            message=f"Sent power-off to {entity_id} (WebOS).",
            service="webos_power",
        )
    return ExecutionResult(
        status="FAILURE",
        message=f"Failed to power off {entity_id}: {result.get('error')}",
        service="webos_power",
    )

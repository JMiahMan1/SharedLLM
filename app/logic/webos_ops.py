
import logging
import asyncio
import requests
from settings import log, HA_URL
from logic.media_ops import execute_ha_service

async def ensure_device_on(entity_id: str, user_creds: dict):
    """
    Checks if device is off/unavailable and attempts to turn it on involved commands.
    """
    if not entity_id:
        return

    headers = {"Authorization": f"Bearer {user_creds.get('ha_token')}"}
    
    # 1. Fetch State
    try:
        url = f"{HA_URL.rstrip('/')}/api/states/{entity_id}"
        # Use sync request in blocking (simplification for now, ideal to use async client)
        # But since execute_ha_service uses requests internally via wrappers, we mimic that.
        # Ideally we should use run_blocking but for simplicity in this module:
        resp = requests.get(url, headers=headers, timeout=2.0)
        if resp.status_code == 200:
            state = resp.json().get("state")
            if state in ["off", "unavailable"]:
                log.info(f"[WebOS] Device {entity_id} is {state}. Attempting to Turn On...")
                # Call Turn On
                await execute_ha_service("media_player", "turn_on", entity_id, user_creds, {}, None)
                # Wait for boot (WebOS takes time ~5-10s, but we'll wait a bit)
                # Maybe loop check? For now fixed wait.
                log.info("[WebOS] Waiting 8s for device to wake...")
                await asyncio.sleep(8)
                
                # 2. Re-check State
                resp = requests.get(url, headers=headers, timeout=2.0)
                if resp.status_code == 200:
                    state = resp.json().get("state")
                    if state == "unavailable":
                         log.warning(f"[WebOS] Device {entity_id} still unavailable. Reloading Integration...")
                         await execute_ha_service("homeassistant", "reload_config_entry", entity_id, user_creds, {}, None)
                         
                         # Wait for availability (Polling)
                         for i in range(15): # Max 30s
                             log.info(f"[WebOS] Waiting for integration reload... ({i+1}/15)")
                             await asyncio.sleep(2)
                             try:
                                 r2 = requests.get(url, headers=headers, timeout=2.0)
                                 if r2.status_code == 200 and r2.json().get("state") not in ["unavailable", "unknown"]:
                                     log.info(f"[WebOS] Device {entity_id} is back online!")
                                     return
                             except:
                                 pass
    except Exception as e:
        log.warning(f"[WebOS] Failed to check state/reload for {entity_id}: {e}")


async def launch_app(entity_id: str, app_name: str, user_creds: dict, redis_client=None) -> dict:
    """
    Launches an app/source on WebOS TV.
    On WebOS, apps are treated as 'Sources'.
    """
    await ensure_device_on(entity_id, user_creds)
    
    log.info(f"[WebOS] Launching app/source '{app_name}' on {entity_id}")
    
    # We use select_source. The source name must match exactly or close enough?
    # WebOS integration usually exposes apps as sources.
    
    return await execute_ha_service(
        "media_player", "select_source", entity_id, user_creds,
        {"source": app_name},
        redis_client
    )

async def send_notification(entity_id: str, message: str, user_creds: dict, redis_client=None, icon: str = None) -> dict:
    """
    Sends a toast notification to the WebOS TV.
    Per HA docs, notify service uses friendly_name (e.g., notify.livingroom_tv).
    """
    await ensure_device_on(entity_id, user_creds)
    
    # Get friendly name from entity state
    headers = {"Authorization": f"Bearer {user_creds.get('ha_token')}"}
    url = f"{HA_URL.rstrip('/')}/api/states/{entity_id}"
    
    try:
        resp = requests.get(url, headers=headers, timeout=2.0)
        if resp.status_code == 200:
            friendly_name = resp.json().get("attributes", {}).get("friendly_name", "")
            # Convert to slug format: "Living Room TV" -> "livingroom_tv"
            service_name = friendly_name.lower().replace(" ", "_")
        else:
            # Fallback to entity_id slug
            service_name = entity_id.replace("media_player.", "")
    except:
        service_name = entity_id.replace("media_player.", "")
    
    domain = "notify"
    
    log.info(f"[WebOS] Notify {service_name}: {message}")
    
    data = {"message": message}
    if icon:
        data["data"] = {"icon": icon}
        
    return await execute_ha_service(
        domain, service_name, None, user_creds,
        data,
        redis_client
    )

async def play_channel(entity_id: str, channel: str, user_creds: dict, redis_client=None) -> dict:
    """
    Switches TV channel.
    Channel can be number or name.
    """
    await ensure_device_on(entity_id, user_creds)
    
    log.info(f"[WebOS] Switching to channel '{channel}' on {entity_id}")
    
    return await execute_ha_service(
        "media_player", "play_media", entity_id, user_creds,
        {"media_content_id": channel, "media_content_type": "channel"},
        redis_client
    )

async def control_device(entity_id: str, command: str, user_creds: dict, redis_client=None) -> dict:
    """
    Sends specific WebOS remote buttons.
    """
    cmd_map = {
        "left": "LEFT", "right": "RIGHT", "up": "UP", "down": "DOWN",
        "home": "HOME", "back": "BACK", "menu": "MENU", "enter": "ENTER", "select": "ENTER",
        "info": "INFO", "exit": "EXIT", "red": "RED", "green": "GREEN", "blue": "BLUE", "yellow": "YELLOW",
        "play": "PLAY", "pause": "PAUSE", 
        "volume up": "VOLUMEUP", "volume down": "VOLUMEDOWN", "mute": "MUTE",
        "channel up": "CHANNELUP", "channel down": "CHANNELDOWN",
        "0": "0", "1": "1", "2": "2", "3": "3", "4": "4", 
        "5": "5", "6": "6", "7": "7", "8": "8", "9": "9"
    }
    
    clean_cmd = command.lower().strip()
    
    # Handle Power / Std Transport
    if clean_cmd in ["turn_on", "turn_off", "toggle", "stop", "next", "previous"]:
        service_map = {
            "turn_on": "turn_on", "turn_off": "turn_off", "toggle": "toggle",
            "stop": "media_stop", "next": "media_next_track", "previous": "media_previous_track"
        }
        return await execute_ha_service(
            "media_player", service_map[clean_cmd], entity_id, user_creds, {}, redis_client
        )
    
    button = cmd_map.get(clean_cmd)
    if not button:
        return {"status": "FAILURE", "message": f"Unknown WebOS command: {command}"}
        
    log.info(f"[WebOS] Button '{button}' on {entity_id}")
    
    return await execute_ha_service(
        "webostv", "button", entity_id, user_creds,
        {"button": button},
        redis_client
    )

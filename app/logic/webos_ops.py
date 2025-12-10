
import logging
from settings import log
from logic.media_ops import execute_ha_service

async def launch_app(entity_id: str, app_name: str, user_creds: dict, redis_client=None) -> dict:
    """
    Launches an app/source on WebOS TV.
    On WebOS, apps are treated as 'Sources'.
    """
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
    Requires the 'notify' service corresponding to the TV.
    Usually 'notify.entity_name_slug'.
    """
    # Derive notify service from entity_id
    # e.g. media_player.living_room_tv -> notify.living_room_tv
    
    service_name = entity_id.replace("media_player.", "")
    domain = "notify"
    
    log.info(f"[WebOS] Notify {service_name}: {message}")
    
    data = {"message": message}
    if icon:
        data["data"] = {"icon": icon}
        
    return await execute_ha_service(
        domain, service_name, None, user_creds, # Entity ID is None for notify domain usually, but strict service call needs checking
        data,
        redis_client
    )

async def play_channel(entity_id: str, channel: str, user_creds: dict, redis_client=None) -> dict:
    """
    Switches TV channel.
    Channel can be number or name.
    """
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

"""
Light control operations and capability detection.
Handles smart home lighting devices with advanced capability detection.
"""

import logging
import re
from typing import Dict, List, Optional

log = logging.getLogger(__name__)

# Color name to RGB mapping
COLOR_MAP = {
    "red": [255, 0, 0],
    "green": [0, 255, 0],
    "blue": [0, 0, 255],
    "yellow": [255, 255, 0],
    "orange": [255, 165, 0],
    "purple": [128, 0, 128],
    "pink": [255, 192, 203],
    "white": [255, 255, 255],
    "warm white": [255, 220, 180],
    "cool white": [200, 220, 255],
    "cyan": [0, 255, 255],
    "magenta": [255, 0, 255],
}


async def detect_light_capabilities(entity_id: str, user_creds: dict, redis_client=None) -> Dict:
    """
    Detect capabilities of a light entity from Home Assistant.
    Uses multiple detection methods for robust capability identification.

    Returns dict with light capabilities:
    - has_brightness: bool
    - has_color_temp: bool
    - has_color: bool
    - color_modes: list
    - supported_features: int
    - brightness_range: tuple (min, max) if applicable
    """
    from app.logic.media_ops import get_device_capabilities

    # Use the existing get_device_capabilities function which has our enhanced logic
    capabilities = await get_device_capabilities(entity_id, user_creds, redis_client)

    # Add any light-specific enhancements here
    if capabilities.get("domain") == "light":
        # Additional light-specific processing can go here
        pass

    return capabilities


async def control_light_brightness(entity_id: str, brightness_percent: int, user_creds: dict, redis_client=None) -> Dict:
    """
    Set light brightness as a percentage (0-100).

    Args:
        entity_id: Light entity ID (e.g., 'light.piano_lamp')
        brightness_percent: Brightness level (0-100)
        user_creds: User credentials for HA API access
        redis_client: Optional Redis client for caching

    Returns:
        Dict with operation result
    """
    from app.logic.media_ops import execute_ha_service

    # Validate brightness range
    if not 0 <= brightness_percent <= 100:
        return {
            "status": "FAILURE",
            "message": f"Brightness must be between 0-100, got {brightness_percent}",
            "entity_id": entity_id,
            "service": "light.turn_on"
        }

    # Convert percentage to HA brightness (0-255)
    brightness_value = int((brightness_percent / 100) * 255)

    service_data = {
        "entity_id": entity_id,
        "brightness": brightness_value
    }

    result = await execute_ha_service("light", "turn_on", entity_id, user_creds, service_data, redis_client)

    if result.get("status") == "SUCCESS":
        result["brightness_percent"] = brightness_percent
        result["brightness_value"] = brightness_value

    return result


async def control_light_color_temp(entity_id: str, color_temp_kelvin: int, user_creds: dict, redis_client=None) -> Dict:
    """
    Set light color temperature in Kelvin.

    Args:
        entity_id: Light entity ID
        color_temp_kelvin: Color temperature in Kelvin (typically 2000-6500)
        user_creds: User credentials
        redis_client: Optional Redis client

    Returns:
        Dict with operation result
    """
    from app.logic.media_ops import execute_ha_service

    service_data = {
        "entity_id": entity_id,
        "color_temp_kelvin": color_temp_kelvin
    }

    result = await execute_ha_service("light", "turn_on", entity_id, user_creds, service_data, redis_client)

    if result.get("status") == "SUCCESS":
        result["color_temp_kelvin"] = color_temp_kelvin

    return result


async def control_light_rgb(entity_id: str, r: int, g: int, b: int, user_creds: dict, redis_client=None) -> Dict:
    """
    Set light RGB color values.

    Args:
        entity_id: Light entity ID
        r, g, b: RGB values (0-255)
        user_creds: User credentials
        redis_client: Optional Redis client

    Returns:
        Dict with operation result
    """
    from app.logic.media_ops import execute_ha_service

    # Validate RGB values
    for val, name in [(r, "red"), (g, "green"), (b, "blue")]:
        if not 0 <= val <= 255:
            return {
                "status": "FAILURE",
                "message": f"{name.capitalize()} value must be between 0-255, got {val}",
                "entity_id": entity_id,
                "service": "light.turn_on"
            }

    service_data = {
        "entity_id": entity_id,
        "rgb_color": [r, g, b]
    }

    result = await execute_ha_service("light", "turn_on", entity_id, user_creds, service_data, redis_client)

    if result.get("status") == "SUCCESS":
        result["rgb_color"] = [r, g, b]

    return result


async def turn_light_on(entity_id: str, user_creds: dict, redis_client=None) -> Dict:
    """
    Turn light on (without changing other settings).

    Args:
        entity_id: Light entity ID
        user_creds: User credentials
        redis_client: Optional Redis client

    Returns:
        Dict with operation result
    """
    from app.logic.media_ops import execute_ha_service

    return await execute_ha_service("light", "turn_on", entity_id, user_creds, {}, redis_client)


async def turn_light_off(entity_id: str, user_creds: dict, redis_client=None) -> Dict:
    """
    Turn light off.

    Args:
        entity_id: Light entity ID
        user_creds: User credentials
        redis_client: Optional Redis client

    Returns:
        Dict with operation result
    """
    from app.logic.media_ops import execute_ha_service

    return await execute_ha_service("homeassistant", "turn_off", entity_id, user_creds, {}, redis_client)


def parse_brightness_command(query: str) -> Optional[Dict]:
    """
    Parse natural language brightness commands.

    Examples:
    - "dim the piano lamp to 50%"
    - "set piano lamp brightness to 75"
    - "make the piano lamp brighter"

    Returns:
        Dict with 'entity_id' and 'brightness_percent' or None if not a brightness command
    """
    import re

    # Pattern for explicit percentage: "dim/set X to Y%"
    percent_pattern = r'(?:dim|set|make)\s+(.+?)\s+(?:to|at)\s+(\d+)%'
    match = re.search(percent_pattern, query.lower())
    if match:
        entity_name = match.group(1).strip()
        brightness = int(match.group(2))
        return {"entity_name": entity_name, "brightness_percent": brightness}

    # Pattern for "brighter/dimmer" relative commands
    relative_pattern = r'make\s+(.+?)\s+(brighter|dimmer)'
    match = re.search(relative_pattern, query.lower())
    if match:
        entity_name = match.group(1).strip()
        direction = match.group(2)
        # For now, return a placeholder - would need current brightness to calculate relative change
        return {"entity_name": entity_name, "relative_change": direction}

    return None


def parse_color_command(query: str) -> Optional[Dict]:
    """
    Parse natural language color commands.

    Examples:
    - "set piano lamp to red"
    - "make piano lamp blue"
    - "change piano lamp color to warm white"

    Returns:
        Dict with color command details or None
    """
    import re

    # Basic color patterns
    color_patterns = [
        (r'set\s+(.+?)\s+to\s+(red|green|blue|yellow|purple|orange|pink|white)', 'named_color'),
        (r'make\s+(.+?)\s+(red|green|blue|yellow|purple|orange|pink|white)', 'named_color'),
        (r'set\s+(.+?)\s+color\s+temp(?:erature)?\s+to\s+(\d+)\s*(?:k|kelvin)', 'color_temp'),
    ]

    for pattern, cmd_type in color_patterns:
        match = re.search(pattern, query.lower())
        if match:
            entity_name = match.group(1).strip()
            if cmd_type == 'named_color':
                color_name = match.group(2)
                return {"entity_name": entity_name, "color_type": "named", "color": color_name}
            elif cmd_type == 'color_temp':
                temp_k = int(match.group(2))
                return {"entity_name": entity_name, "color_type": "temperature", "temperature_k": temp_k}

    return None


async def handle_light_command(intent: str, query: str, entity_id: str, user_creds: dict, redis_client=None) -> List[Dict]:
    """
    Handle color and brightness control for lights.
    
    Args:
        intent: The intent (set_color, set_brightness, dim, brighten)
        query: The original query text
        entity_id: The light entity ID
        user_creds: User credentials for HA API
        redis_client: Optional Redis client for caching
    
    Returns:
        List of result dictionaries
    """
    from app.logic.media_ops import get_device_capabilities, execute_ha_service
    
    domain = entity_id.split('.')[0]
    if domain != "light":
        return [{"status": "FAILURE", "message": f"Color/brightness control only works with lights, not {domain} devices.", "entity_id": entity_id, "service": intent}]
    
    # Fetch device capabilities
    caps = await get_device_capabilities(entity_id, user_creds, redis_client)
    friendly_name = caps.get("friendly_name", entity_id.split('.')[-1].replace('_', ' ').title())
    
    service_data = {}
    
    # 1. Parse Brightness (Always check for brightness in any light command)
    if caps.get("has_brightness"):
        brightness = None
        # Support both % and "percent"
        pct_match = re.search(r"(\d+)\s*(?:%|percent)", query, re.IGNORECASE)
        if pct_match:
            pct = int(pct_match.group(1))
            brightness = int((pct / 100.0) * 255)
        elif intent == "dim":
            brightness = 51  # ~20%
        elif intent == "brighten":
            brightness = 255 # Max
            
        if brightness is not None:
            service_data["brightness"] = max(1, min(255, brightness))

    # 2. Parse Color (If set_color intent or color words found)
    color_found = None
    color_name_found = None
    if intent == "set_color" or any(c in query.lower() for c in COLOR_MAP.keys()):
        # Parse color from COLOR_MAP (find longest/best match)
        q_low = query.lower()
        sorted_colors = sorted(COLOR_MAP.items(), key=lambda x: len(x[0]), reverse=True)

        for color_name, rgb in sorted_colors:
            if color_name in q_low:
                color_found = rgb
                color_name_found = color_name
                break
        
        if color_found:
            color_modes = caps.get("color_modes", [])
            temp_map = {
                "warm white": 2700, "warm": 2700, "soft white": 3000, 
                "white": 4000, "cool white": 5000, "cool": 5000, "daylight": 5500
            }

            if color_name_found in temp_map and caps.get("has_color_temp"):
                service_data["color_temp_kelvin"] = temp_map[color_name_found]
            else:
                has_rgb_mode = any(mode.startswith("rgb") for mode in color_modes)
                if caps.get("has_color") and has_rgb_mode:
                    service_data["rgb_color"] = color_found
                elif caps.get("has_color") and "hs" in color_modes:
                    r, g, b = [x/255.0 for x in color_found]
                    max_c, min_c = max(r, g, b), min(r, g, b)
                    diff = max_c - min_c
                    h = (60 * ((g - b) / diff) + 360) % 360 if diff != 0 and max_c == r else \
                        (60 * ((b - r) / diff) + 120) % 360 if diff != 0 and max_c == g else \
                        (60 * ((r - g) / diff) + 240) % 360 if diff != 0 else 0
                    s = 0 if max_c == 0 else (diff / max_c) * 100
                    service_data["hs_color"] = [h, s]

    if not service_data:
        # Default behavior if nothing parsed but intent was set_brightness
        if intent in ["set_brightness", "dim", "brighten"]:
            service_data["brightness"] = 128
        else:
            return [{"status": "FAILURE", "message": "I couldn't determine the brightness or color you want.", "entity_id": entity_id, "service": intent}]
    
    log.info(f"[LIGHT] Executing turn_on on {entity_id} with data {service_data}")
    return [await execute_ha_service("light", "turn_on", entity_id, user_creds, service_data, redis_client)]

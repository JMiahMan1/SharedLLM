# app/domains/lighting/commands.py
"""
Light command handling and color/brightness control.
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
    from app.domains.media import get_device_capabilities
    from app.domains.shared import execute_ha_service

    domain = entity_id.split('.')[0]
    if domain != "light":
        return [{"status": "FAILURE", "message": f"Color/brightness control only works with lights, not {domain} devices.", "entity_id": entity_id, "service": intent}]

    # Fetch device capabilities
    caps = await get_device_capabilities(entity_id, user_creds, redis_client)
    friendly_name = caps.get("friendly_name", entity_id.split('.')[-1].replace('_', ' ').title())

    # Validate capability support
    if intent == "set_color":
        if not caps.get("has_color") and not caps.get("has_color_temp"):
            return [{
                "status": "FAILURE",
                "message": f"{friendly_name} doesn't support color control. It's a simple on/off or brightness-only light.",
                "entity_id": entity_id,
                "service": "set_color"
            }]

        # Parse color from COLOR_MAP
        color_found = None
        color_name_found = None
        q_low = query.lower()

        # Sort colors by length (longest first) to prefer "warm white" over "white"
        sorted_colors = sorted(COLOR_MAP.items(), key=lambda x: len(x[0]), reverse=True)

        for color_name, rgb in sorted_colors:
            if color_name in q_low:
                color_found = rgb
                color_name_found = color_name
                break

        if not color_found:
            return [{"status": "FAILURE", "message": "I couldn't determine which color you want. Try: red, blue, green, warm white, etc.", "entity_id": entity_id, "service": "set_color"}]

        # Smart mode selection based on device capabilities
        service = "turn_on"
        color_modes = caps.get("color_modes", [])

        # First, check if this is a temperature-based color (warm white, cool white, etc.)
        temp_map = {
            "warm white": 2700,
            "warm": 2700,
            "soft white": 3000,
            "white": 4000,
            "cool white": 5000,
            "cool": 5000,
            "daylight": 5500
        }

        if color_name_found in temp_map and caps.get("has_color_temp"):
            # Use color temperature for white variants
            service_data = {"color_temp_kelvin": temp_map[color_name_found]}
            log.info(f"Setting {entity_id} to {color_name_found} ({temp_map[color_name_found]}K)")

        else:
            # Check for RGB/HS color modes for actual colors
            # Check for any RGB variant (rgb, rgbw, rgbww)
            has_rgb_mode = any(mode.startswith("rgb") for mode in color_modes)
            if caps.get("has_color") and has_rgb_mode:
                # Full RGB color support (works for rgb, rgbw, rgbww modes)
                service_data = {"rgb_color": color_found}
                log.info(f"Setting {entity_id} to RGB {color_found}")

            elif caps.get("has_color") and "hs" in color_modes:
                # HS color mode (convert RGB to HS)
                r, g, b = [x/255.0 for x in color_found]
                max_c = max(r, g, b)
                min_c = min(r, g, b)
                diff = max_c - min_c

                # Hue calculation
                if diff == 0:
                    h = 0
                elif max_c == r:
                    h = (60 * ((g - b) / diff) + 360) % 360
                elif max_c == g:
                    h = (60 * ((b - r) / diff) + 120) % 360
                else:
                    h = (60 * ((r - g) / diff) + 240) % 360

                # Saturation calculation
                s = 0 if max_c == 0 else (diff / max_c) * 100

                service_data = {"hs_color": [h, s]}
                log.info(f"Setting {entity_id} to HS [{h:.1f}, {s:.1f}]")

            else:
                return [{"status": "FAILURE", "message": f"{friendly_name} doesn't support the requested color mode.", "entity_id": entity_id, "service": "set_color"}]

        log.info(f"[COLOR] Executing {domain}.{service} with data {service_data}")
        return [await execute_ha_service(domain, service, entity_id, user_creds, service_data, redis_client)]

    # Handle brightness commands
    elif intent in ["set_brightness", "dim", "brighten"]:
        if not caps.get("has_brightness"):
            return [{
                "status": "FAILURE",
                "message": f"{friendly_name} is an on/off only light and doesn't support brightness control.",
                "entity_id": entity_id,
                "service": "set_brightness"
            }]

        brightness = None

        # Parse brightness from query text
        log.debug(f"[BRIGHTNESS] Parsing brightness from query: '{query}'")
        # Support both % and "percent"
        pct_match = re.search(r"(\d+)\s*(?:%|percent)", query, re.IGNORECASE)
        if pct_match:
            pct = int(pct_match.group(1))
            brightness = int((pct / 100.0) * 255)
            log.debug(f"[BRIGHTNESS] Found percentage {pct}%, setting brightness to {brightness}")
        else:
            log.debug(f"[BRIGHTNESS] No percentage found in query")

        # Relative adjustments (only if no percentage found)
        if brightness is None:
            if intent == "dim":
                brightness = 51  # ~20% brightness for "dim" command
            elif intent == "brighten":
                brightness = 255  # Max brightness

        if brightness is None:
            brightness = 128  # Default to 50%

        service = "turn_on"
        service_data = {"brightness": max(1, min(255, brightness))}
        log.info(f"Setting {entity_id} brightness to {brightness} (from intent {intent})")

        return [await execute_ha_service(domain, service, entity_id, user_creds, service_data, redis_client)]

    else:
        return [{"status": "FAILURE", "message": f"Unsupported light command: {intent}", "entity_id": entity_id, "service": intent}]

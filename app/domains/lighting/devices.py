# app/domains/lighting/devices.py
"""
Light device capability detection and parsing utilities.
"""

import logging
import re
from typing import Dict, Optional

log = logging.getLogger(__name__)


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


def parse_brightness_command(query: str) -> Optional[Dict]:
    """
    Parse natural language brightness commands.

    Examples:
    - "dim the piano lamp to 50%"
    - "set piano lamp brightness to 75"
    - "make the piano lamp brighter"

    Returns:
        Dict with 'entity_name' and 'brightness_percent' or None if not a brightness command
    """
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

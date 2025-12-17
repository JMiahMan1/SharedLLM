# app/domains/lighting/__init__.py
"""
Lighting domain - handles light control, color management, and lighting operations.
"""

from .commands import handle_light_command
from .devices import detect_light_capabilities, parse_brightness_command, parse_color_command

# Color constants
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

__all__ = [
    # Main command handler
    "handle_light_command",

    # Device capabilities
    "detect_light_capabilities",

    # Command parsing
    "parse_brightness_command",
    "parse_color_command",

    # Constants
    "COLOR_MAP",
]

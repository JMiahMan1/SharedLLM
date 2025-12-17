# app/domains/media/__init__.py
"""
Media domain - handles media playback, device control, and media-related operations.
"""

from .commands import handle_media_command
from .devices import (
    get_device_capabilities, get_active_media_players, get_available_media_players,
    smart_resolve_entity, get_last_entity, get_last_media_entity
)
from .integrations import APP_PACKAGES, MEDIA_INTENTS, REGEX_INTENT_MAP

__all__ = [
    # Main command handler
    "handle_media_command",

    # Device management
    "get_device_capabilities",
    "get_active_media_players",
    "get_available_media_players",
    "smart_resolve_entity",
    "get_last_entity",
    "get_last_media_entity",

    # Constants and mappings
    "APP_PACKAGES",
    "MEDIA_INTENTS",
    "REGEX_INTENT_MAP",
]

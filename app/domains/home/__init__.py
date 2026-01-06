# app/domains/home/__init__.py
"""
Home domain - handles general Home Assistant operations and device control.
"""

from .commands import handle_home_command
from .devices import get_entity_state

__all__ = [
    # Main command handler
    "handle_home_command",

    # Device utilities
    "get_entity_state",
]

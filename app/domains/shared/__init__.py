# app/domains/shared/__init__.py
"""
Shared utilities and base functions for all domains.
"""

from .ha_service import execute_ha_service

__all__ = [
    "execute_ha_service",
]

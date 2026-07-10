# services/gateway/media_device_cache.py
"""
Redis-backed cache for last-used media devices.

Tracks which media player was last used per user, so commands like "pause"
or "play music" can target the correct device without explicit naming.

The Redis connection and TTL handling are now shared via
``services.gateway.cache`` (see ``redis_cache_*`` / ``MEDIA_DEVICE_CACHE_TTL``).
"""
import json
import logging
from datetime import UTC, datetime

from services.gateway.cache import (
    MEDIA_DEVICE_CACHE_TTL,
    redis_cache_delete,
    redis_cache_get,
    redis_cache_set,
)

log = logging.getLogger("gateway.media_device_cache")


def _user_key(user_id: str) -> str:
    return f"media:last_used:{user_id}"


def get_last_used_device(user_id: str) -> dict | None:
    """Return cached last-used device info or None."""
    raw = redis_cache_get(_user_key(user_id))
    if raw:
        try:
            return json.loads(raw)
        except Exception:
            return None
    return None


def set_last_used_device(user_id: str, entity_id: str, friendly_name: str = "", state: str = "") -> None:
    """Cache the last-used media device with timestamp."""
    data = {
        "entity_id": entity_id,
        "friendly_name": friendly_name,
        "state": state,
        "updated_at": datetime.now(UTC).isoformat(),
    }
    redis_cache_set(_user_key(user_id), json.dumps(data), MEDIA_DEVICE_CACHE_TTL)
    log.info(f"[media_cache] Cached last-used device for {user_id}: {entity_id}")


def clear_last_used_device(user_id: str) -> None:
    """Remove cached last-used device."""
    redis_cache_delete(_user_key(user_id))
    log.info(f"[media_cache] Cleared last-used device for {user_id}")

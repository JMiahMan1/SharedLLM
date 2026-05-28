# services/gateway/media_device_cache.py
"""
Redis-backed cache for last-used media devices.

Tracks which media player was last used per user, so commands like "pause"
or "play music" can target the correct device without explicit naming.
"""
import logging
import json
import redis
from typing import Optional
from datetime import datetime, timezone

log = logging.getLogger("gateway.media_device_cache")

_redis: redis.Redis | None = None
_TTL = 86400 * 7  # 7 days


def get_redis() -> redis.Redis:
    global _redis
    if _redis is None:
        from services.gateway.config import REDIS_URL
        _redis = redis.from_url(REDIS_URL, decode_responses=True)
    return _redis


def _user_key(user_id: str) -> str:
    return f"media:last_used:{user_id}"


def get_last_used_device(user_id: str) -> Optional[dict]:
    """Return cached last-used device info or None."""
    try:
        r = get_redis()
        raw: str | None = r.get(_user_key(user_id))  # type: ignore[assignment]
        if raw:
            return json.loads(raw)
    except Exception as e:
        log.error(f"Media device cache read error: {e}")
    return None


def set_last_used_device(user_id: str, entity_id: str, friendly_name: str = "", state: str = "") -> None:
    """Cache the last-used media device with timestamp."""
    try:
        r = get_redis()
        data = {
            "entity_id": entity_id,
            "friendly_name": friendly_name,
            "state": state,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        r.setex(_user_key(user_id), _TTL, json.dumps(data))
        log.info(f"[media_cache] Cached last-used device for {user_id}: {entity_id}")
    except Exception as e:
        log.error(f"Media device cache write error: {e}")


def clear_last_used_device(user_id: str) -> None:
    """Remove cached last-used device."""
    try:
        r = get_redis()
        r.delete(_user_key(user_id))
        log.info(f"[media_cache] Cleared last-used device for {user_id}")
    except Exception as e:
        log.error(f"Media device cache delete error: {e}")

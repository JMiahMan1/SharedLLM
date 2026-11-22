"""Shared in-memory TTL caches (Phase 3).

Consolidates the previously-duplicated per-module settings caches so that
``get_all_settings`` and ``resolve_identity`` share one store, and so a single
invalidation call can drop cached identity/settings after a write.

Caches are process-local and TTL-bounded:
- ``SETTINGS_CACHE_TTL`` / ``IDENTITY_CACHE_TTL`` (env-configurable, default 30s).
- The TTL bounds the exposure window of cached identity (which includes tokens)
  and guarantees eventual consistency even if an invalidation call is missed.
"""
import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

import redis as _redis_lib

from services.gateway.config import (  # noqa: F401
    HA_STATE_CACHE_TTL,
    IDENTITY_CACHE_TTL,
    MEDIA_DEVICE_CACHE_TTL,
    SETTINGS_CACHE_TTL,
)

log = logging.getLogger("gateway.cache")

# --- Global settings cache ---
_settings_cache: dict[str, str] | None = None
_settings_cache_time: float = 0.0

# --- Identity cache (keyed by api_key / token / user_id) ---
_identity_cache: dict[str, Any] = {}
_identity_cache_time: dict[str, float] = {}
#: key -> the resolution already on its way to identity, which concurrent callers share.
_identity_inflight: dict[str, asyncio.Future] = {}


async def get_cached_settings(
    fetcher: Callable[[], Awaitable[dict[str, str]]],
    fallback: dict[str, str],
) -> dict[str, str]:
    """Return cached global settings, refreshing via ``fetcher`` when expired.

    On fetch failure the stale cache is served (if any); otherwise ``fallback``
    (typically defaults). The cache is NOT overwritten with fallback data, so a
    transient Identity outage does not mask itself for a full TTL.
    """
    global _settings_cache, _settings_cache_time
    now = time.time()
    if _settings_cache is not None and (now - _settings_cache_time) < SETTINGS_CACHE_TTL:
        return _settings_cache
    try:
        fresh = await fetcher()
    except Exception as e:
        log.warning(f"Settings fetch failed ({e}); serving stale cache or defaults")
        return _settings_cache if _settings_cache is not None else fallback
    _settings_cache = fresh
    _settings_cache_time = now
    return fresh


def invalidate_settings() -> None:
    """Drop the cached global settings (call after any settings write)."""
    global _settings_cache, _settings_cache_time
    _settings_cache = None
    _settings_cache_time = 0.0


def _identity_key(body: dict) -> str:
    return str(body.get("api_key") or body.get("token") or body.get("user_id") or body)


async def get_cached_identity(
    body: dict,
    fetcher: Callable[[], Awaitable[Any]],
) -> Any:
    """Return cached resolved identity for ``body``, refreshing when expired.

    Only successful resolutions are cached; failures are never stored. Concurrent misses for
    the same key share one fetch: a page load sends many requests at once, and each one used
    to ask identity separately, which made identity do first-use work (such as making a
    person's Music Assistant account) several times in the same instant.
    """
    key = _identity_key(body)
    now = time.time()
    cached = _identity_cache.get(key)
    if cached is not None and (now - _identity_cache_time.get(key, 0.0)) < IDENTITY_CACHE_TTL:
        return cached
    pending = _identity_inflight.get(key)
    if pending is not None and not pending.done() and pending.get_loop() is asyncio.get_running_loop():
        try:
            return await asyncio.shield(pending)
        except asyncio.CancelledError:
            if not pending.cancelled():
                raise  # this caller was cancelled, not the shared fetch
            # The caller that was fetching went away; fetch for ourselves below.
    future: asyncio.Future = asyncio.get_running_loop().create_future()
    _identity_inflight[key] = future
    try:
        fresh = await fetcher()
    except asyncio.CancelledError:
        future.cancel()
        raise
    except BaseException as exc:
        future.set_exception(exc)
        future.exception()  # retrieved: no "never retrieved" warning when nobody else waited
        raise
    finally:
        if _identity_inflight.get(key) is future:
            del _identity_inflight[key]
    _identity_cache[key] = fresh
    _identity_cache_time[key] = now
    future.set_result(fresh)
    return fresh


def invalidate_identity(api_key: str | None = None) -> None:
    """Drop cached identity (call on logout / password change).

    With no argument, clears the entire identity cache.
    """
    if api_key is None:
        _identity_cache.clear()
        _identity_cache_time.clear()
        _identity_inflight.clear()
    else:
        _identity_cache.pop(api_key, None)
        _identity_cache_time.pop(api_key, None)
        _identity_inflight.pop(api_key, None)


# --- Shared Redis-backed cache (per-entity / per-user live state) ---
# Consolidates the previously-duplicated per-module Redis connections in
# ha_state_cache.py / media_device_cache.py into one connection + one helper
# set. The backing store stays Redis (shared across instances, survives
# restarts); only the connection and TTL handling are centralized.
_redis: "_redis_lib.Redis | None" = None


def get_redis() -> "_redis_lib.Redis":
    """Return a shared Redis connection (decode_responses=True)."""
    global _redis
    if _redis is None:
        from services.gateway.config import REDIS_URL
        # Synchronous and called from async handlers: without a timeout a hung
        # Redis would freeze the whole gateway.
        _redis = _redis_lib.from_url(REDIS_URL, decode_responses=True, socket_timeout=5, socket_connect_timeout=5)
    return _redis


def redis_cache_get(key: str) -> str | None:
    """Return a cached string value or None on miss/error."""
    try:
        val = get_redis().get(key)
        return str(val) if val is not None else None
    except Exception as e:
        log.warning(f"Redis cache read error: {e}")
        return None


def redis_cache_set(key: str, value: str, ttl: float) -> None:
    """Cache a string value with the given TTL (seconds)."""
    try:
        get_redis().setex(key, int(ttl), value)
    except Exception as e:
        log.warning(f"Redis cache write error: {e}")


def redis_cache_set_many(items: dict[str, str], ttl: float) -> int:
    """Bulk-cache key/value pairs via a pipeline. Returns count cached."""
    try:
        r = get_redis()
        pipe = r.pipeline()
        count = 0
        for k, v in items.items():
            pipe.setex(k, int(ttl), v)
            count += 1
        if count:
            pipe.execute()
        return count
    except Exception as e:
        log.warning(f"Redis bulk cache error: {e}")
        return 0


def redis_cache_delete(key: str) -> None:
    """Remove a cached key."""
    try:
        get_redis().delete(key)
    except Exception as e:
        log.warning(f"Redis cache delete error: {e}")

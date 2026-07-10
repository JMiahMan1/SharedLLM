"""Shared in-memory TTL caches (Phase 3).

Consolidates the previously-duplicated per-module settings caches so that
``get_all_settings`` and ``resolve_identity`` share one store, and so a single
invalidation call can drop cached identity/settings after a write.

Caches are process-local and TTL-bounded:
- ``SETTINGS_CACHE_TTL`` / ``IDENTITY_CACHE_TTL`` (env-configurable, default 30s).
- The TTL bounds the exposure window of cached identity (which includes tokens)
  and guarantees eventual consistency even if an invalidation call is missed.
"""
import logging
import time
from typing import Any, Awaitable, Callable

from services.gateway.config import IDENTITY_CACHE_TTL, SETTINGS_CACHE_TTL

log = logging.getLogger("gateway.cache")

# --- Global settings cache ---
_settings_cache: dict[str, str] | None = None
_settings_cache_time: float = 0.0

# --- Identity cache (keyed by api_key / token / user_id) ---
_identity_cache: dict[str, Any] = {}
_identity_cache_time: dict[str, float] = {}


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

    Only successful resolutions are cached; failures are never stored.
    """
    key = _identity_key(body)
    now = time.time()
    cached = _identity_cache.get(key)
    if cached is not None and (now - _identity_cache_time.get(key, 0.0)) < IDENTITY_CACHE_TTL:
        return cached
    fresh = await fetcher()
    _identity_cache[key] = fresh
    _identity_cache_time[key] = now
    return fresh


def invalidate_identity(api_key: str | None = None) -> None:
    """Drop cached identity (call on logout / password change).

    With no argument, clears the entire identity cache.
    """
    if api_key is None:
        _identity_cache.clear()
        _identity_cache_time.clear()
    else:
        _identity_cache.pop(api_key, None)
        _identity_cache_time.pop(api_key, None)

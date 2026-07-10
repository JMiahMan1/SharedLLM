# services/gateway/ha_state_cache.py
"""
Redis-backed cache for Home Assistant entity states.

RAG stores static entity metadata (area, device_class, capabilities).
Redis caches live state with a short TTL.
entity_id is the stable join key between the two.

The Redis connection and TTL handling are now shared via
``services.gateway.cache`` (see ``redis_cache_*`` / ``HA_STATE_CACHE_TTL``).
"""
import logging

import aiohttp

from services.gateway.cache import (
    HA_STATE_CACHE_TTL,
    get_redis,
    redis_cache_get,
    redis_cache_set,
    redis_cache_set_many,
)

log = logging.getLogger("gateway.ha_state_cache")

# Re-exported so existing callers (background_worker, main) keep working.
__all__ = [
    "get_redis",
    "get_cached_state",
    "set_cached_state",
    "cache_all_states",
    "fetch_live_states",
    "get_live_state",
]


def _key(entity_id: str) -> str:
    return f"ha:state:{entity_id}"


def get_cached_state(entity_id: str) -> str | None:
    """Return cached state or None if missing/expired."""
    return redis_cache_get(_key(entity_id))


def set_cached_state(entity_id: str, state: str) -> None:
    """Cache entity state with TTL."""
    redis_cache_set(_key(entity_id), state, HA_STATE_CACHE_TTL)


def cache_all_states(entities: list[dict]) -> int:
    """Bulk cache all entity states. Returns count cached."""
    items = {
        _key(e["entity_id"]): e["state"]
        for e in entities
        if e.get("entity_id") and e.get("state")
    }
    return redis_cache_set_many(items, HA_STATE_CACHE_TTL)

async def fetch_live_states(execution_url: str, ha_url: str, ha_token: str, internal_secret: str) -> list[dict]:
    """Fetch all states from HA via execution service and cache them."""
    try:
        from services.gateway.main import shared_http_client
        async with shared_http_client() as client:
            resp = await client.get(
                f"{execution_url}/discovery/entities",
                params={"ha_url": ha_url, "ha_token": ha_token},
                headers={"X-Internal-Secret": internal_secret},
                timeout=aiohttp.ClientTimeout(total=10.0),
            )
            if resp.status == 200:
                data = await resp.json()
                entities = data.get("entities", []) if isinstance(data, dict) else []
                cache_all_states(entities)
                return entities
    except Exception as e:
        log.error(f"Failed to fetch live HA states: {e}")
    return []

async def get_live_state(entity_id: str, execution_url: str, ha_url: str, ha_token: str, internal_secret: str) -> str | None:
    """Get state from cache, or fetch live and cache it."""
    cached = get_cached_state(entity_id)
    if cached is not None:
        return cached

    # Cache miss — fetch live (reuse the gateway's shared pooled client)
    try:
        from services.gateway.main import get_http_client

        client = get_http_client()
        async with client.get(
            f"{execution_url}/discovery/entities",
            params={"ha_url": ha_url, "ha_token": ha_token},
            headers={"X-Internal-Secret": internal_secret},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                entities = data.get("entities", []) if isinstance(data, dict) else []
                cache_all_states(entities)
                for e in entities:
                    if e.get("entity_id") == entity_id:
                        return e.get("state")
    except Exception as e:
        log.error(f"Failed to fetch live state for {entity_id}: {e}")
    return None

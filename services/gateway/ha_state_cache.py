# services/gateway/ha_state_cache.py
"""
Redis-backed cache for Home Assistant entity states.

RAG stores static entity metadata (area, device_class, capabilities).
Redis caches live state with a short TTL.
entity_id is the stable join key between the two.
"""
import logging
import aiohttp
import redis

log = logging.getLogger("gateway.ha_state_cache")

_redis: redis.Redis | None = None
_TTL = 60  # Cache states for 60 seconds

def get_redis() -> redis.Redis:
    global _redis
    if _redis is None:
        from services.gateway.config import REDIS_URL
        _redis = redis.from_url(REDIS_URL, decode_responses=True)
    return _redis

def _key(entity_id: str) -> str:
    return f"ha:state:{entity_id}"

def get_cached_state(entity_id: str) -> str | None:
    """Return cached state or None if missing/expired."""
    try:
        r = get_redis()
        val = r.get(_key(entity_id))  # type: ignore[assignment]
        if val is None:
            return None
        return str(val)
    except Exception as e:
        log.error(f"Redis cache read error: {e}")
        return None

def set_cached_state(entity_id: str, state: str) -> None:
    """Cache entity state with TTL."""
    try:
        r = get_redis()
        r.setex(_key(entity_id), _TTL, state)
    except Exception as e:
        log.error(f"Redis cache write error: {e}")

def cache_all_states(entities: list[dict]) -> int:
    """Bulk cache all entity states. Returns count cached."""
    try:
        r = get_redis()
        pipe = r.pipeline()
        count = 0
        for e in entities:
            eid = e.get("entity_id")
            state = e.get("state")
            if eid and state:
                pipe.setex(_key(eid), _TTL, state)
                count += 1
        if count:
            pipe.execute()
        return count
    except Exception as e:
        log.error(f"Redis bulk cache error: {e}")
        return 0

async def fetch_live_states(execution_url: str, ha_url: str, ha_token: str, internal_secret: str) -> list[dict]:
    """Fetch all states from HA via execution service and cache them."""
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10.0)) as client:
            resp = await client.get(
                f"{execution_url}/discovery/entities",
                params={"ha_url": ha_url, "ha_token": ha_token},
                headers={"X-Internal-Secret": internal_secret}
            )
            if resp.status == 200:
                data = await resp.json()
                entities = data.get("entities", []) if isinstance(data, dict) else []
                cache_all_states(entities)
                return entities
    except Exception as e:
        log.error(f"Failed to fetch live HA states: {e}")
    return []

def get_live_state(entity_id: str, execution_url: str, ha_url: str, ha_token: str, internal_secret: str) -> str | None:
    """Get state from cache, or fetch live and cache it."""
    cached = get_cached_state(entity_id)
    if cached is not None:
        return cached
    
    # Cache miss — fetch live
    try:
        import requests
        resp = requests.get(
            f"{execution_url}/discovery/entities",
            params={"ha_url": ha_url, "ha_token": ha_token},
            headers={"X-Internal-Secret": internal_secret},
            timeout=10
        )
        if resp.status == 200:
            data = resp.json()
            entities = data.get("entities", []) if isinstance(data, dict) else []
            cache_all_states(entities)
            for e in entities:
                if e.get("entity_id") == entity_id:
                    return e.get("state")
    except Exception as e:
        log.error(f"Failed to fetch live state for {entity_id}: {e}")
    return None

# services/gateway/history.py
import os
import json
import redis
import logging

log = logging.getLogger("gateway.history")

# Configuration
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
_redis = redis.from_url(REDIS_URL, decode_responses=True)

def _get_history_key(user: str) -> str:
    return f"rag:history:{user}"

async def get_history(user_id: str) -> list:
    """Retrieves conversation history as a list of dicts."""
    try:
        key = _get_history_key(user_id)
        raw_msgs = _redis.lrange(key, 0, -1)
        if not raw_msgs: return []
        
        msgs = []
        for m in raw_msgs:
            try:
                msgs.append(json.loads(m))
            except Exception as e:
                log.debug(f"Failed to parse history message: {e}")
        return msgs
    except Exception as e:
        log.warning(f"History read error: {e}")
        return []

async def update_history(user_id: str, role: str, content: str):
    """Saves a message to Redis history."""
    if not content: return
    try:
        key = _get_history_key(user_id)
        msg = json.dumps({"role": role, "content": content})
        _redis.rpush(key, msg)
        _redis.ltrim(key, -10, -1) # Keep last 10 msgs (5 turns)
        _redis.expire(key, 3600 * 2) # 2 hour TTL
    except Exception as e:
        log.warning(f"History write error: {e}")

def ping_redis() -> bool:
    """Verifies Redis connectivity for health checks."""
    try:
        return _redis.ping()
    except:
        return False

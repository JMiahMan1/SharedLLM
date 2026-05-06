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
                data = json.loads(m)
                if isinstance(data, dict):
                    msgs.append(data)
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

async def get_long_term_memory(user_id: str, query: str) -> str:
    """
    Retrieves relevant 'User Facts' from the RAG service to provide semantic memory.
    """
    try:
        # Call RAG service to find facts for this user
        from main import call_rag_service # Potential circular import, handle with care or move
        res = await call_rag_service(
            method="POST",
            path="/rag/search",
            payload={
                "collection_name": "user_facts",
                "query": query,
                "user_id": user_id,
                "k": 3
            }
        )
        facts = res.get("results", [])
        if not facts:
            return ""
        
        context = "\n".join([f"- {f['content']}" for f in facts])
        return f"### User Preferences & Long-Term Memory\n{context}\n"
    except Exception as e:
        log.warning(f"Failed to retrieve long-term memory: {e}")
        return ""

def ping_redis() -> bool:
    """Verifies Redis connectivity for health checks."""
    try:
        return _redis.ping()
    except:
        return False

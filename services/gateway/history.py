# services/gateway/history.py
import os
import json
import redis
import logging
import time
import httpx
import asyncio

log = logging.getLogger("gateway.history")

# INTERNAL_SECRET sourced from config.py which enforces fail-secure at gateway startup.
try:
    from .config import IDENTITY_SVC, RAG_SVC, INTERNAL_SECRET, REDIS_URL
except (ImportError, ValueError):
    from config import IDENTITY_SVC, RAG_SVC, INTERNAL_SECRET, REDIS_URL

_redis = redis.from_url(REDIS_URL, decode_responses=True)

def _get_history_key(user: str) -> str:
    return f"rag:history:{user}"

async def fetch_librarian_model() -> str:
    """Fetches the designated Librarian model from Identity Service GlobalSettings."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{IDENTITY_SVC}/api/settings/librarian_model",
                headers={"X-Internal-Secret": INTERNAL_SECRET}
            )
            if resp.status_code == 200:
                data = resp.json()
                return data.get("value", "")
    except Exception as e:
        log.warning(f"Failed to fetch librarian_model: {e}")
    return "" # Safe fallback

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
        # We use a dedicated collection for static user facts (Mem0 style)
        payload = {
            "collection_name": "user_facts",
            "query": query,
            "user_id": user_id,
            "k": 5
        }
        
        from .config import RAG_SVC, INTERNAL_SECRET

        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                f"{RAG_SVC}/rag/search",
                json=payload,
                headers={"X-Internal-Secret": secret}
            )
            if resp.status_code != 200:
                return ""

            data = resp.json()
            facts = data.get("results", [])
            if not facts:
                return ""

            context = "\n".join([f"- {f['content']}" for f in facts])
            return f"### User Preferences & Facts (Long-term Memory)\n{context}\n"
    except Exception as e:
        log.warning(f"Failed to retrieve long-term memory: {e}")
        return ""

async def extract_and_store_user_facts(user_id: str, history: list):
    """
    Asynchronous task to extract static facts from conversation and store in RAG.
    """
    if not history or len(history) < 2:
        return

    try:
        LIBRARIAN_MODEL = await fetch_librarian_model()
        from .config import OLLAMA_URL

        # Only look at the last turn
        recent_text = ""
        for m in history[-2:]:
            role = "USER" if m.get("role") == "user" else "ASSISTANT"
            recent_text += f"{role}: {m.get('content')}\n"

        prompt = f"""Extract 1-3 permanent user facts, preferences, or life details from this exchange.
Examples: 'User has a dog named Rex', 'User prefers dark mode', 'User is a software engineer'.
Ignore temporary states like 'User is hungry'.
Conversation:
{recent_text}

Return ONLY a bulleted list of facts, or 'NONE'.
"""
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{OLLAMA_URL}/api/generate",
                json={"model": LIBRARIAN_MODEL, "prompt": prompt, "stream": False},
            )
            if resp.status_code != 200: return
            
            text = resp.json().get("response", "").strip()
            if "NONE" in text.upper() or not text:
                return

            facts = [f.strip("- ").strip() for f in text.split("\n") if f.strip("- ").strip()]
            for f in facts:
                if len(f) < 5: continue
                await client.post(
                    f"{RAG_SVC}/rag/ingest",
                    json={
                        "collection_name": "user_facts",
                        "content": f,
                        "metadata": {"type": "user_fact", "timestamp": time.time()},
                        "user_id": user_id
                    },
                    headers={"X-Internal-Secret": INTERNAL_SECRET}
                )
                log.info(f"[Mem0] Extracted fact for {user_id}: {f}")
    except Exception as e:
        log.error(f"Fact extraction failed: {e}")

def ping_redis() -> bool:
    """Verifies Redis connectivity for health checks."""
    try:
        return _redis.ping()
    except:
        return False

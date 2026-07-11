# services/gateway/history.py
import json
import logging
import time

import aiohttp
import redis

# INTERNAL_SECRET sourced from config.py which enforces fail-secure at gateway startup.
from services.gateway.config import INTERNAL_SECRET

log = logging.getLogger("gateway.history")

_redis = None

def get_redis():
    global _redis
    if _redis is None:
        from services.gateway.config import REDIS_URL
        if not REDIS_URL:
            raise RuntimeError("REDIS_URL not configured")
        _redis = redis.from_url(REDIS_URL, decode_responses=True)
    return _redis

def _get_history_key(user: str) -> str:
    return f"rag:history:{user}"

async def fetch_librarian_model() -> str:
    """Fetches the designated Librarian model from Identity Service GlobalSettings."""
    from services.gateway.orchestrator import _get, get_all_settings
    settings = await get_all_settings()
    return _get(settings, "ollama_librarian_model", "")

async def get_history(user_id: str) -> list:
    """Retrieves conversation history as a list of dicts."""
    try:
        key = _get_history_key(user_id)
        r = get_redis()
        raw_msgs: list = await r.lrange(key, 0, -1)  # type: ignore[misc]
        if not raw_msgs:
            return []

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
    if not content:
        return
    try:
        key = _get_history_key(user_id)
        msg = json.dumps({"role": role, "content": content})
        r = get_redis()
        r.rpush(key, msg)
        r.ltrim(key, -10, -1) # Keep last 10 msgs (5 turns)
        r.expire(key, 3600 * 2) # 2 hour TTL
    except Exception as e:
        log.warning(f"History write error: {e}")

async def get_long_term_memory(user_id: str, query: str) -> str:
    """
    Retrieves relevant 'User Facts' from the RAG service to provide semantic memory.
    """
    from services.gateway.main import shared_http_client
    from services.gateway.orchestrator import _get, get_all_settings
    settings = await get_all_settings()
    rag_svc = _get(settings, "rag_svc_url")
    secret = _get(settings, "internal_secret", INTERNAL_SECRET)
    try:
        # We use a dedicated collection for static user facts (Mem0 style)
        payload = {
            "collection_name": "user_facts",
            "query": query,
            "user_id": user_id,
            "k": 5
        }

        async with shared_http_client() as client:
            resp = await client.post(
                f"{rag_svc}/rag/search",
                json=payload,
                headers={"X-Internal-Secret": secret},
                timeout=aiohttp.ClientTimeout(total=5.0),
            )
            if resp.status != 200:
                return ""

            data = await resp.json()
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
        from services.gateway.main import shared_http_client
        from services.gateway.orchestrator import _get, get_all_settings
        settings = await get_all_settings()
        LIBRARIAN_MODEL = _get(settings, "ollama_librarian_model") or _get(settings, "librarian_model") or _get(settings, "assistant_model")
        if not LIBRARIAN_MODEL:
            return  # No model configured; skip fact extraction silently
        ollama_url = _get(settings, "llm_local_url")
        if not ollama_url:
            raise RuntimeError("Ollama URL not configured in Identity settings. Set llm_local_url in Identity settings.")
        rag_svc = _get(settings, "rag_svc_url")

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
        async with shared_http_client() as client:
            resp = await client.post(
                f"{ollama_url}/api/generate",
                json={"model": LIBRARIAN_MODEL, "prompt": prompt, "stream": False},
                timeout=aiohttp.ClientTimeout(total=60.0),
            )
            if resp.status != 200:
                return

            data = await resp.json()
            text = data.get("response", "").strip()
            if "NONE" in text.upper() or not text:
                return

            facts = [f.strip("- ").strip() for f in text.split("\n") if f.strip("- ").strip()]
            for f in facts:
                if len(f) < 5:
                    continue
                await client.post(
                    f"{rag_svc}/rag/ingest",
                    json={
                        "collection_name": "user_facts",
                        "content": f,
                        "metadata": {"type": "user_fact", "timestamp": time.time()},
                        "user_id": user_id
                    },
                    headers={"X-Internal-Secret": INTERNAL_SECRET},
                    timeout=aiohttp.ClientTimeout(total=60.0),
                )
                log.info(f"[Mem0] Extracted fact for {user_id}: {f}")
    except Exception as e:
        log.error(f"Fact extraction failed: {e}")

def ping_redis() -> bool:
    """Verifies Redis connectivity for health checks."""
    try:
        r = get_redis()
        result = r.ping()
        return bool(result)
    except Exception:
        return False

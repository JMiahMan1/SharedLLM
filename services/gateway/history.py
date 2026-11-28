# services/gateway/history.py
import asyncio
import json
import logging
import re
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


def _write_capped_list(r, key: str, msg: str, ttl_s: int = 3600 * 2) -> None:
    """Append one message and keep the last ten, in one pipeline round-trip."""
    pipe = r.pipeline()
    pipe.rpush(key, msg)
    pipe.ltrim(key, -10, -1)
    pipe.expire(key, ttl_s)
    pipe.execute()

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
        # redis-py is synchronous; a slow Redis must never stall the gateway's
        # event loop (and every other in-flight voice/chat turn with it).
        raw_msgs: list = await asyncio.to_thread(r.lrange, key, 0, -1)  # type: ignore[misc]
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
        await asyncio.to_thread(_write_capped_list, r, key, msg)
        if role == "user":
            _schedule_fact_extraction(user_id)
    except Exception as e:
        log.warning(f"History write error: {e}")

def _get_room_history_key(token: str) -> str:
    """A Talk room's transcript is keyed to the room, not to any one user."""
    return f"rag:room:{token}:history"

async def get_room_history(token: str) -> list:
    """Retrieves the recent turns of one Talk room as a list of dicts."""
    try:
        key = _get_room_history_key(token)
        r = get_redis()
        raw_msgs: list = await asyncio.to_thread(r.lrange, key, 0, -1)  # type: ignore[misc]
        if not raw_msgs:
            return []

        msgs = []
        for m in raw_msgs:
            try:
                data = json.loads(m)
                if isinstance(data, dict):
                    msgs.append(data)
            except Exception as e:
                log.debug(f"Failed to parse room history message: {e}")
        return msgs
    except Exception as e:
        log.warning(f"Room history read error: {e}")
        return []

async def update_room_history(token: str, role: str, content: str):
    """Saves a message to the room's Redis history."""
    if not content:
        return
    try:
        key = _get_room_history_key(token)
        msg = json.dumps({"role": role, "content": content})
        r = get_redis()
        await asyncio.to_thread(_write_capped_list, r, key, msg)
    except Exception as e:
        log.warning(f"Room history write error: {e}")

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
            memory = f"### User Preferences & Facts (Long-term Memory)\n{context}\n"
            # Per-request context only: a bounded block can never crowd out the
            # question or the recalled passages.
            return memory[:MAX_LONG_TERM_MEMORY_CHARS]
    except Exception as e:
        log.warning(f"Failed to retrieve long-term memory: {e}")
        return ""


USER_FACTS_COLLECTION = "user_facts"
MAX_LONG_TERM_MEMORY_CHARS = 400
#: Anonymous/household speakers are not a person; their words must never become
#: a user's long-term facts.
_NON_PERSONAL_USERS = frozenset({"", "default", "household", "anonymous"})
#: Throttle the extractor: a chatty user must not start an LLM call per turn.
FACT_EXTRACTION_INTERVAL_S = 300.0
_last_fact_extraction: dict[str, float] = {}
_fact_tasks: set[asyncio.Task] = set()

_MY_FACT_RE = re.compile(
    r"\bmy\s+([^.!?\n]{1,60}?)\s+(?:is|are|was|were)\s+([^.!?\n]{1,80})", re.IGNORECASE
)
_PREFERENCE_RE = re.compile(
    r"\bI\s+(prefer|like|love|use|work as|work at|live in|am from)\s+([^.!?\n]{1,100})",
    re.IGNORECASE,
)
_REMEMBER_RE = re.compile(r"\bremember that I\s+([^.!?\n]{1,100})", re.IGNORECASE)


def is_personal_user(user_id: str | None) -> bool:
    return str(user_id or "").strip().lower() not in _NON_PERSONAL_USERS


def _deterministic_facts(history: list) -> list[str]:
    """Personal facts stated explicitly, when no LLM is configured."""
    facts: list[str] = []
    for message in list(history or [])[-6:]:
        if not isinstance(message, dict) or str(message.get("role", "")).lower() != "user":
            continue
        text = " ".join(str(message.get("content", "")).split())
        if not text:
            continue
        match = _MY_FACT_RE.search(text)
        if match:
            facts.append(f"User's {match.group(1).strip()} is {match.group(2).strip()}")
        match = _PREFERENCE_RE.search(text)
        if match:
            facts.append(f"User {match.group(1).strip().lower()} {match.group(2).strip()}")
        match = _REMEMBER_RE.search(text)
        if match:
            facts.append(f"Remember: the user {match.group(1).strip()}")
    return _unique_facts(facts)


def _unique_facts(facts: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for fact in facts:
        cleaned = str(fact or "").strip().strip("-* ").strip()
        if len(cleaned) < 5 or cleaned.upper() == "NONE":
            continue
        key = re.sub(r"\W+", " ", cleaned.lower()).strip()
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(cleaned)
    return unique[:3]


async def _llm_facts(history: list, model: str, ollama_url: str) -> list[str] | None:
    """Facts the model extracted, or ``None`` when it said there are none.

    ``None`` is distinct from an exception/empty list: an explicit ``NONE`` must
    stop extraction, while a model outage falls back to explicit facts.
    """
    from services.gateway.main import shared_http_client

    recent_text = ""
    for message in list(history)[-2:]:
        role = "USER" if message.get("role") == "user" else "ASSISTANT"
        recent_text += f"{role}: {message.get('content')}\n"
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
            json={"model": model, "prompt": prompt, "stream": False},
            headers={"x-request-source": "background"},
            timeout=aiohttp.ClientTimeout(total=60.0),
        )
        if resp.status != 200:
            raise RuntimeError(f"fact extraction model answered HTTP {resp.status}")
        data = await resp.json()
        text = str(data.get("response", "") or "").strip()
        # Exact marker, not a substring: a real fact may contain the letters
        # "none" ("User has none of the...") and must not be dropped.
        if not text or text.strip().upper() == "NONE":
            return None
        facts = []
        for line in text.splitlines():
            cleaned = re.sub(r"^[-*\d.\s]+", "", line).strip()
            if cleaned and cleaned.upper() != "NONE":
                facts.append(cleaned)
        return _unique_facts(facts) or None


async def _existing_user_facts(user_id: str, facts: list[str]) -> set[str]:
    """Normalized facts already stored for this user (for dedup)."""
    from services.gateway.main import shared_http_client
    from services.gateway.orchestrator import _get, get_all_settings

    settings = await get_all_settings()
    rag_svc = _get(settings, "rag_svc_url")
    if not rag_svc:
        return set()
    async with shared_http_client() as client:
        resp = await client.post(
            f"{rag_svc}/rag/search",
            json={
                "collection_name": USER_FACTS_COLLECTION,
                "query": " ".join(facts)[:2000],
                "user_id": user_id,
                "k": 20,
                "rerank": False,
            },
            headers={"X-Internal-Secret": INTERNAL_SECRET},
            timeout=aiohttp.ClientTimeout(total=10.0),
        )
        if resp.status != 200:
            return set()
        data = await resp.json()
    return {
        re.sub(r"\W+", " ", str(item.get("content", "")).lower()).strip()
        for item in data.get("results", [])
        if isinstance(item, dict)
    }


def _schedule_fact_extraction(user_id: str) -> None:
    """Fire-and-forget after a user turn; never blocks or fails the reply."""
    owner = str(user_id or "").strip().lower()
    if not is_personal_user(owner):
        return
    now = time.monotonic()
    if now - _last_fact_extraction.get(owner, 0.0) < FACT_EXTRACTION_INTERVAL_S:
        return
    _last_fact_extraction[owner] = now
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    task = loop.create_task(_fact_extraction_task(owner))
    _fact_tasks.add(task)
    task.add_done_callback(_fact_tasks.discard)


async def _fact_extraction_task(user_id: str) -> None:
    try:
        history = await get_history(user_id)
        await extract_and_store_user_facts(user_id, history)
    except Exception as e:  # a background extractor must never log as a failure
        log.debug(f"Fact extraction task failed for {user_id}: {e}")


async def extract_and_store_user_facts(user_id: str, history: list):
    """Extract this user's permanent facts and store them in User SIM's facts.

    Strictly per-user: anonymous and household speakers are skipped here as
    well as at the scheduler, so a shared device cannot leak into a person's
    memory.
    """
    owner = str(user_id or "").strip().lower()
    if not is_personal_user(owner) or not history or len(history) < 2:
        return

    try:
        from services.gateway.main import shared_http_client
        from services.gateway.orchestrator import _get, get_all_settings

        settings = await get_all_settings()
        librarian_model = (
            _get(settings, "ollama_librarian_model")
            or _get(settings, "librarian_model")
            or _get(settings, "assistant_model")
        )
        ollama_url = _get(settings, "llm_local_url")
        rag_svc = _get(settings, "rag_svc_url")
        facts: list[str] = []
        if librarian_model and ollama_url:
            try:
                extracted = await _llm_facts(history, librarian_model, ollama_url)
            except Exception as e:
                log.debug(f"LLM fact extraction failed; using explicit facts: {e}")
            else:
                if extracted is None:
                    return  # the model explicitly said there is nothing to keep
                facts = extracted
        if not facts:
            facts = _deterministic_facts(history)
        if not facts:
            return

        if not rag_svc:
            return
        existing = await _existing_user_facts(owner, facts)
        facts = [fact for fact in facts if re.sub(r"\W+", " ", fact.lower()).strip() not in existing]
        if not facts:
            return

        async with shared_http_client() as client:
            for fact in facts:
                await client.post(
                    f"{rag_svc}/rag/ingest",
                    json={
                        "collection_name": USER_FACTS_COLLECTION,
                        "content": fact,
                        "metadata": {"type": "user_fact", "timestamp": time.time()},
                        "user_id": owner,
                    },
                    headers={"X-Internal-Secret": INTERNAL_SECRET},
                    timeout=aiohttp.ClientTimeout(total=60.0),
                )
                log.info(f"[Mem0] Extracted fact for {owner}: {fact}")
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

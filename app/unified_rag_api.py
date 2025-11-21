# unified_rag_api.py — Complete: History, Robust Debugging, Smart Media Logic
import os
import time
import json
import subprocess
import logging
import traceback
import asyncio
import re
from typing import List, Optional, Dict, Any, Tuple
from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor
from functools import partial

import requests
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Optional OpenAI support
try:
    import openai
except Exception:
    openai = None

# Load .env when running locally
if os.getenv("DOCKER_ENV") != "1" and os.path.exists(".env"):
    from dotenv import load_dotenv

    load_dotenv(".env")

# ------------------
# Logging + flags
# ------------------
DEBUG = os.getenv("DEBUG", "0") in ("1", "true", "True")
DEBUG_RAG_CONTEXT = (
    os.getenv("DEBUG_RAG_CONTEXT", "0") in ("1", "true", "True") or DEBUG
)
logging.basicConfig(
    level=logging.DEBUG if DEBUG else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("unified-rag")

# ------------------
# Environment / config
# ------------------
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
HA_URL = os.getenv("HA_URL")
HA_DEFAULT_USER = os.getenv("HA_DEFAULT_USER", "Admin")
CHROMA_DIR = os.getenv("CHROMA_PERSIST_DIR", "/data/chroma_db")
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "qwen3:latest")
NEXTCLOUD_URL = os.getenv("NEXTCLOUD_URL")
NEXTCLOUD_USER = os.getenv("NEXTCLOUD_USER")
NEXTCLOUD_PASS = os.getenv("NEXTCLOUD_PASS")
EMB_MODEL = os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "120"))
OLLAMA_RETRY = int(os.getenv("OLLAMA_RETRY", "1"))

# Tuning
HA_CACHE_TTL = float(os.getenv("HA_CACHE_TTL", "30.0"))
QUERY_CACHE_TTL = float(os.getenv("QUERY_CACHE_TTL", "60.0"))
MAX_HISTORY_TURNS = 10  # Keep last 10 interactions

# Thread pool for blocking IO (requests, chroma)
EXECUTOR = ThreadPoolExecutor(max_workers=int(os.getenv("THREADPOOL_SIZE", "8")))

# Configure OpenAI if present
if openai and OPENAI_API_KEY:
    try:
        openai.api_key = OPENAI_API_KEY
    except Exception:
        log.warning("Failed to set OpenAI API key in client.")

# ------------------
# In-Memory Conversation History
# ------------------
CHAT_HISTORY: Dict[str, List[Dict[str, str]]] = {}


def update_history(user: str, role: str, content: str):
    if not content:
        return
    if user not in CHAT_HISTORY:
        CHAT_HISTORY[user] = []
    CHAT_HISTORY[user].append({"role": role, "content": content})
    if len(CHAT_HISTORY[user]) > MAX_HISTORY_TURNS * 2:
        CHAT_HISTORY[user] = CHAT_HISTORY[user][-(MAX_HISTORY_TURNS * 2) :]


def get_history_context(user: str) -> str:
    if user not in CHAT_HISTORY or not CHAT_HISTORY[user]:
        return ""
    formatted = []
    for msg in CHAT_HISTORY[user]:
        role = "USER" if msg["role"] == "user" else "ASSISTANT"
        formatted.append(f"{role}: {msg['content']}")
    return "\n".join(formatted)


# ------------------
# Helper: user creds
# ------------------
def get_user_creds(user: Optional[str] = None):
    user = user or HA_DEFAULT_USER
    ha_token = os.getenv(f"HA_{user}_TOKEN") or os.getenv("HA_TOKEN")
    nc_pass = os.getenv(f"NEXTCLOUD_{user}_PASS") or os.getenv("NEXTCLOUD_PASS")
    return {"user": user, "ha_token": ha_token, "nc_pass": nc_pass}


# ------------------
# Global Resources
# ------------------
class GlobalResources:
    embedding_model = None
    chroma_client = None
    nextcloud_collection = None
    ha_collection = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("--- STARTUP: Loading Embedding Model & Vector DB ---")
    try:
        os.environ["ANONYMIZED_TELEMETRY"] = "False"
        from langchain_huggingface import HuggingFaceEmbeddings
        from langchain_chroma import Chroma

        log.info(f"Loading embedding model: {EMB_MODEL} ...")
        GlobalResources.embedding_model = HuggingFaceEmbeddings(model_name=EMB_MODEL)

        log.info(f"Connecting to ChromaDB at {CHROMA_DIR} ...")
        GlobalResources.chroma_client = Chroma(
            persist_directory=CHROMA_DIR,
            embedding_function=GlobalResources.embedding_model,
        )

        GlobalResources.nextcloud_collection = Chroma(
            collection_name="nextcloud_docs",
            embedding_function=GlobalResources.embedding_model,
            persist_directory=CHROMA_DIR,
        )

        GlobalResources.ha_collection = Chroma(
            collection_name="ha_sensors",
            embedding_function=GlobalResources.embedding_model,
            persist_directory=CHROMA_DIR,
        )
        log.info("RAG Resources initialized successfully.")
    except Exception as e:
        log.critical(f"CRITICAL: Failed to initialize RAG resources: {e}")
        log.critical(traceback.format_exc())
    yield
    log.info("--- SHUTDOWN: Cleaning up resources ---")
    GlobalResources.embedding_model = None
    GlobalResources.chroma_client = None
    GlobalResources.ha_collection = None
    GlobalResources.nextcloud_collection = None


try:
    from langchain_core.documents import Document
except Exception:
    Document = None

# ------------------
# FastAPI app
# ------------------
app = FastAPI(title="Unified RAG API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ------------------
# Caching
# ------------------
class TTLCache:
    def __init__(self):
        self._store: Dict[str, Tuple[float, Any]] = {}
        self._lock = asyncio.Lock()

    async def get(self, key: str):
        async with self._lock:
            v = self._store.get(key)
            if not v:
                return None
            ts, val = v
            if time.time() - ts > QUERY_CACHE_TTL:
                del self._store[key]
                return None
            return val

    async def set(self, key: str, value: Any, ttl: Optional[float] = None):
        async with self._lock:
            self._store[key] = (time.time(), value)


_query_cache = TTLCache()
_ha_cache: Dict[str, Tuple[float, str]] = {}
_ha_cache_lock = asyncio.Lock()


async def ha_cache_get(user: str) -> Optional[str]:
    async with _ha_cache_lock:
        rec = _ha_cache.get(user)
        if not rec:
            return None
        ts, val = rec
        if time.time() - ts > HA_CACHE_TTL:
            del _ha_cache[user]
            return None
        return val


async def ha_cache_set(user: str, value: str):
    async with _ha_cache_lock:
        _ha_cache[user] = (time.time(), value)


# ------------------
# Utility
# ------------------
async def run_blocking(fn, *args, **kwargs):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(EXECUTOR, partial(fn, *args, **kwargs))


def _requests_post(url, json=None, headers=None, timeout=None, stream=False):
    return requests.post(
        url, json=json, headers=headers, timeout=timeout, stream=stream
    )


def _requests_get(url, headers=None, timeout=None, stream=False):
    return requests.get(url, headers=headers, timeout=timeout, stream=stream)


async def requests_post(url, json=None, headers=None, timeout=None, stream=False):
    return await run_blocking(_requests_post, url, json, headers, timeout, stream)


async def requests_get(url, headers=None, timeout=None, stream=False):
    return await run_blocking(_requests_get, url, headers, timeout, stream)


# ------------------
# Ollama helper
# ------------------
async def call_ollama_generate(
    prompt: str,
    model: str = DEFAULT_MODEL,
    stream: bool = False,
    timeout: int = OLLAMA_TIMEOUT,
):
    url = f"{OLLAMA_URL.rstrip('/')}/api/generate"
    payload = {"model": model, "prompt": prompt, "stream": stream}
    headers = {"Content-Type": "application/json"}
    last_exc = None

    for attempt in range(max(1, OLLAMA_RETRY)):
        try:
            resp = await requests_post(
                url, json=payload, headers=headers, timeout=timeout, stream=True
            )
            resp.raise_for_status()
            r = resp
            break
        except Exception as e:
            last_exc = e
            await asyncio.sleep(0.2)
    else:
        log.error(f"Ollama failed: {last_exc}")
        raise HTTPException(status_code=502, detail="Ollama unavailable")

    if stream:

        def generator_sync():
            try:
                for raw_line in r.iter_lines(decode_unicode=True):
                    if not raw_line:
                        continue
                    try:
                        obj = json.loads(raw_line)
                        yield obj
                        if obj.get("done") is True:
                            break
                    except:
                        yield raw_line
            except:
                pass

        async def async_iter():
            loop = asyncio.get_running_loop()
            for chunk in await loop.run_in_executor(
                EXECUTOR, lambda: list(generator_sync())
            ):
                yield chunk

        return {"iterable": async_iter}

    try:
        data = r.json()
        return {"text": data.get("text") or data.get("response") or ""}
    except:
        return {"text": r.text}


# ------------------
# OpenAI helper
# ------------------
async def call_openai_chat(
    messages: List[Dict[str, str]], model: Optional[str] = None, stream: bool = False
):
    model = model or OPENAI_MODEL
    if not openai:
        raise HTTPException(status_code=501)
    try:
        if stream:

            def gen_sync():
                resp = openai.ChatCompletion.create(
                    model=model, messages=messages, stream=True
                )
                for chunk in resp:
                    yield chunk

            async def async_iter():
                loop = asyncio.get_running_loop()
                for chunk in await loop.run_in_executor(
                    EXECUTOR, lambda: list(gen_sync())
                ):
                    yield chunk

            return {"iterable": async_iter}
        else:
            resp = await run_blocking(
                openai.ChatCompletion.create, model=model, messages=messages
            )
            return {"text": resp.choices[0].message.content}
    except Exception as e:
        log.exception("OpenAI call failed: %s", e)
        raise HTTPException(status_code=502, detail=str(e))


# ------------------
# Action Handlers
# ------------------
async def execute_ha_service(
    domain: str,
    service: str,
    entity_id: str,
    user: str = None,
    service_data: dict = None,
):
    creds = get_user_creds(user)
    url = f"{HA_URL.rstrip('/')}/api/services/{domain}/{service}"
    headers = {"Authorization": f"Bearer {creds['ha_token']}"}

    payload = {"entity_id": entity_id}
    if service_data:
        payload.update(service_data)

    try:
        resp = await requests_post(url, json=payload, headers=headers, timeout=5.0)
        if resp.status_code >= 400:
            log.error(f"HA Service Error ({resp.status_code}): {resp.text}")
            return f"Home Assistant failed (Code {resp.status_code}): {resp.text}"

        resp.raise_for_status()
        return f"Successfully executed {domain}.{service} on {entity_id}."
    except Exception as e:
        log.error(f"Action failed: {e}")
        return f"Failed to execute command on {entity_id}: {e}"


# ------------------
# Query Contextualizer & Decomposer
# ------------------
def is_system_task(query: str) -> bool:
    q = query.strip()
    if q.startswith("### Task:"):
        return True
    if "Generate a concise" in q and "title" in q:
        return True
    if "Generate" in q and "tags" in q:
        return True
    if "Suggest" in q and "follow-up questions" in q:
        return True
    return False


async def contextualize_query(query: str, user: str, model: str = DEFAULT_MODEL) -> str:
    history_str = get_history_context(user)
    if not history_str:
        return query

    prompt = f"""Rewrite the User Input to be a standalone command based on the Chat History.
Chat History:
{history_str}

User Input: {query}

Standalone Input:"""

    try:
        resp = await call_ollama_generate(prompt, model=model, stream=False)
        new_q = resp["text"].strip().strip('"')

        # Fix: Strip common LLM artifacts like /think or markdown
        # "contextualized: '/think ...'" -> cleanup
        if "/think" in new_q or "**" in new_q:
            # Naive cleanup: remove lines starting with special chars
            clean_lines = [
                line
                for line in new_q.split("\n")
                if not line.strip().startswith(("/", "*", "`"))
            ]
            if clean_lines:
                new_q = " ".join(clean_lines)

        log.debug(f"Contextualized: '{query}' -> '{new_q}'")
        return new_q
    except:
        return query


async def decompose_command_query(query: str, model: str = DEFAULT_MODEL) -> List[str]:
    prompt = f"""Analyze the user request. If it contains multiple distinct commands, split them into a JSON list of strings. 
If it is a single command, return a list with just that command.
Do not include JSON markdown formatting.

User Request: "{query}"

Output (JSON Array Only):"""

    try:
        resp = await call_ollama_generate(prompt, model=model, stream=False)
        text = resp["text"].strip()
        if text.startswith("```json"):
            text = text.split("```json")[1].split("```")[0]
        if text.startswith("```"):
            text = text.strip("`")
        commands = json.loads(text)
        if isinstance(commands, list):
            return commands
        return [query]
    except Exception as e:
        log.warning(f"Decomposition failed: {e}")
        return [query]


async def _handle_single_command(query: str, user: str) -> Optional[str]:
    q = query.lower().strip()
    service = None
    service_data = None

    # 1. Basic Switch/Light/Lock Logic
    if "turn on" in q or "switch on" in q:
        service = "turn_on"
    elif "turn off" in q or "switch off" in q:
        service = "turn_off"
    elif "toggle" in q:
        service = "toggle"
    elif "lock" in q:
        service = "lock"
    elif "unlock" in q:
        service = "unlock"
    elif "open" in q:
        service = "open_cover"
    elif "close" in q:
        service = "close_cover"

    # 2. Media Logic (Play Content)
    elif "play" in q:
        service = "play_media"
        try:
            clean_q = (
                q.replace("please", "")
                .replace("can you", "")
                .replace("music", "")
                .replace("song", "")
            )
            if " on " in clean_q:
                parts = clean_q.split("play ", 1)[1].split(" on ")
                media_content = parts[0].strip()
            else:
                media_content = clean_q.split("play ", 1)[1].strip()

            media_content = media_content.strip().strip('".')
            if media_content:
                service_data = {
                    "media_id": media_content,
                    "enqueue": "play",
                    "radio_mode": True,
                }
        except:
            pass

    # 3. Simple Media Controls
    elif "resume" in q:
        service = "media_play"
    elif "pause" in q:
        service = "media_pause"
    elif "stop" in q:
        service = "media_stop"
    elif "next" in q or "skip" in q:
        service = "media_next_track"
    elif "previous" in q or "back" in q:
        service = "media_previous_track"

    if not service or not GlobalResources.ha_collection:
        return None

    try:

        def search_sync():
            return GlobalResources.ha_collection.similarity_search(query, k=1)

        docs = await run_blocking(search_sync)
        if not docs:
            return None
        eid = docs[0].metadata.get("entity_id")
        if not eid:
            return None

        domain = eid.split(".")[0]
        target_service = service
        target_domain = domain

        if domain not in [
            "light",
            "switch",
            "cover",
            "lock",
            "input_boolean",
            "script",
            "automation",
            "climate",
            "media_player",
        ]:
            return None

        # FIX: Default to homeassistant domain ONLY for generic domains
        if service in ["turn_on", "turn_off", "toggle"] and domain != "media_player":
            target_domain = "homeassistant"

        if domain == "lock" and service in ["turn_on", "turn_off"]:
            target_service = "lock" if service == "turn_on" else "unlock"
            target_domain = "lock"

        if service in ["open_cover", "close_cover"]:
            if domain == "cover":
                target_domain = "cover"
            else:
                target_domain = "homeassistant"
                target_service = "turn_on" if "open" in service else "turn_off"

        # FIX: Media Player Specific Logic
        if domain == "media_player":
            target_domain = "media_player"  # Ensure we stay on media_player domain
            if service == "turn_on":
                target_service = "turn_on"
            if service == "turn_off":
                target_service = "turn_off"

            # If using Music Assistant specific play
            if service == "play_media" and service_data:
                target_domain = "music_assistant"
                target_service = "play_media"

        return await execute_ha_service(
            target_domain, target_service, eid, user, service_data
        )
    except Exception as e:
        log.warning(f"Intent execution error: {e}")
        return None


async def try_handle_command(query: str, user: str) -> Optional[str]:
    commands = await decompose_command_query(query)
    results = []
    for cmd in commands:
        res = await _handle_single_command(cmd, user)
        if res:
            results.append(res)

    if not results:
        return None
    return "\n".join(results)


# ------------------
# Context Fetchers
# ------------------
async def get_ha_context(
    user: Optional[str] = None, limit: int = 1000, query: Optional[str] = None
) -> str:
    creds = get_user_creds(user)
    user_key = creds["user"]

    if not query:
        cached = await ha_cache_get(user_key)
        if cached is not None:
            return cached

    ha_token = creds["ha_token"]
    if not HA_URL:
        return "[System Warning: HA_URL is not configured]"
    if not ha_token:
        return "[System Warning: HA_TOKEN is missing]"

    target_ids = {"sensor.time_date", "sun.sun"}
    GLOBAL_DOMAINS = ["person", "weather", "calendar", "sensor"]

    if GlobalResources.ha_collection and query:
        try:

            def search_sync():
                return GlobalResources.ha_collection.similarity_search(query, k=15)

            docs = await run_blocking(search_sync)
            for d in docs:
                eid = d.metadata.get("entity_id")
                if eid:
                    target_ids.add(eid)
        except Exception as e:
            log.warning(f"Hybrid search error: {e}")

    try:
        headers = {"Authorization": f"Bearer {ha_token}"}
        resp = await requests_get(
            f"{HA_URL.rstrip('/')}/api/states", headers=headers, timeout=(3.0, 5.0)
        )
        resp.raise_for_status()
        states = resp.json()

        final_lines = []
        for s in states:
            if s.get("state") in ["unavailable", "unknown"]:
                continue

            eid = s.get("entity_id", "")
            domain = eid.split(".")[0]

            is_target = eid in target_ids
            is_global = domain in GLOBAL_DOMAINS

            if is_target or is_global or (not query and len(final_lines) < limit):
                fname = s.get("attributes", {}).get("friendly_name", "")
                line = f"{eid}: {s['state']}"
                if fname:
                    line += f" ({fname})"
                final_lines.append(line)

        ctx = "Home Assistant Status:\n" + "\n".join(final_lines[:limit])

        if not query:
            await ha_cache_set(user_key, ctx)

        return ctx
    except requests.exceptions.ConnectTimeout:
        return f"[System Warning: Connection to Home Assistant at {HA_URL} timed out]"
    except requests.exceptions.ConnectionError:
        return f"[System Warning: Failed to connect to Home Assistant at {HA_URL}. Check if it is running.]"
    except Exception as e:
        log.exception("Failed to fetch HA context: %s", e)
        return f"[System Warning: Error fetching Home Assistant data: {e}]"


# ------------------
# Nextcloud context
# ------------------
async def get_nextcloud_context(
    query: str, user: Optional[str] = None, k: int = 4
) -> str:
    cache_key = f"nc:{user or 'default'}:{query}"
    cached = await _query_cache.get(cache_key)
    if cached is not None:
        return cached

    if not GlobalResources.nextcloud_collection:
        return "[System Note: Nextcloud knowledge base is not initialized]"

    def search_sync():
        try:
            return GlobalResources.nextcloud_collection.similarity_search_with_score(
                query, k=k
            )
        except Exception as e:
            log.error("Chroma search failed: %s", e)
            return None

    docs_with_scores = await run_blocking(search_sync)

    if docs_with_scores is None:
        return "[System Warning: Database search failed]"

    if not docs_with_scores:
        await _query_cache.set(cache_key, "")
        return ""

    parts = []
    for d, score in docs_with_scores:
        content = getattr(d, "page_content", "") or d.get("page_content", "")
        meta = getattr(d, "metadata", {}) or d.get("metadata", {})
        path = meta.get("path", "N/A")
        if content.strip():
            parts.append(f"[Source: {path} | Score: {score:.4f}]\n{content}")

    result = f"Nextcloud context (user {user or 'default'}):\n" + "\n\n".join(parts)
    await _query_cache.set(cache_key, result)
    return result


# ------------------
# Pydantic models
# ------------------
class ChatMessage(BaseModel):
    role: str
    content: str


class CompletionRequest(BaseModel):
    model: Optional[str] = DEFAULT_MODEL
    messages: Optional[List[ChatMessage]] = None
    query: Optional[str] = None
    user: Optional[str] = None
    stream: Optional[bool] = False
    use_openai: Optional[bool] = False


class UpsertRagRequest(BaseModel):
    id: Optional[str] = None
    text: str
    metadata: Optional[Dict[str, Any]] = None


class GenerateRequest(BaseModel):
    prompt: str
    model: Optional[str] = DEFAULT_MODEL
    stream: Optional[bool] = False
    use_openai: Optional[bool] = False


# ------------------
# Helper: RAG Streaming Logic
# ------------------
async def stream_rag_result(
    query: str, user: str, model: str, use_openai: bool, format_type: str
):
    # 1. System Task Bypass
    if is_system_task(query):
        log.debug(f"System task detected, bypassing RAG: {query[:30]}...")
        if use_openai and openai:
            resp = await call_openai_chat(
                [{"role": "user", "content": query}], model=OPENAI_MODEL, stream=True
            )

            async def oa_task_gen():
                yield f"data: {json.dumps({'id': f'chatcmpl-{int(time.time())}', 'choices': [{'index': 0, 'delta': {'role': 'assistant'}, 'finish_reason': None}]})}\n\n"
                async for chunk in resp["iterable"]():
                    content = (
                        chunk.choices[0].delta.get("content", "")
                        if hasattr(chunk, "choices")
                        else ""
                    )
                    yield f"data: {json.dumps({'choices': [{'delta': {'content': content}}]})}\n\n"
                yield "data: [DONE]\n\n"

            return StreamingResponse(oa_task_gen(), media_type="text/event-stream")
        else:
            r = await call_ollama_generate(query, model, stream=True)
            if "iterable" in r:

                async def ol_task_gen():
                    if format_type == "openai":
                        yield f"data: {json.dumps({'id': f'chatcmpl-{int(time.time())}', 'choices': [{'index': 0, 'delta': {'role': 'assistant'}, 'finish_reason': None}]})}\n\n"
                    async for chunk in r["iterable"]():
                        if format_type == "openai":
                            txt = ""
                            if isinstance(chunk, dict):
                                txt = chunk.get("response", "")
                            yield f"data: {json.dumps({'choices': [{'index': 0, 'delta': {'content': txt}}]})}\n\n"
                            if isinstance(chunk, dict) and chunk.get("done"):
                                yield f"data: {json.dumps({'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]})}\n\n"
                                yield "data: [DONE]\n\n"
                        else:
                            yield (
                                json.dumps(chunk) + "\n"
                                if isinstance(chunk, dict)
                                else str(chunk) + "\n"
                            )

                return StreamingResponse(
                    ol_task_gen(), media_type="application/x-ndjson"
                )
            return JSONResponse({"response": r.get("text")})

    # 2. Contextualize
    refined_query = await contextualize_query(query, user, model)
    update_history(user, "user", query)

    # 3. Intent Check
    command_res = await try_handle_command(refined_query, user)
    if command_res:
        update_history(user, "assistant", command_res)

        async def action_response_fmt():
            text = command_res
            if format_type == "openai":
                yield f"data: {json.dumps({'id': f'chatcmpl-{int(time.time())}', 'choices': [{'index': 0, 'delta': {'role': 'assistant'}, 'finish_reason': None}]})}\n\n"
                yield f"data: {json.dumps({'choices': [{'index': 0, 'delta': {'content': text}, 'finish_reason': None}]})}\n\n"
                yield f"data: {json.dumps({'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]})}\n\n"
                yield "data: [DONE]\n\n"
            elif format_type == "chat":
                yield (
                    json.dumps(
                        {
                            "model": model,
                            "created_at": time.strftime(
                                "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
                            ),
                            "message": {"role": "assistant", "content": text},
                            "done": True,
                        }
                    )
                    + "\n"
                )
            else:
                yield json.dumps({"response": text, "done": True}) + "\n"

        media = (
            "text/event-stream" if format_type == "openai" else "application/x-ndjson"
        )
        return StreamingResponse(action_response_fmt(), media_type=media)

    # 4. Standard RAG Flow
    ha_ctx = await get_ha_context(user=user, query=refined_query)
    nc_ctx = await get_nextcloud_context(refined_query, user=user)
    combined_context = "\n\n".join([c for c in (ha_ctx, nc_ctx) if c])

    history_context = get_history_context(user)

    prompt = f"""You are a local AI assistant.
Chat History:
{history_context}

Context:
{combined_context}

User question:
{refined_query}

Answer:"""

    full_reply = ""

    if use_openai and openai:
        resp = await call_openai_chat(
            messages=[
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": prompt},
            ],
            model=OPENAI_MODEL,
            stream=True,
        )

        async def openai_fmt():
            nonlocal full_reply
            async for chunk in resp["iterable"]():
                content = (
                    chunk.choices[0].delta.get("content", "")
                    if hasattr(chunk, "choices")
                    else ""
                )
                full_reply += content
                yield f"data: {json.dumps({'choices': [{'delta': {'content': content}}]})}\n\n"

            update_history(user, "assistant", full_reply)
            yield "data: [DONE]\n\n"

        return StreamingResponse(openai_fmt(), media_type="text/event-stream")

    r = await call_ollama_generate(prompt=prompt, model=model, stream=True)

    if "iterable" in r:

        async def ollama_translator():
            nonlocal full_reply

            if format_type == "openai":
                yield f"data: {json.dumps({'id': f'chatcmpl-{int(time.time())}', 'choices': [{'index': 0, 'delta': {'role': 'assistant'}, 'finish_reason': None}]})}\n\n"

            async for chunk in r["iterable"]():
                if isinstance(chunk, dict):
                    token = c.get("response", "")
                    full_reply += token

                    if format_type == "openai":
                        yield f"data: {json.dumps({'choices': [{'index': 0, 'delta': {'content': token}, 'finish_reason': None}]})}\n\n"
                        if chunk.get("done"):
                            yield f"data: {json.dumps({'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]})}\n\n"
                            yield "data: [DONE]\n\n"

                    elif format_type == "chat":
                        if "message" not in chunk:
                            yield (
                                json.dumps(
                                    {
                                        "model": model,
                                        "message": {
                                            "role": "assistant",
                                            "content": token,
                                        },
                                        "done": chunk.get("done", False),
                                    }
                                )
                                + "\n"
                            )
                        else:
                            yield json.dumps(chunk) + "\n"

                    else:
                        yield json.dumps(chunk) + "\n"

                else:
                    yield str(chunk) + "\n"

            update_history(user, "assistant", full_reply)

        media_type = (
            "text/event-stream" if format_type == "openai" else "application/x-ndjson"
        )
        return StreamingResponse(ollama_translator(), media_type=media_type)

    return JSONResponse({"error": "Stream failed"})


# ------------------
# RAG endpoints
# ------------------
@app.post("/rag/query")
async def rag_query(body: CompletionRequest, request: Request):
    header_user = request.headers.get("X-RAG-User")
    user = header_user or body.user or HA_DEFAULT_USER
    query = body.query
    if not query and body.messages:
        query = body.messages[-1].content
    if not query:
        raise HTTPException(status_code=400, detail="No query provided")

    refined = await contextualize_query(query, user, body.model or DEFAULT_MODEL)
    update_history(user, "user", query)

    cmd_res = await try_handle_command(refined, user)
    if cmd_res:
        update_history(user, "assistant", cmd_res)
        return {"id": f"cmd-{int(time.time())}", "user": user, "response": cmd_res}

    ha_ctx = await get_ha_context(user=user, query=refined)
    nc_ctx = await get_nextcloud_context(refined, user=user)
    combined_context = "\n\n".join([c for c in (ha_ctx, nc_ctx) if c])

    if DEBUG_RAG_CONTEXT:
        return {"debug": True, "query": refined, "context": combined_context}

    prompt = f"""You are a local AI assistant.
Context:
{combined_context}
User question:
{refined}
Answer:"""

    model = body.model or DEFAULT_MODEL

    if body.use_openai and openai:
        resp = await call_openai_chat(
            messages=[{"role": "user", "content": prompt}], model=OPENAI_MODEL
        )
        update_history(user, "assistant", resp["text"])
        return {"id": f"rag-{int(time.time())}", "user": user, "response": resp["text"]}

    resp = await call_ollama_generate(prompt=prompt, model=model)
    update_history(user, "assistant", resp["text"])
    return {"id": f"rag-{int(time.time())}", "user": user, "response": resp["text"]}


@app.post("/api/chat")
async def api_chat(body: CompletionRequest, request: Request):
    header_user = request.headers.get("X-RAG-User")
    user = header_user or body.user or HA_DEFAULT_USER
    query = body.query or (body.messages[-1].content if body.messages else "")

    if body.stream:
        return await stream_rag_result(
            query,
            user,
            body.model or DEFAULT_MODEL,
            body.use_openai,
            format_type="chat",
        )

    if is_system_task(query):
        r = await call_ollama_generate(query, body.model)
        return {
            "model": body.model,
            "message": {"role": "assistant", "content": r["text"]},
            "done": True,
        }

    refined = await contextualize_query(query, user, body.model or DEFAULT_MODEL)
    update_history(user, "user", query)

    cmd_res = await try_handle_command(refined, user)
    if cmd_res:
        update_history(user, "assistant", cmd_res)
        return {
            "model": body.model or DEFAULT_MODEL,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "message": {"role": "assistant", "content": cmd_res},
            "done": True,
        }

    ha_ctx = await get_ha_context(user=user, query=refined)
    nc_ctx = await get_nextcloud_context(refined, user=user)
    prompt = f"Context:\n{ha_ctx}\n{nc_ctx}\n\nUser: {refined}\nAnswer:"

    if body.use_openai and openai:
        resp = await call_openai_chat(
            messages=[{"role": "user", "content": prompt}], model=OPENAI_MODEL
        )
        text = resp["text"]
    else:
        resp = await call_ollama_generate(
            prompt=prompt, model=body.model or DEFAULT_MODEL
        )
        text = resp["text"]

    update_history(user, "assistant", text)

    return {
        "model": body.model or DEFAULT_MODEL,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "message": {"role": "assistant", "content": text},
        "done": True,
    }


@app.post("/v1/chat/completions")
@app.post("/api/chat/completions")
@app.post("/chat/completions")
async def v1_chat(body: CompletionRequest, request: Request):
    header_user = request.headers.get("X-RAG-User")
    user = header_user or body.user or HA_DEFAULT_USER
    query = body.query or (body.messages[-1].content if body.messages else "")

    if body.stream:
        return await stream_rag_result(
            query,
            user,
            body.model or DEFAULT_MODEL,
            body.use_openai,
            format_type="openai",
        )

    if is_system_task(query):
        r = await call_ollama_generate(query, body.model)
        return {
            "id": f"sys-{int(time.time())}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": body.model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": r["text"]},
                    "finish_reason": "stop",
                }
            ],
        }

    refined = await contextualize_query(query, user, body.model or DEFAULT_MODEL)
    update_history(user, "user", query)

    cmd_res = await try_handle_command(refined, user)
    text = cmd_res if cmd_res else ""

    if not text:
        ha_ctx = await get_ha_context(user=user, query=refined)
        nc_ctx = await get_nextcloud_context(refined, user=user)
        prompt = f"Context:\n{ha_ctx}\n{nc_ctx}\n\nUser: {refined}\nAnswer:"

        if body.use_openai and openai:
            resp = await call_openai_chat(
                messages=[{"role": "user", "content": prompt}], model=OPENAI_MODEL
            )
            text = resp["text"]
        else:
            resp = await call_ollama_generate(
                prompt=prompt, model=body.model or DEFAULT_MODEL
            )
            text = resp["text"]

    update_history(user, "assistant", text)

    return {
        "id": f"chatcmpl-{int(time.time())}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": body.model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


@app.post("/chat/stream")
async def chat_stream(body: CompletionRequest, request: Request):
    header_user = request.headers.get("X-RAG-User")
    user = header_user or body.user or HA_DEFAULT_USER
    query = body.query or (body.messages[-1].content if body.messages else "")
    return await stream_rag_result(
        query, user, body.model or DEFAULT_MODEL, body.use_openai, format_type="chat"
    )


@app.post("/generate")
async def generate(req: GenerateRequest):
    if req.use_openai and openai:
        resp = await call_openai_chat(
            messages=[{"role": "user", "content": req.prompt}],
            model=OPENAI_MODEL,
            stream=False,
        )
        return {"text": resp.get("text")}
    r = await call_ollama_generate(
        prompt=req.prompt, model=req.model or DEFAULT_MODEL, stream=False
    )
    return {"text": r.get("text")}


@app.post("/generate/stream")
async def generate_stream(req: GenerateRequest):
    if req.use_openai and openai:
        resp = await call_openai_chat(
            messages=[{"role": "user", "content": req.prompt}],
            model=OPENAI_MODEL,
            stream=True,
        )
        return StreamingResponse(resp["iterable"](), media_type="text/event-stream")

    r = await call_ollama_generate(
        prompt=req.prompt, model=req.model or DEFAULT_MODEL, stream=True
    )
    if "iterable" in r:

        async def fmt():
            async for c in r["iterable"]():
                yield json.dumps(c) + "\n"

        return StreamingResponse(fmt(), media_type="text/event-stream")
    return JSONResponse({"text": r.get("text")})


# ------------------
# Manual RAG operations
# ------------------
@app.post("/api/rag/upsert")
async def rag_upsert(item: UpsertRagRequest):
    if not GlobalResources.chroma_client:
        raise HTTPException(status_code=503, detail="Vector DB not initialized")
    meta = item.metadata or {}
    meta["id"] = item.id or f"doc-{int(time.time())}"
    doc = Document(page_content=item.text, metadata=meta)

    def add_sync():
        GlobalResources.chroma_client.add_documents([doc])
        GlobalResources.chroma_client.persist()

    await run_blocking(add_sync)
    return {"status": "ok", "id": meta["id"]}


@app.post("/api/rag/delete")
async def rag_delete(doc_id: str):
    if not GlobalResources.chroma_client:
        raise HTTPException(status_code=503, detail="Vector DB not initialized")
    try:

        def delete_sync():
            try:
                GlobalResources.chroma_client.delete(ids=[doc_id])
            except:
                GlobalResources.chroma_client._collection.delete(ids=[doc_id])

        await run_blocking(delete_sync)
        return {"status": "ok", "deleted": doc_id}
    except Exception as e:
        log.exception("RAG delete failed")
        raise HTTPException(status_code=500, detail=f"RAG delete failed: {e}")


@app.get("/api/rag/list")
async def rag_list(limit: int = 100):
    if not GlobalResources.chroma_client:
        raise HTTPException(status_code=503, detail="Vector DB not initialized")
    try:

        def list_sync():
            coll = GlobalResources.chroma_client._collection
            cnt = coll.count()
            peek_n = min(limit, cnt)
            samples = (
                coll.peek(peek_n)
                if peek_n > 0
                else {"documents": [], "metadatas": [], "ids": []}
            )
            out = []
            for i, doc_id in enumerate(samples.get("ids", [])):
                meta = (
                    samples.get("metadatas", [])[i] if samples.get("metadatas") else {}
                )
                content = (
                    samples.get("documents", [])[i] if samples.get("documents") else ""
                )
                out.append({"id": doc_id, "preview": content[:500], "metadata": meta})
            return {"count": cnt, "docs": out}

        return await run_blocking(list_sync)
    except Exception as e:
        log.exception("RAG list failed")
        raise HTTPException(status_code=500, detail=f"RAG list failed: {e}")


@app.get("/api/rag/search")
async def rag_search(q: str, k: int = 4):
    if not GlobalResources.chroma_client:
        raise HTTPException(status_code=503, detail="Vector DB not initialized")
    try:

        def search_sync():
            docs = GlobalResources.chroma_client.similarity_search(q, k=k)
            out = []
            for d in docs:
                txt = getattr(d, "page_content", "")
                md = getattr(d, "metadata", {})
                out.append({"text": txt, "metadata": md})
            return out

        results = await run_blocking(search_sync)
        return {"results": results}
    except Exception as e:
        log.exception("RAG search failed")
        raise HTTPException(status_code=500, detail=f"RAG search failed: {e}")


@app.post("/context/update")
async def update_context(payload: Request):
    if not GlobalResources.chroma_client:
        raise HTTPException(status_code=503, detail="Vector DB not available")
    data = await payload.json()
    txt = data.get("text")
    if not txt:
        raise HTTPException(status_code=400, detail="text required")
    meta = {"source": data.get("source", "shared_context"), "user": data.get("user")}
    doc = Document(page_content=txt, metadata=meta)

    def add_sync():
        GlobalResources.chroma_client.add_documents([doc])
        GlobalResources.chroma_client.persist()

    await run_blocking(add_sync)
    return {"status": "ok"}


# ------------------
# Ingest script runner helpers
# ------------------
def _run_script_sync(script_path: str):
    stdout_accum, stderr_accum = [], []
    try:
        proc = subprocess.Popen(
            ["python", script_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        for line in proc.stdout:
            stdout_accum.append(line.rstrip())
        proc.wait()
        for line in proc.stderr:
            stderr_accum.append(line.rstrip())

        return {
            "status": "ok" if proc.returncode == 0 else "error",
            "stdout": "\n".join(stdout_accum),
            "stderr": "\n".join(stderr_accum),
            "code": proc.returncode,
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


async def _run_script(path: str):
    script_path = f"/app/{path}"
    log.info("Starting ingestion script: %s", path)
    return await run_blocking(_run_script_sync, script_path)


@app.post("/ingest/ha")
async def ingest_ha():
    return await _run_script("ha_ingest.py")


@app.post("/ingest/nextcloud")
async def ingest_nextcloud():
    return await _run_script("ingest_nextcloud.py")


@app.post("/ingest/all")
async def ingest_all():
    return {
        "ha": await _run_script("ha_ingest.py"),
        "nextcloud": await _run_script("ingest_nextcloud.py"),
    }


# ------------------
# Ollama passthrough endpoints
# ------------------
@app.get("/v1/models")
@app.get("/models")
async def v1_models():
    try:
        resp = await requests_get(f"{OLLAMA_URL.rstrip('/')}/v1/models", timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return {"data": []}


@app.get("/api/models")
async def api_models():
    out = {"ollama": None, "openai": None}
    try:
        resp = await requests_get(f"{OLLAMA_URL.rstrip('/')}/v1/models", timeout=10)
        out["ollama"] = resp.json()
    except Exception:
        pass
    if openai:
        try:
            out["openai"] = openai.Model.list()
        except:
            pass
    return out


@app.get("/api/tags")
async def ollama_tags():
    try:
        resp = await requests_get(f"{OLLAMA_URL.rstrip('/')}/api/tags", timeout=10)
        return resp.json()
    except Exception:
        return {"models": []}


@app.get("/api/ps")
async def ollama_ps():
    try:
        resp = await requests_get(f"{OLLAMA_URL.rstrip('/')}/api/ps", timeout=10)
        return resp.json()
    except Exception:
        return {"models": []}


@app.get("/api/version")
async def ollama_version():
    try:
        resp = await requests_get(f"{OLLAMA_URL.rstrip('/')}/api/version", timeout=5)
        return {"service": "unified-rag", "ollama": resp.json()}
    except Exception as e:
        return {"service": "unified-rag", "error": str(e)}


# ------------------
# Health, ping, root, debug
# ------------------
@app.get("/health")
async def health():
    return {
        "ok": True,
        "db_loaded": GlobalResources.chroma_client is not None,
        "model_loaded": GlobalResources.embedding_model is not None,
        "ha_url": bool(HA_URL),
    }


@app.get("/")
async def root():
    return {"service": "unified-rag", "status": "optimized"}


@app.get("/api/ping")
async def ping():
    return {"ok": True, "time": int(time.time())}


@app.get("/api/debug/timing")
async def debug_timing():
    return {
        "uptime_seconds": int(time.time()),
        "has_vector_db": bool(GlobalResources.chroma_client),
        "debug_mode": DEBUG,
    }


# ------------------
# Exception handlers
# ------------------
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    log.warning("HTTPException %s: %s", exc.status_code, exc.detail)
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    log.exception("Unhandled exception: %s", exc)
    tb = traceback.format_exc()
    return JSONResponse(
        status_code=500, content={"detail": str(exc), "trace": tb[:2000]}
    )

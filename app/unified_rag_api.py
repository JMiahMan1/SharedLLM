# unified_rag_api.py — High-Performance, Protocol-Fixed, Full Feature Set
import os
import time
import json
import subprocess
import logging
import traceback
import asyncio
from typing import List, Optional, Dict, Any, Tuple
from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor
from functools import partial

import requests
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
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
DEBUG_RAG_CONTEXT = os.getenv("DEBUG_RAG_CONTEXT", "0") in ("1", "true", "True") or DEBUG
logging.basicConfig(level=logging.DEBUG if DEBUG else logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
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

# Thread pool for blocking IO (requests, chroma)
EXECUTOR = ThreadPoolExecutor(max_workers=int(os.getenv("THREADPOOL_SIZE", "8")))

# Configure OpenAI if present
if openai and OPENAI_API_KEY:
    try:
        openai.api_key = OPENAI_API_KEY
    except Exception:
        log.warning("Failed to set OpenAI API key in client.")

# ------------------
# Helper: user creds
# ------------------
def get_user_creds(user: Optional[str] = None):
    user = user or HA_DEFAULT_USER
    ha_token = os.getenv(f"HA_{user}_TOKEN") or os.getenv("HA_TOKEN")
    nc_pass = os.getenv(f"NEXTCLOUD_{user}_PASS") or os.getenv("NEXTCLOUD_PASS")
    return {"user": user, "ha_token": ha_token, "nc_pass": nc_pass}

# ------------------
# Global Resources (The Performance Fix)
# ------------------
# We use a global class to hold the loaded models so they persist across requests
class GlobalResources:
    embedding_model = None
    chroma_client = None
    nextcloud_collection = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Initialize heavy resources (Embeddings, Vector DB) once on startup.
    """
    log.info("--- STARTUP: Loading Embedding Model & Vector DB ---")
    try:
        # Prevent Chroma from sending telemetry logs which clutter output
        os.environ["ANONYMIZED_TELEMETRY"] = "False"
        
        from langchain_huggingface import HuggingFaceEmbeddings
        from langchain_chroma import Chroma
        
        log.info(f"Loading embedding model: {EMB_MODEL} ...")
        GlobalResources.embedding_model = HuggingFaceEmbeddings(model_name=EMB_MODEL)
        
        log.info(f"Connecting to ChromaDB at {CHROMA_DIR} ...")
        GlobalResources.chroma_client = Chroma(
            persist_directory=CHROMA_DIR,
            embedding_function=GlobalResources.embedding_model
        )
        
        # Pre-load the specific collection used for Nextcloud RAG
        GlobalResources.nextcloud_collection = Chroma(
            collection_name="nextcloud_docs",
            embedding_function=GlobalResources.embedding_model,
            persist_directory=CHROMA_DIR
        )
        log.info("RAG Resources initialized successfully.")
        
    except Exception as e:
        log.critical(f"CRITICAL: Failed to initialize RAG resources: {e}")
        log.critical(traceback.format_exc())
    
    yield
    
    log.info("--- SHUTDOWN: Cleaning up resources ---")
    GlobalResources.embedding_model = None
    GlobalResources.chroma_client = None

# Document compatibility check
try:
    from langchain_core.documents import Document
except Exception:
    try:
        from langchain.schema import Document
    except Exception:
        Document = None

# ------------------
# FastAPI app
# ------------------
app = FastAPI(title="Unified RAG API", lifespan=lifespan)

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
            if not v: return None
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
        if not rec: return None
        ts, val = rec
        if time.time() - ts > HA_CACHE_TTL:
            del _ha_cache[user]
            return None
        return val

async def ha_cache_set(user: str, value: str):
    async with _ha_cache_lock:
        _ha_cache[user] = (time.time(), value)

# ------------------
# Utility: run blocking fn in threadpool
# ------------------
async def run_blocking(fn, *args, **kwargs):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(EXECUTOR, partial(fn, *args, **kwargs))

# ------------------
# Requests wrappers
# ------------------
def _requests_post(url, json=None, headers=None, timeout=None, stream=False):
    return requests.post(url, json=json, headers=headers, timeout=timeout, stream=stream)

def _requests_get(url, headers=None, timeout=None, stream=False):
    return requests.get(url, headers=headers, timeout=timeout, stream=stream)

async def requests_post(url, json=None, headers=None, timeout=None, stream=False):
    return await run_blocking(_requests_post, url, json, headers, timeout, stream)

async def requests_get(url, headers=None, timeout=None, stream=False):
    return await run_blocking(_requests_get, url, headers, timeout, stream)

# ------------------
# Ollama helper (NDJSON streaming aware)
# ------------------
async def call_ollama_generate(prompt: str, model: str = DEFAULT_MODEL, stream: bool = False, timeout: int = OLLAMA_TIMEOUT):
    url = f"{OLLAMA_URL.rstrip('/')}/api/generate"
    payload = {"model": model, "prompt": prompt, "stream": stream}
    headers = {"Content-Type": "application/json"}
    last_exc = None
    
    for attempt in range(max(1, OLLAMA_RETRY)):
        try:
            resp = await requests_post(url, json=payload, headers=headers, timeout=timeout, stream=True)
            resp.raise_for_status()
            r = resp
            break
        except Exception as e:
            last_exc = e
            log.warning("Ollama request attempt %d failed: %s", attempt + 1, e)
            await asyncio.sleep(0.2)
    else:
        log.exception("Ollama request failed after retries: %s", last_exc)
        raise HTTPException(status_code=502, detail=f"Ollama request failed: {last_exc}")

    content_type = r.headers.get("Content-Type", "")
    # streaming NDJSON case
    if stream or "ndjson" in content_type or "application/x-ndjson" in content_type:
        def generator_sync():
            try:
                for raw_line in r.iter_lines(decode_unicode=True):
                    if not raw_line: continue
                    try:
                        obj = json.loads(raw_line)
                        # Yield the FULL object so the endpoint can decide how to format it
                        yield obj
                        if obj.get("done") is True: break
                    except Exception:
                        # If json parse fails, yield text
                        yield raw_line + "\n"
            except Exception as e:
                log.warning("Error while streaming from Ollama: %s", e)
                return

        async def async_iter():
            loop = asyncio.get_running_loop()
            for chunk in await loop.run_in_executor(EXECUTOR, lambda: list(generator_sync())):
                yield chunk
        return {"iterable": async_iter}

    # non-stream
    try:
        data = r.json()
        # Normalize response text
        text = data.get("text") or data.get("response") or data.get("output") or ""
        return {"text": text}
    except Exception:
        return {"text": r.text}

# ------------------
# OpenAI helper
# ------------------
async def call_openai_chat(messages: List[Dict[str, str]], model: Optional[str] = None, stream: bool = False):
    model = model or OPENAI_MODEL
    if openai is None:
        raise HTTPException(status_code=501, detail="OpenAI library not installed")
    try:
        if stream:
            def gen_sync():
                resp = openai.ChatCompletion.create(model=model, messages=messages, stream=True)
                for chunk in resp:
                    yield chunk # yield raw chunk
            async def async_iter():
                loop = asyncio.get_running_loop()
                for chunk in await loop.run_in_executor(EXECUTOR, lambda: list(gen_sync())):
                    yield chunk
            return {"iterable": async_iter}
        else:
            def do_call():
                return openai.ChatCompletion.create(model=model, messages=messages)
            resp = await run_blocking(do_call)
            text = resp.choices[0].message.content
            return {"text": text}
    except Exception as e:
        log.exception("OpenAI call failed: %s", e)
        raise HTTPException(status_code=502, detail=f"OpenAI request failed: {e}")

# ------------------
# Home Assistant + Nextcloud context
# ------------------
async def get_ha_context(user: Optional[str] = None, limit: int = 200) -> str:
    creds = get_user_creds(user)
    user_key = creds["user"]
    
    # Cache check
    cached = await ha_cache_get(user_key)
    if cached is not None: return cached

    ha_token = creds["ha_token"]
    if not HA_URL or not ha_token: return ""

    try:
        headers = {"Authorization": f"Bearer {ha_token}"}
        # Reduced timeout to prevent long hangs if HA is down
        resp = await requests_get(f"{HA_URL.rstrip('/')}/api/states", headers=headers, timeout=(3.0, 5.0))
        resp.raise_for_status()
        states = resp.json()
        lines = []
        # Filter irrelevant states
        for s in states[:limit]:
            if s.get("state") in ["unavailable", "unknown"]: continue
            eid = s.get("entity_id")
            st = s.get("state")
            lines.append(f"{eid}: {st}")
            
        ctx = "Home Assistant snapshot for user %s:\n%s" % (creds['user'], "\n".join(lines))
        await ha_cache_set(user_key, ctx)
        return ctx
    except Exception as e:
        log.exception("Failed to fetch HA context: %s", e)
        return ""

async def get_nextcloud_context(query: str, user: Optional[str] = None, k: int = 4) -> str:
    cache_key = f"nc:{user or 'default'}:{query}"
    cached = await _query_cache.get(cache_key)
    if cached is not None: return cached

    # Use GlobalResources instead of re-initializing
    if not GlobalResources.nextcloud_collection:
        return ""

    def search_sync():
        try:
            return GlobalResources.nextcloud_collection.similarity_search_with_score(query, k=k)
        except Exception as e:
            log.error("Chroma search failed: %s", e)
            return []

    docs_with_scores = await run_blocking(search_sync)

    if not docs_with_scores:
        await _query_cache.set(cache_key, "")
        return ""

    parts = []
    for d, score in docs_with_scores:
        content = getattr(d, "page_content", "") or d.get("page_content", "")
        meta = getattr(d, "metadata", {}) or d.get("metadata", {})
        path = meta.get("path", "N/A")
        if content.strip():
            parts.append(f"[Source: Nextcloud, Path: {path}, Score: {score:.4f}]\n{content}")

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
# Helper: RAG Streaming Logic (The Protocol Fix)
# ------------------
async def stream_rag_result(query: str, user: str, model: str, use_openai: bool, format_type: str):
    """
    Streams RAG results while ensuring correct JSON protocol for the client (Ollama vs OpenAI).
    """
    ha_ctx = await get_ha_context(user=user)
    nc_ctx = await get_nextcloud_context(query, user=user)
    combined_context = "\n\n".join([c for c in (ha_ctx, nc_ctx) if c])
    
    prompt = f"""You are a local AI assistant with access to Home Assistant data and Nextcloud docs.
Context:
{combined_context}

User question:
{query}

Answer:"""

    # 1. OpenAI Stream Handling
    if use_openai and openai:
        resp = await call_openai_chat(
            messages=[{"role": "system", "content": "You are a helpful assistant."},
                      {"role": "user", "content": prompt}],
            model=OPENAI_MODEL,
            stream=True
        )
        
        async def openai_fmt():
            async for chunk in resp["iterable"]():
                # Standard OpenAI stream passthrough
                # But if the client expects SSE "data: {...}", ensure we verify format
                # OpenAI library usually yields objects. We might need to serialize if this endpoint implies raw SSE.
                # For /v1/chat/completions compat:
                content = chunk.choices[0].delta.get("content", "") if hasattr(chunk, "choices") else ""
                yield f"data: {json.dumps({'choices': [{'delta': {'content': content}}]})}\n\n"
            yield "data: [DONE]\n\n"
            
        return StreamingResponse(openai_fmt(), media_type="text/event-stream")

    # 2. Ollama Stream Handling (The Fix for Open WebUI)
    r = await call_ollama_generate(prompt=prompt, model=model, stream=True)
    
    if "iterable" in r:
        async def ollama_fmt():
            async for chunk in r["iterable"]():
                # chunk is a Dict (from call_ollama_generate logic)
                if not isinstance(chunk, dict): 
                    yield str(chunk) + "\n"
                    continue
                
                # Logic: Convert "Generate" response -> "Chat" response
                # OpenWebUI looks for 'message' object in the stream
                if format_type == "chat" and "response" in chunk and "message" not in chunk:
                    new_chunk = {
                        "model": chunk.get("model", model),
                        "created_at": chunk.get("created_at"),
                        "message": {
                            "role": "assistant",
                            "content": chunk.get("response", "")
                        },
                        "done": chunk.get("done", False)
                    }
                    yield json.dumps(new_chunk) + "\n"
                else:
                    # Pass through as-is (for /generate or if already correct)
                    yield json.dumps(chunk) + "\n"

        return StreamingResponse(ollama_fmt(), media_type="application/x-ndjson")
    
    return JSONResponse({"error": "Stream failed"})

# ------------------
# RAG endpoints (primary)
# ------------------

# Legacy/Internal Endpoint
@app.post("/rag/query")
async def rag_query(body: CompletionRequest, request: Request):
    header_user = request.headers.get("X-RAG-User")
    user = header_user or body.user or HA_DEFAULT_USER
    query = body.query
    if not query and body.messages:
        query = body.messages[-1].content
    if not query: raise HTTPException(status_code=400, detail="No query provided")

    ha_ctx = await get_ha_context(user=user)
    nc_ctx = await get_nextcloud_context(query, user=user)
    combined_context = "\n\n".join([c for c in (ha_ctx, nc_ctx) if c])

    if DEBUG_RAG_CONTEXT:
        return {"debug": True, "query": query, "context": combined_context}

    prompt = f"""You are a local AI assistant.
Context:
{combined_context}
User question:
{query}
Answer:"""

    model = body.model or DEFAULT_MODEL
    
    if body.use_openai and openai:
        resp = await call_openai_chat(messages=[{"role":"user", "content": prompt}], model=OPENAI_MODEL)
        return {"id": f"rag-{int(time.time())}", "user": user, "response": resp["text"]}
    
    resp = await call_ollama_generate(prompt=prompt, model=model)
    return {"id": f"rag-{int(time.time())}", "user": user, "response": resp["text"]}

# ------------------
# 1. API Chat (Ollama Compatible - UI FIX HERE)
# ------------------
@app.post("/api/chat")
async def api_chat(body: CompletionRequest, request: Request):
    """
    Endpoint used by Open WebUI. 
    If stream=True, we must yield objects with {"message": ...}
    If stream=False, we must return a JSON with {"message": ...}
    """
    header_user = request.headers.get("X-RAG-User")
    user = header_user or body.user or HA_DEFAULT_USER
    query = body.query or (body.messages[-1].content if body.messages else "")
    
    if body.stream:
        return await stream_rag_result(query, user, body.model or DEFAULT_MODEL, body.use_openai, format_type="chat")

    # Non-Streaming Logic
    ha_ctx = await get_ha_context(user=user)
    nc_ctx = await get_nextcloud_context(query, user=user)
    prompt = f"Context:\n{ha_ctx}\n{nc_ctx}\n\nUser: {query}\nAnswer:"

    if body.use_openai and openai:
        resp = await call_openai_chat(messages=[{"role":"user", "content": prompt}], model=OPENAI_MODEL)
        text = resp["text"]
    else:
        resp = await call_ollama_generate(prompt=prompt, model=body.model or DEFAULT_MODEL)
        text = resp["text"]

    # OLLAMA FORMAT RESPONSE
    return {
        "model": body.model or DEFAULT_MODEL,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "message": {
            "role": "assistant",
            "content": text
        },
        "done": True
    }

# ------------------
# 2. OpenAI Compatible Chat
# ------------------
@app.post("/v1/chat/completions")
@app.post("/api/chat/completions")
async def v1_chat(body: CompletionRequest, request: Request):
    header_user = request.headers.get("X-RAG-User")
    user = header_user or body.user or HA_DEFAULT_USER
    query = body.query or (body.messages[-1].content if body.messages else "")

    if body.stream:
        # Use the same streamer but we can infer formatting if we added more logic.
        # For now, the OpenAI branch in stream_rag_result handles true OpenAI models.
        # If using Ollama-as-OpenAI, the client usually handles the translation, 
        # or we would need a specific "openai" format_type in stream_rag_result.
        return await stream_rag_result(query, user, body.model or DEFAULT_MODEL, body.use_openai, format_type="chat")

    ha_ctx = await get_ha_context(user=user)
    nc_ctx = await get_nextcloud_context(query, user=user)
    prompt = f"Context:\n{ha_ctx}\n{nc_ctx}\n\nUser: {query}\nAnswer:"

    if body.use_openai and openai:
        resp = await call_openai_chat(messages=[{"role":"user", "content": prompt}], model=OPENAI_MODEL)
        text = resp["text"]
    else:
        resp = await call_ollama_generate(prompt=prompt, model=body.model or DEFAULT_MODEL)
        text = resp["text"]

    # OPENAI FORMAT RESPONSE
    return {
        "id": f"chatcmpl-{int(time.time())}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": body.model,
        "choices": [{
            "index": 0,
            "message": {
                "role": "assistant",
                "content": text
            },
            "finish_reason": "stop"
        }],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    }

# ------------------
# 3. Generic Stream Endpoint
# ------------------
@app.post("/chat/stream")
async def chat_stream(body: CompletionRequest, request: Request):
    header_user = request.headers.get("X-RAG-User")
    user = header_user or body.user or HA_DEFAULT_USER
    query = body.query or (body.messages[-1].content if body.messages else "")
    return await stream_rag_result(query, user, body.model or DEFAULT_MODEL, body.use_openai, format_type="chat")

# ------------------
# 4. Direct Generate Endpoints
# ------------------
@app.post("/generate")
async def generate(req: GenerateRequest):
    if req.use_openai and openai:
        resp = await call_openai_chat(messages=[{"role": "user", "content": req.prompt}], model=OPENAI_MODEL, stream=False)
        return {"text": resp.get("text")}
    r = await call_ollama_generate(prompt=req.prompt, model=req.model or DEFAULT_MODEL, stream=False)
    return {"text": r.get("text")}

@app.post("/generate/stream")
async def generate_stream(req: GenerateRequest):
    if req.use_openai and openai:
        resp = await call_openai_chat(messages=[{"role": "user", "content": req.prompt}], model=OPENAI_MODEL, stream=True)
        return StreamingResponse(resp["iterable"](), media_type="text/event-stream")
    
    r = await call_ollama_generate(prompt=req.prompt, model=req.model or DEFAULT_MODEL, stream=True)
    if "iterable" in r:
        # Simple passthrough for /generate
        async def fmt():
            async for c in r["iterable"](): yield json.dumps(c) + "\n"
        return StreamingResponse(fmt(), media_type="text/event-stream")
    return JSONResponse({"text": r.get("text")})

# ------------------
# 5. Manual RAG Operations (Using GlobalResources)
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
            out = []
            coll = GlobalResources.chroma_client._collection
            cnt = coll.count()
            peek_n = min(limit, cnt)
            samples = coll.peek(peek_n) if peek_n > 0 else {"documents": [], "metadatas": [], "ids": []}
            
            for i, doc_id in enumerate(samples.get("ids", [])):
                meta = samples.get("metadatas", [])[i] if samples.get("metadatas") else {}
                content = samples.get("documents", [])[i] if samples.get("documents") else ""
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
# 6. Ingest script runner helpers
# ------------------
def _run_script_sync(script_path: str):
    stdout_accum, stderr_accum = [], []
    try:
        proc = subprocess.Popen(
            ["python", script_path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1
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
            "code": proc.returncode
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
        "nextcloud": await _run_script("ingest_nextcloud.py")
    }

# ------------------
# 7. Ollama passthrough endpoints (preserve UI needs)
# ------------------
@app.get("/v1/models")
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
    except Exception: pass
    if openai:
        try: out["openai"] = openai.Model.list()
        except: pass
    return out

@app.get("/api/tags")
async def ollama_tags():
    try:
        resp = await requests_get(f"{OLLAMA_URL.rstrip('/')}/api/tags", timeout=10)
        return resp.json()
    except Exception: return {"models": []}

@app.get("/api/ps")
async def ollama_ps():
    try:
        resp = await requests_get(f"{OLLAMA_URL.rstrip('/')}/api/ps", timeout=10)
        return resp.json()
    except Exception: return {"models": []}

@app.get("/api/version")
async def ollama_version():
    try:
        resp = await requests_get(f"{OLLAMA_URL.rstrip('/')}/api/version", timeout=5)
        return {"service": "unified-rag", "ollama": resp.json()}
    except Exception as e:
        return {"service": "unified-rag", "error": str(e)}

# ------------------
# 8. Health, ping, root, debug
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
        "debug_mode": DEBUG
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
    return JSONResponse(status_code=500, content={"detail": str(exc), "trace": tb[:2000]})

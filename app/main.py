import time
import json
import os
import subprocess
import requests
import asyncio
from contextlib import asynccontextmanager
from typing import List, Optional, Dict, Any

from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Fixed imports with Fallback
try:
    from app.settings import (
        get_user_creds, run_blocking, GlobalResources, log,
        DEFAULT_MODEL, OPENAI_MODEL, OLLAMA_URL, openai_client, OPENAI_API_KEY, HA_URL,
        load_resources
    )
except ImportError:
    from settings import (
        get_user_creds, run_blocking, GlobalResources, log,
        DEFAULT_MODEL, OPENAI_MODEL, OLLAMA_URL, openai_client, OPENAI_API_KEY, HA_URL,
        load_resources
    )
from app.logic import (
    generate_rag_stream, contextualize_query, try_handle_compound_command, 
    call_ollama_generate, call_openai_chat, 
    get_ha_context, get_rag_context, update_history
)
from app.logic.refresh_devices import refresh_db
from app.intent_engine import engine as intent_engine
from app.logic.timer_storage import storage as timer_storage
from app.routers import music_assistant

async def initialize_rag_resources():
    """Reloads RAG resources for hot-reloading."""
    await load_resources()
    from app.intent_engine import engine

    await engine.load()

# --- LIFESPAN (Startup Logic) ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    await load_resources()

    # Initialize Intent Engine
    from app.intent_engine import engine
    await engine.load()

    # Start Device DB Refresh (Async)
    from app.logic.refresh_devices import refresh_db
    asyncio.create_task(refresh_db())

    # Start Timer Scheduler
    from app.logic.timer_scheduler import start_scheduler, stop_scheduler
    log.info("Starting Timer/Alarm Scheduler...")
    scheduler_task = asyncio.create_task(start_scheduler())

    # Start Video Cache Cleanup
    from app.utils.video_cache import schedule_periodic_cleanup
    asyncio.create_task(schedule_periodic_cleanup())

    yield

    # Shutdown
    log.info("--- SHUTDOWN: Cleaning up resources ---")
    await stop_scheduler()
    try:
        scheduler_task.cancel()
    except:
        pass

    GlobalResources.embedding_model = None
    GlobalResources.chroma_client = None
    GlobalResources.ha_collection = None
    GlobalResources.nextcloud_collection = None
    if GlobalResources.redis_client:
        try:
            await scheduler_task
        except asyncio.CancelledError:
            pass

    if GlobalResources.redis_client:
        GlobalResources.redis_client.close()
    log.info("Shutdown complete.")

app = FastAPI(title="Unified RAG API", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

# Register Routers
app.include_router(music_assistant.router)
from app.routers import android_tv
from app.routers import webos
from app.routers import roku
from app.endpoints import cast_video
from app.domains.media.dlna import video_server

# Cast video streaming endpoint
app.include_router(cast_video.router, tags=["cast"])
# DLNA Router
app.include_router(video_server.router)
# TV Integration Routers
app.include_router(android_tv.router)
app.include_router(webos.router)
app.include_router(roku.router)
from app.routers import context
from app.routers import music_assistant # Added import
app.include_router(context.router)
app.include_router(music_assistant.router) # Added include

# --- Models ---
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

class GenerateRequest(BaseModel):
    prompt: str
    model: Optional[str] = DEFAULT_MODEL
    stream: Optional[bool] = False
    use_openai: Optional[bool] = False

class UpsertRagRequest(BaseModel):
    id: Optional[str] = None
    text: str
    metadata: Optional[Dict[str, Any]] = None

class LearnRequest(BaseModel):
    phrase: str
    intent: str

# --- Endpoints ---

@app.post("/v1/chat/completions")
@app.post("/api/chat")
@app.post("/chat/completions")
async def chat_endpoint(body: CompletionRequest, request: Request):
    user = request.headers.get("X-RAG-User") or body.user or "admin"
    query = body.query or (body.messages[-1].content if body.messages else "")
    if not query: raise HTTPException(400, detail="No query")
    
    format_type = "openai" if "completions" in request.url.path else "chat"

    generator = generate_rag_stream(query, user, body.model, body.use_openai, format_type)

    if body.stream:
        media_type = "text/event-stream" if format_type == "openai" else "application/x-ndjson"
        return StreamingResponse(generator, media_type=media_type)

    full_text = ""
    try:
        async for chunk in generator:
            try:
                if chunk.startswith("data: "): 
                    if "[DONE]" in chunk: continue
                    d = json.loads(chunk.replace("data: ", ""))
                    if "choices" in d:
                        full_text += d["choices"][0]["delta"].get("content", "")
                else:
                    d = json.loads(chunk)
                    if "message" in d:
                        full_text += d["message"].get("content", "")
                    elif "response" in d:
                        full_text += d.get("response", "")
            except: pass
    except Exception as e:
        log.error(f"Error accumulating response: {e}")
    
    if format_type == "openai":
        response = {
            "id": f"chat-{int(time.time())}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": body.model,
            "choices": [{"message": {"role": "assistant", "content": full_text}, "finish_reason": "stop", "index": 0}]
        }
        log.debug(f"[RESPONSE] Returning to client: {full_text[:200]}")
        return response
    
    response = {
        "model": body.model, 
        "created_at": time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        "message": {"role": "assistant", "content": full_text}, 
        "done": True
    }
    log.debug(f"[RESPONSE] Returning to client: {full_text[:200]}")
    return response

# --- Intent Engine Endpoints ---
@app.post("/api/intent/learn")
async def intent_learn(req: LearnRequest):
    """Teach the AI a new phrase -> intent mapping."""
    success = await intent_engine.learn(req.phrase, req.intent)
    if success:
        return {"status": "learned", "msg": f"Mapped '{req.phrase}' to '{req.intent}'"}
    return {"status": "exists", "msg": "Phrase already mapped or invalid intent."}

@app.get("/api/intent/export")
async def intent_export():
    """Force save the phrasebook to disk."""
    if await intent_engine.export():
        return {"status": "ok", "msg": "Phrasebook saved to disk."}
    raise HTTPException(500, "Export failed")

@app.get("/api/intent/list")
async def intent_list():
    """List all available intents."""
    return {"intents": intent_engine.get_valid_intents()}

# --- Timer/Alarm API ---
@app.get("/api/timer/list")
async def api_timer_list():
    """Returns a raw JSON list of active timers."""
    return await timer_storage.list_timers()

@app.post("/api/timer/delete")
async def api_timer_delete(timer_id: str):
    await timer_storage.delete_timer(timer_id)
    return {"status": "ok", "msg": f"Timer {timer_id} deleted."}

@app.post("/api/admin/reindex")
async def admin_reindex(background_tasks: BackgroundTasks, request: Request):
    creds = get_user_creds(request.headers.get("X-RAG-User") or "admin")
    background_tasks.add_task(intent_engine.load)
    return {"status": "Re-indexing started"}

@app.post("/api/admin/refresh_devices")
async def admin_refresh_devices(background_tasks: BackgroundTasks, request: Request):
    """Triggers a full refresh of the Device DB for grouping."""
    background_tasks.add_task(refresh_db)
    return {"status": "Device DB Refresh started"}

# --- HA Proxy ---
@app.get("/api/ha/state/{entity_id}")
async def get_ha_state_proxy(entity_id: str, request: Request):
    creds = get_user_creds(request.headers.get("X-RAG-User") or "admin")
    if not HA_URL: return {"error": "No HA URL"}
    try:
        headers = {"Authorization": f"Bearer {creds['ha_token']}"}
        r = requests.get(f"{HA_URL.rstrip('/')}/api/states/{entity_id}", headers=headers, timeout=5)
        if r.status_code == 200: return r.json()
        return {"error": r.status_code, "msg": r.text}
    except Exception as e: return {"error": str(e)}

@app.post("/rag/query")
async def rag_query(body: CompletionRequest, request: Request):
    user = request.headers.get("X-RAG-User") or body.user or "admin"
    query = body.query or (body.messages[-1].content if body.messages else "")
    refined, intent, score, is_high_confidence = await contextualize_query(query, user, body.model)
    creds = get_user_creds(user)
    cmd = await try_handle_compound_command(refined, creds, body.model, intent, score, is_high_confidence)
    if cmd: return {"response": cmd}
    ha = await get_ha_context(user, query=refined)
    nc = await get_rag_context(refined)
    return {"response": f"Context:\n{ha}\n{nc}"}

# --- System & Models ---
@app.get("/v1/models")
@app.get("/models")
@app.get("/api/tags")
async def models():
    try: return requests.get(f"{OLLAMA_URL.rstrip('/')}/api/tags", timeout=5).json()
    except: return {"models": []}
@app.get("/api/ps")
async def ps():
    try: return requests.get(f"{OLLAMA_URL.rstrip('/')}/api/ps", timeout=5).json()
    except: return {}
@app.get("/api/version")
async def ver():
    try: return requests.get(f"{OLLAMA_URL.rstrip('/')}/api/version", timeout=3).json()
    except: return {}

@app.post("/generate")
@app.post("/api/generate")
async def generate(req: GenerateRequest):
    r = await call_ollama_generate(req.prompt, req.model, stream=False)
    return {
        "model": req.model,
        "created_at": time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        "response": r.get("text", ""),
        "done": True
    }

# --- Ingestion Endpoints with Background Tasks ---
def _run_sync(path):
    """Runs a python script and captures both stdout and stderr."""
    try: 
        result = subprocess.run(
            ["python", path], 
            capture_output=True, 
            text=True
        )
        output = result.stdout
        if result.stderr:
            output += f"\n[STDERR]\n{result.stderr}"
        return output
    except Exception as e: 
        return f"Error executing subprocess: {e}"

async def _run_background_ingest(script_name: str):
    """Helper to run ingestion script in thread pool then reload RAG."""
    log.info(f"--- Started Background Ingestion: {script_name} ---")
    output = await run_blocking(_run_sync, f"/app/{script_name}")
    
    # Log output for debugging
    log.info(f"--- {script_name} Finished ---")
    if len(output) > 1000:
        log.info(f"Output snippet: {output[:500]} ... {output[-500:]}")
    else:
        log.info(f"Output: {output}")

    if "[STDERR]" in output or "CRITICAL" in output:
        log.error(f"Ingestion {script_name} reported errors. Check logs above.")
    
    # Auto-reload after completion
    log.info("Reloading RAG Resources...")
    await initialize_rag_resources()
    log.info("RAG Resources Reloaded.")

@app.post("/ingest/ha")
async def ing_ha(bg_tasks: BackgroundTasks):
    bg_tasks.add_task(_run_background_ingest, "ha_ingest.py")
    return {"status": "accepted", "msg": "Home Assistant ingestion started in background."}

@app.post("/ingest/nextcloud")
async def ing_nc(bg_tasks: BackgroundTasks):
    bg_tasks.add_task(_run_background_ingest, "ingest_nextcloud.py")
    return {"status": "accepted", "msg": "Nextcloud ingestion started in background."}

@app.post("/ingest/all")
async def ing_all(bg_tasks: BackgroundTasks):
    bg_tasks.add_task(_run_background_ingest, "ha_ingest.py")
    bg_tasks.add_task(_run_background_ingest, "ingest_nextcloud.py")
    return {"status": "accepted", "msg": "Full ingestion started in background."}

# --- NEW: Hot Reload Endpoint ---
@app.post("/api/system/reload")
async def system_reload():
    """Forces a reload of RAG resources (Vector DB, Intent Engine) without restarting the process."""
    await initialize_rag_resources()
    return {"status": "ok", "msg": "RAG resources reloaded from disk."}

# --- RAG Management ---
@app.post("/api/rag/upsert")
async def rag_upsert(i: UpsertRagRequest):
    if not GlobalResources.chroma_client: 
        raise HTTPException(503, detail="ChromaDB not initialized")
    try:
        from langchain_core.documents import Document
        doc = Document(page_content=i.text, metadata=i.metadata or {})
        await run_blocking(lambda: (GlobalResources.chroma_client.add_documents([doc]), GlobalResources.chroma_client.persist()))
        return {"status": "ok"}
    except Exception as e:
        log.error(f"RAG Upsert Failed: {e}")
        raise HTTPException(500, detail=f"Database Write Error: {str(e)}")

@app.post("/api/rag/delete")
async def rag_delete(id: str):
    if not GlobalResources.chroma_client: 
        raise HTTPException(503, detail="ChromaDB not initialized")
    try:
        await run_blocking(lambda: GlobalResources.chroma_client.delete(ids=[id]))
        return {"status": "ok"}
    except Exception as e:
        log.error(f"RAG Delete Failed: {e}")
        raise HTTPException(500, detail=f"Database Delete Error: {str(e)}")

@app.get("/api/rag/list")
async def rag_list(limit: int = 100):
    if not GlobalResources.chroma_client: raise HTTPException(503)
    def sync():
        results = {"count": 0, "docs": []}
        for name in ["nextcloud_docs", "home_assistant"]:
            try:
                c = GlobalResources.chroma_client.get_collection(name)
                cnt = c.count()
                results["count"] += cnt
                s = c.peek(min(limit, cnt)) if cnt else {}
                for i, id in enumerate(s.get("ids", [])):
                    results["docs"].append({
                        "id": id, 
                        "collection": name,
                        "preview": s["documents"][i][:200]
                    })
            except Exception:
                pass # Collection might not exist yet
        return results
    return await run_blocking(sync)

@app.get("/api/rag/search")
async def rag_search(q: str, k: int = 4, source: Optional[str] = None):
    """
    Search the vector DB. 
    Optional 'source' param: 'ha' (Home Assistant) or 'nextcloud' (Documents).
    If omitted, searches both.
    """
    results = []
    
    # Determine which collections to search
    search_ha = (source is None or source == 'ha')
    search_nc = (source is None or source == 'nextcloud')
    
    # 1. Search Home Assistant (if enabled)
    if search_ha and GlobalResources.ha_collection:
        try:
            ha_docs = await run_blocking(lambda: GlobalResources.ha_collection.similarity_search(q, k=k))
            results.extend([{"text": d.page_content, "metadata": d.metadata, "source": "home_assistant"} for d in ha_docs])
        except Exception as e: log.error(f"Error searching HA collection: {e}")
        
    # 2. Search Nextcloud (if enabled)
    if search_nc and GlobalResources.nextcloud_collection:
        try:
            nc_docs = await run_blocking(lambda: GlobalResources.nextcloud_collection.similarity_search(q, k=k))
            results.extend([{"text": d.page_content, "metadata": d.metadata, "source": "nextcloud"} for d in nc_docs])
        except Exception as e: log.error(f"Error searching Nextcloud collection: {e}")
        
    return {"results": results}

@app.post("/context/update")
async def update_context(r: Request):
    d = await r.json()
    if not GlobalResources.chroma_client: raise HTTPException(503)
    from langchain_core.documents import Document
    await run_blocking(lambda: (GlobalResources.chroma_client.add_documents([Document(page_content=d.get("text",""), metadata={"source":"manual"})]), GlobalResources.chroma_client.persist()))
    return {"status": "ok"}

# --- Diagnostics ---
@app.get("/health")
async def health(): return {"status": "ok", "db": GlobalResources.chroma_client is not None}
@app.get("/")
async def root(): return {"service": "agentic-rag-api", "status": "active"}
@app.get("/api/ping")
async def ping(): return {"ok": True}

@app.exception_handler(HTTPException)
async def http_exception_handler(r, e): return JSONResponse(status_code=e.status_code, content={"detail": e.detail})

@app.exception_handler(Exception)
async def generic_exception_handler(r, e):
    log.exception("Error")
    return JSONResponse(status_code=500, content={"detail": str(e)})

# --- Admin Endpoints ---
@app.get("/api/admin/logs")
async def admin_logs(lines: int = 100):
    """Read the last N lines of the application log file."""
    log_file = "/data/app.log"
    if not os.path.exists(log_file):
        return {"error": "Log file not found"}
    try:
        # Simple tail implementation
        with open(log_file, "r") as f:
            all_lines = f.readlines()
            return {"logs": all_lines[-lines:]}
    except Exception as e:
        return {"error": str(e)}

@app.post("/api/admin/run_tests")
async def admin_run_tests(background_tasks: BackgroundTasks):
    """Run the system verification suite."""
    from app.logic.test_runner import runner
    # We run it synchronously in a thread pool to avoid blocking the loop
    return await run_blocking(runner.run_all)

@app.get("/api/device/capabilities/{entity_id:path}")
async def get_device_capabilities(entity_id: str):
    """
    Query device capabilities from ChromaDB.
    Returns supported_features, color_modes, and parsed capability flags.
    
    Example: /api/device/capabilities/light.piano_lamp
    """
    try:
        from app.logic.media_ops import get_device_capabilities as get_caps
        user_creds = get_user_creds("default")
        redis_client = GlobalResources.redis_client
        
        capabilities = await get_caps(entity_id, user_creds, redis_client)
        
        # Add human-readable feature breakdown
        if "supported_features" in capabilities:
            features = capabilities["supported_features"]
            domain = capabilities.get("domain", "")
            
            if domain == "light":
                capabilities["features_breakdown"] = {
                    "brightness": bool(features & 1),
                    "color_temp": bool(features & 2),
                    "effect": bool(features & 4),
                    "flash": bool(features & 8),
                    "color": bool(features & 16),
                    "transition": bool(features & 32)
                }
            elif domain == "media_player":
                capabilities["features_breakdown"] = {
                    "pause": bool(features & 1),
                    "seek": bool(features & 2),
                    "volume": bool(features & 4),
                    "volume_mute": bool(features & 8),
                    "previous_track": bool(features & 16),
                    "next_track": bool(features & 32),
                    "turn_on": bool(features & 128),
                    "turn_off": bool(features & 256),
                    "play_media": bool(features & 512)
                }
        
        return capabilities
    except Exception as e:
        log.error(f"Error fetching capabilities for {entity_id}: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})

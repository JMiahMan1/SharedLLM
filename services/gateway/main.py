# services/gateway/main.py
import os
import logging
import json
import asyncio
import httpx
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, status, Request
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel
import re
from datetime import datetime

# --- Imports from internal modules ---
try:
    from .schemas import ChatRequest, ChatResponse, OllamaPullRequest, OllamaGenerateRequest
    from .intent_engine import engine
    from .history import get_history, update_history, ping_redis
except (ImportError, ValueError):
    try:
        from gateway.schemas import ChatRequest, ChatResponse, OllamaPullRequest, OllamaGenerateRequest
        from gateway.intent_engine import engine
        from gateway.history import get_history, update_history, ping_redis
    except ImportError:
        from schemas import ChatRequest, ChatResponse, OllamaPullRequest, OllamaGenerateRequest
        from intent_engine import engine
        from history import get_history, update_history, ping_redis

# --- Setup Logging ---
log = logging.getLogger("gateway")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s")

# --- Configuration ---
IDENTITY_SVC = os.getenv("IDENTITY_SVC_URL", "http://identity:8001")
EXECUTION_SVC = os.getenv("EXECUTION_SVC_URL", "http://execution:8003")
RAG_SVC = os.getenv("RAG_SVC_URL", "http://rag:8004")
STORAGE_SVC = os.getenv("STORAGE_SVC_URL", "http://storage:8005")
LOGGING_SVC_URL = os.getenv("LOGGING_SVC_URL", "http://logging:8006")
INTERNAL_SECRET = os.getenv("INTERNAL_SECRET", "change-me-in-production")
FAST_PATH_THRESHOLD = float(os.getenv("FAST_PATH_THRESHOLD", "0.85"))
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://ollama:11434")

# --- Global Clients ---
_global_http_client: httpx.AsyncClient = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _global_http_client
    _global_http_client = httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0))
    log.info("Gateway starting up...")
    engine.load()
    yield
    await _global_http_client.aclose()
    log.info("Gateway shutting down...")

app = FastAPI(title="SOA Intent Gateway", version="1.0.0", lifespan=lifespan)

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    err_msg = f"Gateway Error: {type(exc).__name__}: {str(exc)}"
    log.error(f"{err_msg}\n{traceback.format_exc()}")
    await emit_log("ERROR", err_msg, {"trace": traceback.format_exc()})
    return JSONResponse(
        status_code=500,
        content={"status": "ERROR", "message": "Internal Gateway Error", "detail": str(exc)}
    )

# --- Global Health & Readiness ---
@app.get("/health")
def health():
    return {"status": "ok", "service": "gateway"}

@app.get("/health/ready")
async def readiness():
    """Verifies all downstream services are reachable."""
    services = {
        "identity": f"{IDENTITY_SVC}/health",
        "execution": f"{EXECUTION_SVC}/health",
        "rag": f"{RAG_SVC}/health",
        "storage": f"{STORAGE_SVC}/health",
        "logging": f"{LOGGING_SVC_URL}/health"
    }
    
    results = {"status": "READY", "services": {}}
    all_ok = True
    
    async with httpx.AsyncClient(timeout=2.0) as client:
        for name, url in services.items():
            try:
                resp = await client.get(url)
                if resp.status_code == 200:
                    results["services"][name] = "OK"
                else:
                    results["services"][name] = f"ERROR ({resp.status_code})"
                    all_ok = False
            except Exception as e:
                results["services"][name] = f"UNREACHABLE"
                all_ok = False
                
    if ping_redis():
        results["services"]["redis"] = "OK"
    else:
        results["services"]["redis"] = "UNREACHABLE"
        all_ok = False
        
    if not all_ok:
        results["status"] = "NOT_READY"
    return results

# --- Logging Helper ---
async def emit_log(level: str, message: str, context: dict = None):
    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{LOGGING_SVC_URL}/log",
                json={"service": "gateway", "level": level, "message": message, "context": context},
                headers={"X-Internal-Secret": INTERNAL_SECRET},
                timeout=1.0
            )
    except: pass

@app.get("/api/logs")
async def get_api_logs(limit: int = 50):
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{LOGGING_SVC_URL}/logs", params={"limit": limit})
        return resp.json()

# --- Contextualization Logic ---
async def contextualize_query(query: str, history: list) -> str:
    """Uses history to rewrite ambiguous queries like 'yes' or 'do it'."""
    if not history: return query
    
    q_lower = query.lower().strip().strip("!.")
    if len(q_lower.split()) > 4 and q_lower not in ["play the first one"]:
        return query

    hist_str = ""
    for m in history[-3:]:
        role = "USER" if m.get("role") == "user" else "ASSISTANT"
        hist_str += f"{role}: {m.get('content')}\n"

    prompt = f"Given history:\n{hist_str}\nRewrite follow-up to standalone command.\nFollow-up: {query}\nCommand:"
    try:
        payload = {"model": "llama3", "prompt": prompt, "stream": False, "options": {"temperature": 0.0}}
        resp = await _global_http_client.post(f"{OLLAMA_URL}/api/generate", json=payload, timeout=5.0)
        if resp.status_code == 200:
            rewritten = resp.json().get("response", query).strip().strip('"')
            log.info(f"[Context] '{query}' -> '{rewritten}'")
            return rewritten
    except: pass
    return query

# --- Helper Functions ---
async def decompose_command_query(query: str) -> list[str]:
    if " and " not in query.lower() and " then " not in query.lower():
        return [query]
    parts = re.split(r'\s+(?:and|then)\s+', query, flags=re.IGNORECASE)
    return [p.strip() for p in parts if p.strip()]

async def resolve_identity(body: dict) -> dict:
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{IDENTITY_SVC}/api/resolve",
                json=body,
                headers={"X-Internal-Secret": INTERNAL_SECRET},
                timeout=5.0
            )
            if resp.status_code != 200:
                err_detail = f"Identity resolution failed: {resp.status_code} {resp.text}"
                log.error(err_detail)
                raise HTTPException(status_code=resp.status_code, detail=err_detail)
            return resp.json()
    except httpx.RequestError as e:
        log.error(f"Identity service unreachable: {e}")
        raise HTTPException(status_code=503, detail="Identity service unreachable")

async def fetch_ha_entities(creds: dict) -> list:
    try:
        resp = await _global_http_client.get(
            f"{EXECUTION_SVC}/discovery/entities",
            params={"ha_url": creds.get("ha_url"), "ha_token": creds.get("ha_token")},
            headers={"X-Internal-Secret": INTERNAL_SECRET},
            timeout=5.0
        )
        if resp.status_code != 200:
            log.warning(f"Failed to fetch entities: {resp.status_code}")
            return []
        
        entities = resp.json()
        if entities:
            # Async sync task
            asyncio.create_task(_global_http_client.post(
                f"{RAG_SVC}/rag/sync/ha",
                json={"entities": entities, "user_id": creds.get("user", "admin")},
                headers={"X-Internal-Secret": INTERNAL_SECRET}
            ))
        return entities
    except Exception as e:
        log.error(f"Entity discovery error: {e}")
        return []

@app.post("/api/discovery/sync")
async def discovery_sync(request: Request):
    """Orchestrates HA entity discovery and RAG sync."""
    body = await request.json()
    creds = await resolve_identity(body)
    entities = await fetch_ha_entities(creds)
    return {"status": "SUCCESS", "entities_count": len(entities)}

async def execute_command(endpoint: str, payload: dict) -> dict:
    try:
        resp = await _global_http_client.post(
            f"{EXECUTION_SVC}{endpoint}",
            json=payload,
            headers={"X-Internal-Secret": INTERNAL_SECRET},
            timeout=10.0
        )
        return resp.json()
    except Exception as e:
        return {"status": "FAILURE", "message": str(e)}

# --- Chat Handler ---
@app.post("/api/chat")
@app.post("/v1/chat/completions")
async def chat_handler(request: Request):
    body = await request.json()
    query = body.get("query") or (body.get("messages", [{}])[-1].get("content") if "messages" in body else "")
    if not query: return JSONResponse({"status": "ERROR", "message": "No query"}, status_code=400)

    # 1. Resolve Identity & Context
    creds = await resolve_identity(body)
    user_id = creds.get("user", "admin")
    history = await get_history(user_id)
    real_entities = await fetch_ha_entities(creds)
    
    await emit_log("INFO", f"Chat request from {user_id}", {"query": query, "entities_count": len(real_entities)})

    # 2. Contextualize & Decompose
    refined_query = await contextualize_query(query, history)
    sub_commands = await decompose_command_query(refined_query)
    
    # 3. Fast Path (Semantic Routing)
    intent, confidence = engine.classify(refined_query)
    if confidence >= FAST_PATH_THRESHOLD:
        log.info(f"[FastPath] intent='{intent}' confidence={confidence}")
        
        # Simple routing map
        endpoint_map = {
            "turn_on": "/execute/light",
            "turn_off": "/execute/light",
            "play_media": "/execute/media/play",
            "pause_media": "/execute/media/transport",
            "open_garage": "/execute/security",
            "close_garage": "/execute/security"
        }
        
        endpoint = endpoint_map.get(intent)
        if endpoint:
            # Smart-ish entity resolution for stubs
            target_entity = "auto"
            
            # 1. Try to find a match in real_entities based on the query
            query_lower = refined_query.lower()
            for e in real_entities:
                friendly_name = e.get("attributes", {}).get("friendly_name", "").lower()
                eid = e.get("entity_id", "").lower()
                
                # If name mentioned in query, and type matches intent
                if friendly_name and friendly_name in query_lower:
                    if "media" in intent and eid.startswith("media_player."):
                        target_entity = e["entity_id"]
                        break
                    if ("light" in intent or "turn" in intent) and eid.startswith("light."):
                        target_entity = e["entity_id"]
                        break
            
            # 2. Fallback to first available of type
            if target_entity == "auto":
                if "media" in intent:
                    players = [e for e in real_entities if e['entity_id'].startswith('media_player.')]
                    if players: target_entity = players[0]['entity_id']
                elif "light" in intent or "turn" in intent:
                    lights = [e for e in real_entities if e['entity_id'].startswith('light.')]
                    if lights: target_entity = lights[0]['entity_id']

            exec_payload = {
                "user_context": creds,
                "entity_id": target_entity,
                "action": "turn_on" if intent == "turn_on" else ("turn_off" if intent == "turn_off" else "play")
            }
            
            # For media, add default content
            if intent == "play_media":
                exec_payload["media_content_id"] = "http://stream.radioparadise.com/flac"
                exec_payload["media_content_type"] = "music"

            exec_res = await execute_command(endpoint, exec_payload)
            return JSONResponse({
                "status": "SUCCESS",
                "message": exec_res.get("message", "Executed"),
                "intent": intent,
                "confidence": confidence,
                "execution_result": exec_res
            })
    
    # 4. Proxy to Ollama (Slow Path)
    # 4. LLM Proxy (Slow Path)
    await emit_log("INFO", f"Slow path triggered for: {refined_query}")
    try:
        # Try /api/chat first (Ollama standard)
        ollama_payload = {
            "model": "llama3",
            "messages": history + [{"role": "user", "content": refined_query}],
            "stream": False
        }
        resp = await _global_http_client.post(f"{OLLAMA_URL}/api/chat", json=ollama_payload)
        
        if resp.status_code == 404:
            # Fallback to /api/generate for older Ollama versions
            log.warning("Ollama /api/chat not found, falling back to /api/generate")
            gen_payload = {
                "model": "llama3",
                "prompt": f"{refined_query}", # Simplified
                "stream": False
            }
            resp = await _global_http_client.post(f"{OLLAMA_URL}/api/generate", json=gen_payload)
            
        if resp.status_code != 200:
            err_msg = f"Ollama Error {resp.status_code}: {resp.text}"
            log.error(err_msg)
            return JSONResponse({"status": "ERROR", "message": "The brain is currently unavailable."}, status_code=502)
            
        data = resp.json()
        answer = data.get("message", {}).get("content") or data.get("response", "I encountered an error.")
        
        # Save to history
        await update_history(user_id, "user", query)
        await update_history(user_id, "assistant", answer)
        
        return JSONResponse({"status": "SUCCESS", "message": answer})
        
    except Exception as e:
        log.error(f"LLM Proxy Error: {e}")
        raise HTTPException(status_code=502, detail="Upstream LLM error")

# --- Ollama Proxy ---
@app.post("/api/generate")
async def proxy_generate(request: Request):
    body = await request.json()
    async with httpx.AsyncClient(timeout=None) as client:
        resp = await client.post(f"{OLLAMA_URL}/api/generate", json=body)
        return resp.json()

@app.get("/api/tags")
async def proxy_tags():
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{OLLAMA_URL}/api/tags")
        return resp.json()

@app.get("/api/version")
async def proxy_version():
    return {"version": "0.1.32"}

# services/gateway/main.py
"""
Microservice 2: Intent Gateway
Entry point for all clients. Coordinates Identity, RAG, Execution, and LLM.
"""
import os
import logging
from contextlib import asynccontextmanager
import json
import httpx
from fastapi import FastAPI, HTTPException, status, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from schemas import ChatRequest, ChatResponse, OllamaPullRequest, OllamaGenerateRequest
from intent_engine import engine

log = logging.getLogger("gateway")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s")

IDENTITY_SVC = os.getenv("IDENTITY_SVC_URL", "http://identity:8001")
EXECUTION_SVC = os.getenv("EXECUTION_SVC_URL", "http://execution:8003")
RAG_SVC = os.getenv("RAG_SVC_URL", "http://rag:8004")
INTERNAL_SECRET = os.getenv("INTERNAL_SECRET", "change-me-in-production")
FAST_PATH_THRESHOLD = float(os.getenv("FAST_PATH_THRESHOLD", "0.85"))
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://ollama:11434")

# LLM Config (simplified for gateway)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Gateway starting up...")
    engine.load()
    yield
    log.info("Gateway shutting down...")

from fastapi.responses import JSONResponse
import traceback

app = FastAPI(title="SharedLLM Intent Gateway", version="1.0.0", lifespan=lifespan)

@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    err_msg = f"Gateway Error: {type(exc).__name__}: {str(exc)}"
    log.error(f"{err_msg}\n{traceback.format_exc()}")
    return JSONResponse(
        status_code=500,
        content={"status": "ERROR", "message": err_msg, "detail": traceback.format_exc().splitlines()[-3:]}
    )

async def resolve_identity(req: ChatRequest) -> dict:
    """Call Identity Service to get decrypted credentials."""
    async with httpx.AsyncClient() as client:
        try:
            payload = {
                "rag_user": req.rag_user,
                "voice_id": req.voice_id,
                "device_id": req.device_id
            }
            resp = await client.post(
                f"{IDENTITY_SVC}/api/resolve",
                json=payload,
                headers={"X-Internal-Secret": INTERNAL_SECRET},
                timeout=5.0
            )
            if resp.status_code == 404:
                raise HTTPException(status_code=401, detail="User resolution failed")
            resp.raise_for_status()
            return resp.json()
        except httpx.RequestError as e:
            log.error(f"Identity Service unreachable: {e}")
            raise HTTPException(status_code=503, detail="Identity service unavailable")

async def execute_command(endpoint: str, payload: dict) -> dict:
    """Call Execution Bridge."""
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                f"{EXECUTION_SVC}{endpoint}",
                json=payload,
                headers={"X-Internal-Secret": INTERNAL_SECRET},
                timeout=10.0
            )
            if resp.status_code != 200:
                log.error(f"Execution Bridge error ({resp.status_code}): {resp.text}")
                return {"status": "FAILURE", "message": f"Execution error: {resp.text}", "service": endpoint}
            return resp.json()
        except httpx.RequestError as e:
            log.error(f"Execution Bridge unreachable: {e}")
            return {"status": "FAILURE", "message": f"Execution service error: {str(e)}"}

async def fetch_ha_entities(creds: dict) -> list:
    """Fetch real entities from Execution service."""
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(
                f"{EXECUTION_SVC}/discovery/entities",
                params={"ha_url": creds.get("ha_url"), "ha_token": creds.get("ha_token")},
                headers={"X-Internal-Secret": INTERNAL_SECRET},
                timeout=5.0
            )
            if resp.status_code == 200:
                return resp.json()
        except Exception as e:
            log.warning(f"Failed to fetch real entities: {e}")
    return []

def extract_entity_heuristic(query: str, intent: str, entities: list = None) -> str:
    """
    Enhanced heuristic entity extraction using real HA data if available.
    """
    q = query.lower()
    
    # 1. If we have real entities, try to match friendly names or IDs
    if entities:
        # Sort by length descending to match more specific names first
        for e in sorted(entities, key=lambda x: len(x.get("attributes", {}).get("friendly_name", "")), reverse=True):
            entity_id = e.get("entity_id", "")
            domain = entity_id.split(".")[0]
            
            # Check if domain matches intent type
            if intent in ("turn_on", "turn_off", "toggle") and domain not in ("light", "switch", "media_player"):
                continue
            if intent in ("play_media", "pause_media") and domain != "media_player":
                continue
                
            fname = e.get("attributes", {}).get("friendly_name", "").lower()
            q_clean = q.lower().replace("-", " ")
            f_clean = fname.replace("-", " ")
            
            if fname and (f_clean in q_clean or q_clean in f_clean):
                log.info(f"[heuristic] Matched '{q}' to '{entity_id}' via friendly_name '{fname}'")
                return entity_id
                
            if entity_id.split(".")[1].replace("_", " ") in q_clean:
                log.info(f"[heuristic] Matched '{q}' to '{entity_id}' via ID part")
                return entity_id

    # 2. No match found - Return None to trigger Slow Path/Error
    return None

def extract_brightness(q: str) -> int | None:
    """Try to extract a percentage (0-100) from the query."""
    import re
    match = re.search(r"(\d+)\s*%", q)
    if match:
        return int(match.group(1))
    
    # Check for words like "half", "full", etc.
    q_low = q.lower()
    if "half" in q_low: return 50
    if "full" in q_low or "maximum" in q_low: return 100
    if "dim" in q_low: return 10
    
    # Check for raw numbers
    match = re.search(r"to\s+(\d+)", q_low)
    if match:
        val = int(match.group(1))
        if 0 <= val <= 100: return val
        
    return None

@app.post("/api/chat")
@app.post("/v1/chat/completions")
@app.post("/chat/completions")
async def chat_handler(request: Request):
    """
    Unified chat endpoint. Handles:
    1. RAG-style ChatRequest (query: str)
    2. Ollama-style chat (messages: list)
    3. OpenAI-style chat (messages: list)
    """
    body = await request.json()
    is_native_proxy = "messages" in body
    
    # Extract query
    query = ""
    if is_native_proxy:
        messages = body.get("messages", [])
        if messages:
            query = messages[-1].get("content", "")
    else:
        query = body.get("query") or body.get("prompt") or ""

    if not query:
        # Fallback for non-chat requests misrouted here
        if is_native_proxy:
            async with httpx.AsyncClient() as client:
                resp = await client.post(f"{OLLAMA_URL}/api/chat", json=body, timeout=60.0)
                return resp.json()
        raise HTTPException(status_code=400, detail="No query found")

    # 1. Resolve Identity and Entities (Always needed for context)
    try:
        req_for_id = ChatRequest(query=query, rag_user=body.get("rag_user"), voice_id=body.get("voice_id"))
        creds = await resolve_identity(req_for_id)
        user_context = {
            "user": creds["user"],
            "ha_url": creds.get("ha_url", ""),
            "ha_token": creds.get("ha_token", "")
        }
        real_entities = await fetch_ha_entities(creds)
    except Exception as e:
        log.warning(f"Resolution error: {e}")
        user_context = {"user": "admin", "ha_url": "", "ha_token": ""}
        real_entities = []

    # 2. Intent Classification (Semantic Router)
    intent, confidence = engine.classify(query)
    log.info(f"[gateway] Classified '{query}' -> {intent} ({confidence:.2f})")

    # 3. Fast Path Evaluation
    # Skip Fast Path for queries/questions
    is_fast_path = confidence >= FAST_PATH_THRESHOLD and intent in ("turn_on", "turn_off", "toggle", "set_brightness", "play_media", "pause_media", "announce")

    if is_fast_path:
        log.info(f"[gateway] FAST PATH triggered for {intent}")
        entity_id = extract_entity_heuristic(query, intent, real_entities)
        
        if not entity_id:
            log.warning(f"[gateway] Fast path failed: No entity matched for '{query}'. Falling back to Slow Path.")
            is_fast_path = False
        else:
            # Execute
            if intent in ("turn_on", "turn_off", "toggle", "set_brightness"):
                brightness = extract_brightness(query)
                action = "turn_on" if intent == "set_brightness" else intent
                exec_payload = {
                    "user_context": user_context,
                    "entity_id": entity_id,
                    "action": action,
                    "brightness": brightness
                }
                exec_res = await execute_command("/execute/light", exec_payload)
            elif intent in ("play_media", "pause_media"):
                if intent == "play_media":
                    exec_payload = {"user_context": user_context, "entity_id": entity_id, "query": query.replace("play", "").strip()}
                    exec_res = await execute_command("/execute/media/play", exec_payload)
                else:
                    exec_payload = {"user_context": user_context, "entity_id": entity_id, "command": "pause"}
                    exec_res = await execute_command("/execute/media/transport", exec_payload)
            elif intent == "announce":
                exec_payload = {"user_context": user_context, "entity_id": entity_id, "message": query.replace("announce", "").replace("say", "").strip()}
                exec_res = await execute_command("/execute/announce", exec_payload)
            else:
                exec_res = {"status": "FAILURE", "message": "Unknown intent"}

            # Format Response based on request type
            success_msg = f"OK. I've processed the {intent} command for {entity_id}."
            if exec_res.get("status") == "SUCCESS":
                msg = success_msg
            else:
                msg = f"Attempted to {intent} {entity_id}, but: {exec_res.get('message')}"

            if is_native_proxy:
                # Return Ollama-style response
                resp_data = {
                    "model": body.get("model", "gateway-fast-path"),
                    "created_at": "2024-01-01T00:00:00Z", # Mock
                    "message": {"role": "assistant", "content": msg},
                    "status": exec_res.get("status", "SUCCESS"),
                    "done": True
                }
                if body.get("stream", False):
                    async def _gen():
                        yield json.dumps(resp_data) + "\n"
                    return StreamingResponse(_gen(), media_type="application/x-ndjson")
                return resp_data
            else:
                return ChatResponse(
                    status=exec_res.get("status", "FAILURE"),
                    message=msg,
                    intent=intent,
                    confidence=confidence,
                    execution_result=exec_res
                )

    # Build Device Context for LLM
    device_context = ""
    if real_entities:
        q_low = query.lower()
        mentioned_eids = set()
        
        # 1. Find Primary Entities (Direct mention)
        for e in real_entities:
            eid = e.get("entity_id", "").lower()
            fname = e.get("attributes", {}).get("friendly_name", "").lower()
            if (fname and fname in q_low) or (eid.split(".")[1].replace("_", " ") in q_low):
                mentioned_eids.add(eid)
        
        # 2. Broad Query Handling (If no specific mention but general question)
        # Check for broad keywords like "control", "devices", "everything", "all", "capabilities", "what can you do"
        broad_keywords = ["control", "devices", "everything", "all", "capabilities", "list", "what can you", "status"]
        is_broad = any(k in q_low for k in broad_keywords)
        
        if not mentioned_eids and is_broad:
            log.info("[gateway] Broad query detected - injecting controllable entity summary")
            # Inject a sample of each controllable domain
            domains = ["light", "switch", "media_player", "climate", "lock", "cover", "vacuum"]
            for domain in domains:
                matches = [e for e in real_entities if e.get("entity_id", "").startswith(domain + ".")]
                if matches:
                    # Take up to 10 from each domain to avoid token bloat
                    for e in matches[:10]:
                        mentioned_eids.add(e["entity_id"])

        # 3. Find Related Entities (Sensors, Info) for the mentioned ones
        prefixes = set()
        for eid in mentioned_eids:
            parts = eid.split(".")
            if len(parts) > 1:
                prefixes.add(parts[1].split("_")[0]) 

        # 4. Compile full context
        for e in real_entities:
            eid = e.get("entity_id", "")
            fname = e.get("attributes", {}).get("friendly_name", "").lower()
            is_mentioned = eid.lower() in mentioned_eids
            is_related = any(p in eid.lower() for p in prefixes) if prefixes else False
            
            if is_mentioned or is_related:
                state = e.get("state", "unknown")
                actual_name = e.get("attributes", {}).get("friendly_name") or eid
                attrs = e.get("attributes", {})
                filtered_attrs = {k: v for k, v in attrs.items() if k not in ("icon", "entity_picture", "templates")}
                device_context += f"- {actual_name} ({eid}): {state} (Attributes: {filtered_attrs})\n"
    
    if device_context:
        # Truncate if too long
        if len(device_context) > 15000:
            device_context = device_context[:15000] + "... [Truncated for brevity]"

        ctx_msg = (
            "## Home Assistant Device Context\n"
            "The following devices and related sensors were found in the user's setup. "
            "Use this information to answer accurately. \n\n"
            f"{device_context}\n"
        )
        log.info(f"[gateway] Injecting device context for {len(device_context.splitlines())} entities")
        if is_native_proxy:
            # Inject into messages for Ollama/OpenAI
            body["messages"].insert(0, {"role": "system", "content": ctx_msg})

    # 3. Slow Path / Proxy
    if is_native_proxy:
        log.info(f"[gateway] Proxying to Ollama (with context injection)")
        async def _proxy_stream():
            try:
                async with httpx.AsyncClient(timeout=None) as client:
                    async with client.stream("POST", f"{OLLAMA_URL}/api/chat", json=body) as r:
                        if r.status_code != 200:
                            log.error(f"Ollama returned {r.status_code}")
                            yield json.dumps({"error": f"Ollama error: {r.status_code}"}).encode()
                            return
                            
                        async for chunk in r.aiter_bytes():
                            if chunk:
                                yield chunk
            except Exception as e:
                log.error(f"Proxy stream error: {e}")
                yield json.dumps({"error": str(e)}).encode()

        if body.get("stream", True):
            return StreamingResponse(_proxy_stream(), media_type="application/x-ndjson")
        async with httpx.AsyncClient() as client:
            resp = await client.post(f"{OLLAMA_URL}/api/chat", json=body, timeout=60.0)
            return resp.json()

    # RAG / Slow Path (Original logic)
    log.info(f"[gateway] SLOW PATH for intent {intent} ({confidence:.2f})")
    
    # Simulate RAG call
    async with httpx.AsyncClient() as client:
        try:
            rag_payload = {"query": query, "user_id": body.get("rag_user", "admin"), "k": 3}
            rag_resp = await client.post(
                f"{RAG_SVC}/rag/search",
                json=rag_payload,
                headers={"X-Internal-Secret": INTERNAL_SECRET},
                timeout=5.0
            )
            rag_data = rag_resp.json() if rag_resp.status_code == 200 else {}
        except Exception:
            rag_data = {}

    return ChatResponse(
        status="SUCCESS",
        message=f"I am processing your request. Simulated LLM response for: {query}",
        intent=intent,
        confidence=confidence
    )

@app.get("/health")
def health():
    return {"status": "ok", "service": "gateway"}

# --- Ollama Proxy Endpoints ---

@app.post("/api/pull")
async def api_pull(req: OllamaPullRequest):
    """Proxy to Ollama with local check bypass."""
    model_name = req.model or req.name or ""
    log.info(f"[/api/pull] Request for model: '{model_name}'")

    # Check local tags first to avoid unnecessary pulls
    async with httpx.AsyncClient() as client:
        try:
            tags_resp = await client.get(f"{OLLAMA_URL}/api/tags", timeout=5.0)
            if tags_resp.status_code == 200:
                local_models = [m.get("name", "") for m in tags_resp.json().get("models", [])]
                for local in local_models:
                    if local == model_name or local.split(":")[0] == model_name.split(":")[0]:
                        log.info(f"[/api/pull] Model '{model_name}' already present. Skipping.")
                        return {"status": "success"}
        except Exception as e:
            log.warning(f"[/api/pull] Could not check local models: {e}")

    # Proxy the pull
    async def _proxy_stream():
        async with httpx.AsyncClient() as client:
            async with client.stream(
                "POST", 
                f"{OLLAMA_URL}/api/pull", 
                json=req.model_dump(), 
                timeout=None
            ) as r:
                async for chunk in r.aiter_bytes():
                    yield chunk

    if req.stream:
        return StreamingResponse(_proxy_stream(), media_type="application/x-ndjson")
    
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{OLLAMA_URL}/api/pull", json=req.model_dump(), timeout=600)
        return resp.json()

@app.post("/api/generate")
@app.post("/generate")
async def api_generate(req: OllamaGenerateRequest):
    """Proxy generate requests to Ollama."""
    if req.stream:
        async def _proxy_stream():
            async with httpx.AsyncClient() as client:
                async with client.stream("POST", f"{OLLAMA_URL}/api/generate", json=req.model_dump(), timeout=None) as r:
                    async for chunk in r.aiter_bytes():
                        yield chunk
        return StreamingResponse(_proxy_stream(), media_type="application/x-ndjson")

    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{OLLAMA_URL}/api/generate", json=req.model_dump(), timeout=60.0)
        return resp.json()

@app.get("/api/tags")
@app.get("/api/models")
async def api_tags():
    """Proxy tags requests to Ollama."""
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(f"{OLLAMA_URL}/api/tags", timeout=5.0)
            return resp.json()
        except Exception as e:
            log.error(f"Failed to proxy /api/tags: {e}")
            raise HTTPException(status_code=503, detail="Ollama unreachable")

@app.get("/api/version")
async def api_version():
    """Proxy version requests to Ollama."""
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(f"{OLLAMA_URL}/api/version", timeout=5.0)
            return resp.json()
        except Exception as e:
            # Fallback version if Ollama is down but we want to satisfy clients
            return {"version": "0.1.32"}

@app.post("/api/show")
async def api_show(request: Request):
    """Proxy show requests to Ollama."""
    body = await request.json()
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{OLLAMA_URL}/api/show", json=body, timeout=5.0)
        return resp.json()

# End of Chat Handlers

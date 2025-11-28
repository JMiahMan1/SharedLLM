# app/main.py — Interface
import time
import json
import subprocess
import requests
from typing import List, Optional, Dict, Any

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Fixed imports
from settings import (
    lifespan, get_user_creds, run_blocking, GlobalResources, log,
    DEFAULT_MODEL, OPENAI_MODEL, OLLAMA_URL, openai_client, OPENAI_API_KEY, HA_URL,
    initialize_rag_resources # IMPORTED HOT RELOAD FUNCTION
)
from logic import (
    generate_rag_stream, contextualize_query, try_handle_compound_command, 
    call_ollama_generate, call_openai_chat, 
    get_ha_context, get_rag_context, update_history
)

app = FastAPI(title="Unified RAG API", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

# Models
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
        return {
            "id": f"chat-{int(time.time())}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": body.model,
            "choices": [{"message": {"role": "assistant", "content": full_text}, "finish_reason": "stop", "index": 0}]
        }
    
    return {
        "model": body.model, 
        "created_at": time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        "message": {"role": "assistant", "content": full_text}, 
        "done": True
    }

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
    refined = await contextualize_query(query, user, body.model)
    creds = get_user_creds(user)
    cmd = await try_handle_compound_command(refined, creds, body.model)
    if cmd: return {"response": cmd}
    ha = await get_ha_context(user, query=refined)
    nc = await get_rag_context(refined)
    return {"response": f"Context:\n{ha}\n{nc}"}

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

def _run_sync(path):
    try: return subprocess.run(["python", path], capture_output=True, text=True).stdout
    except: return "Error"

# --- Ingestion Endpoints with Hot Reload ---
@app.post("/ingest/ha")
async def ing_ha(): 
    res = await run_blocking(_run_sync, "/app/ha_ingest.py")
    await initialize_rag_resources() # RELOAD DB
    return res

@app.post("/ingest/nextcloud")
async def ing_nc(): 
    res = await run_blocking(_run_sync, "/app/ingest_nextcloud.py")
    await initialize_rag_resources() # RELOAD DB
    return res

@app.post("/ingest/all")
async def ing_all(): 
    res = {"ha": await _run_script("ha_ingest.py"), "nextcloud": await _run_script("ingest_nextcloud.py")}
    await initialize_rag_resources() # RELOAD DB
    return res

async def _run_script(path): return await run_blocking(_run_sync, f"/app/{path}")

@app.post("/api/rag/upsert")
async def rag_upsert(i: UpsertRagRequest):
    if not GlobalResources.chroma_client: raise HTTPException(503)
    from langchain_core.documents import Document
    doc = Document(page_content=i.text, metadata=i.metadata or {})
    await run_blocking(lambda: (GlobalResources.chroma_client.add_documents([doc]), GlobalResources.chroma_client.persist()))
    return {"status": "ok"}
@app.post("/api/rag/delete")
async def rag_delete(id: str):
    if not GlobalResources.chroma_client: raise HTTPException(503)
    await run_blocking(lambda: GlobalResources.chroma_client.delete(ids=[id]))
    return {"status": "ok"}
@app.get("/api/rag/list")
async def rag_list(limit: int = 100):
    if not GlobalResources.chroma_client: raise HTTPException(503)
    def sync():
        c = GlobalResources.chroma_client._collection
        cnt = c.count()
        s = c.peek(min(limit, cnt)) if cnt else {}
        return {"count": cnt, "docs": [{"id": id, "preview": s["documents"][i][:200]} for i, id in enumerate(s.get("ids", []))]}
    return await run_blocking(sync)

@app.get("/api/rag/search")
async def rag_search(q: str, k: int = 4):
    results = []
    if GlobalResources.ha_collection:
        try:
            ha_docs = await run_blocking(lambda: GlobalResources.ha_collection.similarity_search(q, k=k))
            results.extend([{"text": d.page_content, "metadata": d.metadata, "source": "home_assistant"} for d in ha_docs])
        except Exception as e: log.error(f"Error searching HA collection: {e}")
    if GlobalResources.nextcloud_collection:
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

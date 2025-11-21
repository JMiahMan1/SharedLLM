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
    DEFAULT_MODEL, OPENAI_MODEL, OLLAMA_URL, openai_client, OPENAI_API_KEY
)
from logic import (
    stream_rag_result, contextualize_query, try_handle_compound_command, 
    is_system_task, call_ollama_generate, call_openai_chat, 
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
    
    format_type = "openai" if "v1" in request.url.path or "completions" in request.url.path else "chat"

    if body.stream or is_system_task(query):
        return await stream_rag_result(query, user, body.model, body.use_openai, format_type)

    # Non-Stream Fallback
    full_text = ""
    async for chunk in stream_rag_result(query, user, body.model, body.use_openai, "raw"):
        try: 
            d = json.loads(chunk)
            full_text += d.get("response", "")
        except: pass
    
    if format_type == "openai":
        return {"choices": [{"message": {"role": "assistant", "content": full_text}}]}
    return {"model": body.model, "message": {"role": "assistant", "content": full_text}, "done": True}

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

# --- Passthroughs ---
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
async def generate(req: GenerateRequest):
    r = await call_ollama_generate(req.prompt, req.model)
    return {"text": r.get("text")}

# --- Ingest ---
def _run_sync(path):
    try: return subprocess.run(["python", path], capture_output=True, text=True).stdout
    except: return "Error"
@app.post("/ingest/ha")
async def ing_ha(): return await run_blocking(_run_sync, "/app/ha_ingest.py")
@app.post("/ingest/nextcloud")
async def ing_nc(): return await run_blocking(_run_sync, "/app/ingest_nextcloud.py")
@app.post("/ingest/all")
async def ing_all(): return {"ha": await _run_script("ha_ingest.py"), "nextcloud": await _run_script("ingest_nextcloud.py")}
async def _run_script(path): return await run_blocking(_run_sync, f"/app/{path}")

# --- System ---
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
    if not GlobalResources.chroma_client: raise HTTPException(503)
    docs = await run_blocking(lambda: GlobalResources.chroma_client.similarity_search(q, k=k))
    return {"results": [{"text": d.page_content, "metadata": d.metadata} for d in docs]}
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

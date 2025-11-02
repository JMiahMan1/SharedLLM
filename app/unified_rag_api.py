# unified_rag_api.py (Final Corrected Version)
import os
import time
import json
import subprocess
import logging
from typing import List, Optional
import sys

import requests
from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel

# Load .env when running locally (Docker Compose will supply env)
if os.getenv("DOCKER_ENV") != "1" and os.path.exists(".env"):
    from dotenv import load_dotenv
    load_dotenv(".env")

# ------------------
# Logging
# ------------------
DEBUG = os.getenv("DEBUG", "0") in ("1", "true", "True")
# CRITICAL ADDITION: Define the RAG debug flag
DEBUG_RAG_CONTEXT = os.getenv("DEBUG_RAG_CONTEXT", "0") in ("1", "true", "True") or DEBUG
logging.basicConfig(level=logging.DEBUG if DEBUG else logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("unified-rag")

# ------------------
# Environment/Configuration
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


# ------------------
# User creds helper
# ------------------
def get_user_creds(user: Optional[str] = None):
    user = user or HA_DEFAULT_USER
    ha_token = os.getenv(f"HA_{user}_TOKEN") or os.getenv("HA_TOKEN")
    nc_pass = os.getenv(f"NEXTCLOUD_{user}_PASS") or os.getenv("NEXTCLOUD_PASS")
    return {"user": user, "ha_token": ha_token, "nc_pass": nc_pass}

# ------------------
# Vector DB + Embeddings initialization
# ------------------
db = None
try:
    from langchain_huggingface import HuggingFaceEmbeddings
    from langchain_chroma import Chroma
    log.info("Initializing embeddings model: %s", EMB_MODEL)
    emb = HuggingFaceEmbeddings(model_name=EMB_MODEL)
    # Instantiate the base Chroma store (will use a default collection)
    db = Chroma(persist_directory=CHROMA_DIR, embedding_function=emb) 
    log.info("Chroma initialized at %s", CHROMA_DIR)
except Exception as e:
    log.warning("Chroma/embeddings init failed or not available: %s", e)
    db = None

# Documents type helper
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
app = FastAPI(title="Unified RAG API")

# ------------------
# Ollama helper: robustly handle NDJSON streaming and non-stream cases
# ------------------
def call_ollama_generate(prompt: str, model: str = DEFAULT_MODEL, stream: bool = False, timeout: int = 120):
    """
    Call Ollama /api/generate.
    """
    url = f"{OLLAMA_URL.rstrip('/')}/api/generate"
    payload = {"model": model, "prompt": prompt, "stream": stream}
    headers = {"Content-Type": "application/json"}

    try:
        r = requests.post(url, json=payload, headers=headers, timeout=timeout, stream=True)
        r.raise_for_status()
    except Exception as e:
        log.exception("Ollama request failed")
        raise HTTPException(status_code=502, detail=f"Ollama request failed: {e}")

    content_type = r.headers.get("Content-Type", "")
    if "ndjson" in content_type or "application/x-ndjson" in content_type or stream:
        text_accum = []
        final_text = ""
        try:
            for raw_line in r.iter_lines(decode_unicode=True):
                if not raw_line:
                    continue
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                
                if "response" in obj and obj.get("response"): 
                    text_accum.append(obj.get("response") or "") 
                if "message" in obj and isinstance(obj["message"], dict): 
                    msg = obj["message"].get("content") or obj["message"].get("response") 
                    if msg:
                        text_accum.append(msg) 
                if obj.get("done") is True or obj.get("finished") is True:
                    final_text = "".join(text_accum).strip()
                    break
            if not final_text: 
                final_text = "".join(text_accum).strip()
            if final_text:
                return {"text": final_text}
        except Exception as e:
            log.warning("Error while streaming/processing ndjson: %s", e) 
    
    try:
        data = r.json()
        if isinstance(data, dict):
            text = data.get("text") or data.get("response") or data.get("output") 
            if not text and "message" in data and isinstance(data["message"], dict): 
                text = data["message"].get("content") or data["message"].get("response") 
            return {"text": text or json.dumps(data)} 
        return {"text": str(data)}
    except Exception as e:
        try:
            raw = r.content.decode("utf-8", errors="ignore") 
            return {"text": raw} 
        except Exception:
            raise HTTPException(status_code=502, detail="Unable to parse Ollama response")

# ------------------
# Helpers for HA and Nextcloud contexts
# ------------------
def get_ha_context(user: Optional[str] = None, limit: int = 50) -> str:
    creds = get_user_creds(user)
    ha_token = creds["ha_token"]
    if not HA_URL or not ha_token:
        log.debug("HA_URL or token missing, skipping HA context")
        return ""
    try:
        headers = {"Authorization": f"Bearer {ha_token}"}
        r = requests.get(f"{HA_URL.rstrip('/')}/api/states", headers=headers, timeout=10)
        r.raise_for_status()
        states = r.json()
        lines = []
        for s in states[:limit]:
            eid = s.get("entity_id")
            st = s.get("state")
            if isinstance(st, dict):
                st = json.dumps(st)
            lines.append(f"{eid}: {st}")
        log.debug("HA context fetched: %d entities", len(lines))
        return f"Home Assistant snapshot for user {creds['user']}:\n" + "\n".join(lines)
    except Exception as e:
        log.exception("Failed to fetch HA context")
        return f"[HA context unavailable for {creds['user']}: {e}]"

# CRITICAL FIX applied here
def get_nextcloud_context(query: str, user: Optional[str] = None, k: int = 4) -> str:
    # We don't use the global 'db' here, but instead initialize a connection to the specific collection
    if not os.path.exists(CHROMA_DIR):
        log.debug("Chroma persistence directory missing, skipping Nextcloud context")
        return ""
    
    try:
        from langchain_huggingface import HuggingFaceEmbeddings
        from langchain_chroma import Chroma
        
        # Re-initialize embeddings (must be consistent with the global one)
        embeddings = HuggingFaceEmbeddings(model_name=EMB_MODEL)
        
        # CRITICAL: Instantiate a Chroma client for the specific collection
        nc_db = Chroma(
            collection_name="nextcloud_docs",
            embedding_function=embeddings,
            persist_directory=CHROMA_DIR
        )
        
        # Use similarity_search_with_score for better debugging (score + path)
        docs_with_scores = nc_db.similarity_search_with_score(query, k=k)
        log.debug("Chroma search results count from nextcloud_docs: %d", len(docs_with_scores))
    except Exception as e:
        log.exception("Nextcloud vector search failed.")
        return ""
            
    if not docs_with_scores:
        return ""
        
    texts = []
    # FIX: Simplify content extraction to directly access attributes on the Document object.
    for d, score in docs_with_scores:
        try:
            # Direct attribute access for content and metadata
            content = d.page_content
            meta = d.metadata
            path = meta.get("path", "N/A")
            
            if content and content.strip():
                # Format the output to include path and score for debugging/LLM grounding
                texts.append(f"[Source: Nextcloud, Path: {path}, Score: {score:.4f}]\n{content}")
        except Exception as e:
            log.warning(f"Error extracting content from document: {e}")
            continue # Skip this document if extraction fails
            
    if not texts:
        return ""
        
    return f"Nextcloud context (user {user or 'default'}):\n" + "\n\n".join(texts)

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

# ------------------
# Endpoints
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

    # Get contexts
    ha_ctx = get_ha_context(user=user)
    nc_ctx = get_nextcloud_context(query, user=user)
    combined_context = "\n\n".join([c for c in (ha_ctx, nc_ctx) if c])

    # DEBUG MODE CHECK - Critical for troubleshooting
    if DEBUG_RAG_CONTEXT:
        log.warning(f"DEBUG_RAG_CONTEXT is ON. Skipping LLM call and returning context.")
        log.info(f"Retrieved Context:\n{combined_context}")
        return {
            "debug_status": "RAG Context Returned Instead of LLM Response (DEBUG_RAG_CONTEXT=true)",
            "query": query,
            "user": user,
            "retrieved_context": combined_context
        }

    prompt = f"""You are a local AI assistant with access to Home Assistant data and private Nextcloud documents for user {user}.
Use only the provided context to answer concisely and accurately.
If the answer is not in the context say you don't know.
Context:
{combined_context}

User question:
{query}

Answer:"""

    log.debug("Sending prompt to Ollama (model=%s). Prompt length=%d", body.model or DEFAULT_MODEL, len(prompt))
    resp = call_ollama_generate(prompt=prompt, model=body.model or DEFAULT_MODEL, stream=False)
    text = resp.get("text") if isinstance(resp, dict) else str(resp)
    return {"id": f"rag-{int(time.time())}", "user": user, "response": text}

@app.post("/api/chat/completions")
async def chat_completions(body: CompletionRequest, request: Request):
    # backwards-compatible wrapper to rag_query
    return await rag_query(body, request)

# Manual vector ingestion endpoint (add arbitrary text into Chroma)
@app.post("/context/update")
async def update_context(payload: Request):
    global db
    if db is None:
        try:
            from langchain_huggingface import HuggingFaceEmbeddings
            from langchain_chroma import Chroma
            # Use EMB_MODEL for consistency
            emb = HuggingFaceEmbeddings(model_name=EMB_MODEL)
            db = Chroma(persist_directory=CHROMA_DIR, embedding_function=emb)
        except Exception as e:
            log.exception("Vector DB not available on update_context")
            raise HTTPException(status_code=500, detail=f"Vector DB not available: {e}")

    data = await payload.json()
    txt = data.get("text")
    if not txt:
        raise HTTPException(status_code=400, detail="text required")

    meta = {"source": data.get("source", "shared_context"), "user": data.get("user")}
    if Document:
        doc = Document(page_content=txt, metadata=meta)
    else:
        doc = {"page_content": txt, "metadata": meta}
    try:
        db.add_documents([doc])
        db.persist()
    except Exception as e:
        log.exception("Failed to add documents to DB")
        raise HTTPException(status_code=500, detail=f"Failed to add documents: {e}")
    return {"status": "ok"}

# ------------------
# Ingestion endpoints that call your ingest scripts (ha_ingest.py and ingest_nextcloud.py)
# ------------------
def _run_script(path):
    """
    Run script at /app/<path> and capture output, STREAMING logs immediately.
    """
    script_path = f"/app/{path}"
    log.info("Starting ingestion script: %s", path)

    stdout_accum = []
    stderr_accum = []
    returncode = -1

    try:
        proc = subprocess.Popen(
            ["python", script_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1
        )

        for line in proc.stdout:
            line = line.strip()
            if line:
                log.info("[%s] %s", path, line)
                stdout_accum.append(line)

        proc.wait()
        returncode = proc.returncode

        if proc.stderr:
            for line in proc.stderr:
                line = line.strip()
                if line:
                    log.error("[%s] %s", path, line)
                    stderr_accum.append(line)

        if returncode != 0:
            log.error("Script %s failed with return code %d", path, returncode)
            raise subprocess.CalledProcessError(returncode, ["python", script_path], "\n".join(stdout_accum), "\n".join(stderr_accum))

        return {"status": "ok", "stdout": "\n".join(stdout_accum)}

    except subprocess.CalledProcessError as e:
        log.exception("Script %s failed", path)
        return {"status": "error", "stdout": e.stdout, "stderr": e.stderr, "returncode": e.returncode}
    except Exception as e:
        log.exception("Unexpected error running script %s", path)
        return {"status": "error", "stdout": "\n".join(stdout_accum), "stderr": f"Unexpected error: {e}", "returncode": -1}


@app.post("/ingest/ha")
async def ingest_ha():
    log.info("Triggering HA ingest via ha_ingest.py")
    return _run_script("ha_ingest.py")

@app.post("/ingest/nextcloud")
async def ingest_nextcloud():
    log.info("Triggering Nextcloud ingest via ingest_nextcloud.py")
    return _run_script("ingest_nextcloud.py")

@app.post("/ingest/all")
async def ingest_all():
    results = {}
    log.info("Triggering HA ingest via ha_ingest.py")
    results["ha"] = _run_script("ha_ingest.py")
    log.info("Triggering Nextcloud ingest via ingest_nextcloud.py")
    results["nextcloud"] = _run_script("ingest_nextcloud.py")
    return results

# ------------------
# Health and root endpoints
# ------------------
@app.get("/health")
async def health():
    return {
        "ok": True,
        "ollama_url": OLLAMA_URL,
        "ha_url": bool(HA_URL),
        "nextcloud_configured": bool(NEXTCLOUD_URL and NEXTCLOUD_USER and NEXTCLOUD_PASS),
        "vector_db": bool(db),
        "debug_rag_context_mode": DEBUG_RAG_CONTEXT
    }

@app.get("/")
async def root():
    return {"service": "unified-rag", "ollama": OLLAMA_URL}

# services/storage/main.py
import logging
import os
import httpx
import re
from fastapi import FastAPI, HTTPException, BackgroundTasks, Body, Query
from pydantic import BaseModel
from typing import Optional

try:
    from .indexer import (
        build_content_index, summarize_index, extract_and_chunk_contents, 
        set_indexer_pause, CheckpointManager
    )
    from .providers import build_provider, ProviderConfig
    from .models import ProviderWriteRequest, ProviderMirrorRequest
except (ImportError, ValueError):
    from indexer import (
        build_content_index, summarize_index, extract_and_chunk_contents, 
        set_indexer_pause, CheckpointManager
    )
    from providers import build_provider, ProviderConfig
    from models import ProviderWriteRequest, ProviderMirrorRequest

log = logging.getLogger("storage")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s")

app = FastAPI(title="Librarian Storage Service")

RAG_SVC = os.getenv("RAG_SVC_URL", "http://127.0.0.1:8004")
INTERNAL_SECRET = os.getenv("INTERNAL_SECRET", "change-me-in-production")

class IndexScanRequest(BaseModel):
    provider: ProviderConfig
    path: str = "/"
    recursive: bool = True

@app.get("/health")
def health():
    return {"status": "ok", "service": "storage"}

@app.post("/index/full")
async def full_content_index(req: IndexScanRequest, background_tasks: BackgroundTasks):
    """Scan structure, extract content, chunk, and sync to RAG in the background."""
    background_tasks.add_task(_run_full_index_task, req)
    return {
        "status": "SUCCESS",
        "message": "Indexing started in background."
    }

async def _run_full_index_task(req: IndexScanRequest):
    """Internal task for background indexing."""
    try:
        log.info(f"Background indexing started for user: {req.provider.settings.get('username')} at {req.provider.settings.get('url')}")
        try:
            provider = build_provider(req.provider)
        except (KeyError, ValueError) as exc:
            log.error(f"Failed to build provider: {exc}")
            return

        # 1. Scan structure
        log.info(f"Starting background scan for path: {req.path}")
        from starlette.concurrency import run_in_threadpool
        entries = await run_in_threadpool(provider.list_entries, path=req.path, recursive=req.recursive)
        log.info(f"Scan complete. Found {len(entries)} raw entries.")
        items = build_content_index(entries)
        
        # 2. Extract and chunk with checkpointing
        checkpoint = CheckpointManager()
        chunks = await extract_and_chunk_contents(provider, items, checkpoint=checkpoint)
        
        # 3. Sync to RAG
        user_id = req.provider.settings.get("username", "admin")
        import time
        session_id = str(int(time.time()))
        
        for c in chunks:
            c["metadata"]["session_id"] = session_id

        sync_payload = {
            "chunks": chunks,
            "user_id": user_id,
            "collection_name": f"{req.provider.kind}_files"
        }
        
        async with httpx.AsyncClient(timeout=60.0) as client:
            try:
                resp = await client.post(
                    f"{RAG_SVC}/rag/sync/files",
                    json=sync_payload,
                    headers={"X-Internal-Secret": INTERNAL_SECRET}
                )
                resp.raise_for_status()
                
                # 4. Cleanup old entries
                await client.post(
                    f"{RAG_SVC}/rag/purge",
                    json={
                        "collection_name": f"{req.provider.kind}_files",
                        "user_id": user_id,
                        "filter": {"session_id": {"$ne": session_id}}
                    },
                    headers={"X-Internal-Secret": INTERNAL_SECRET}
                )
                log.info(f"Background index complete for {user_id}. Extracted {len(chunks)} chunks.")
            except Exception as e:
                log.error(f"Failed to sync background index to RAG: {e}")
    except Exception as e:
        log.error(f"Background index task failed: {e}")
        import traceback
        log.error(traceback.format_exc())

@app.post("/index/pause")
def pause_indexing():
    set_indexer_pause(True)
    return {"status": "PAUSED"}

@app.post("/index/resume")
def resume_indexing():
    set_indexer_pause(False)
    return {"status": "RESUMED"}

@app.post("/providers/list")
async def list_provider_entries(req: IndexScanRequest):
    try:
        provider = build_provider(req.provider)
        entries = provider.list_entries(path=req.path, recursive=req.recursive)
        return {"status": "SUCCESS", "count": len(entries), "entries": [e.dict() for e in entries]}
    except Exception as e:
        log.error(f"List failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/providers/search")
async def search_provider(query: str = Query(...), req: IndexScanRequest = Body(...)):
    try:
        from starlette.concurrency import run_in_threadpool
        provider = build_provider(req.provider)
        # Scan root for shallow search (could be optimized)
        entries = await run_in_threadpool(provider.list_entries, path=req.path, recursive=req.recursive)
        
        q_lower = query.lower()
        q_words = set(re.findall(r'\b\w+\b', q_lower))
        matches = []
        
        for e in entries:
            name_lower = e.name.lower()
            name_words = set(re.findall(r'\b\w+\b', name_lower))
            
            if q_lower in name_lower or (q_words & name_words):
                matches.append(e)
                if len(matches) >= 20: break # Limit
                
        return {"status": "SUCCESS", "matches": [e.dict() for e in matches]}
    except Exception as e:
        log.error(f"Provider search failed: {e}")
        return {"status": "ERROR", "matches": []}


@app.post("/providers/write")
async def write_provider_content(req: ProviderWriteRequest):
    try:
        import base64
        provider = build_provider(req.provider)
        
        content = req.content
        is_binary = False
        if req.content_b64:
            content = base64.b64decode(req.content_b64)
            is_binary = True
            
        if content is None:
            raise HTTPException(status_code=400, detail="Either content or content_b64 must be provided")

        result = provider.write_content(
            req.path,
            content,
            create_parents=req.create_parents,
            verify=req.verify,
            is_binary=is_binary
        )
        return {"status": "SUCCESS", "result": result}
    except Exception as e:
        log.error(f"Provider write failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/providers/mirror")
async def mirror_provider_directory(req: ProviderMirrorRequest):
    try:
        provider = build_provider(req.provider)
        if not hasattr(provider, "upload_directory"):
             raise HTTPException(status_code=400, detail="Provider does not support directory mirroring")
             
        result = provider.upload_directory(req.remote_path, req.local_path)
        return {"status": "SUCCESS", "result": result}
    except Exception as e:
        log.error(f"Provider mirror failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

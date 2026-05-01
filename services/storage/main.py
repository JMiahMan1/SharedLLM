# services/storage/main.py
import logging
import os
import httpx
import re
from fastapi import FastAPI, HTTPException, BackgroundTasks, Body
from pydantic import BaseModel
from typing import Optional

try:
    from .indexer import (
        build_content_index, summarize_index, extract_and_chunk_contents, 
        set_indexer_pause, CheckpointManager
    )
    from .providers import build_provider, ProviderConfig
except (ImportError, ValueError):
    from indexer import (
        build_content_index, summarize_index, extract_and_chunk_contents, 
        set_indexer_pause, CheckpointManager
    )
    from providers import build_provider, ProviderConfig

log = logging.getLogger("storage")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s")

app = FastAPI(title="Librarian Storage Service")

RAG_SVC = os.getenv("RAG_SVC_URL", "http://rag:8004")
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

@app.post("/nextcloud/search")
async def search_nextcloud(query: str, req: dict = Body(...)):
    # Backward compatibility endpoint
    nc_url = req.get("nc_url")
    nc_user = req.get("nc_user")
    nc_pass = req.get("nc_pass")
    
    try:
        from starlette.concurrency import run_in_threadpool
        from nextcloud_client import NextCloudClient
        client = NextCloudClient(nc_url, nc_user, nc_pass)
        # Deep search not implemented in client yet, just list root for now or similar
        entries = await run_in_threadpool(client.list_entries, path="/", recursive=False)
        q_lower = query.lower()
        matches = []
        for e in entries:
            name_lower = e.name.lower()
            if name_lower in q_lower or q_lower in name_lower:
                matches.append(e)
            else:
                # Check for word overlaps
                q_words = set(re.findall(r'\w+', q_lower))
                name_words = set(re.findall(r'\w+', name_lower))
                if q_words & name_words:
                    matches.append(e)
        return {"matches": [e.dict() for e in matches]}
    except Exception as e:
        log.error(f"Search failed: {e}")
        return {"matches": []}

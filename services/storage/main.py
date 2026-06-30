# services/storage/main.py
import logging
import httpx
import re

from services.config import RAG_SVC_URL, INTERNAL_SECRET
from fastapi import FastAPI, HTTPException, BackgroundTasks, Body, Query
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from services.storage.indexer import (
    build_content_index, extract_and_chunk_contents,
    set_indexer_pause, is_indexer_paused, CheckpointManager
)
from services.storage.providers import build_provider, ProviderConfig
from services.storage.models import ProviderWriteRequest, ProviderMirrorRequest

from services.shared.info_endpoint import info_router

log = logging.getLogger("storage")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s")

app = FastAPI(title="Librarian Storage Service")

app.include_router(info_router)

RAG_SVC = RAG_SVC_URL

class IndexScanRequest(BaseModel):
    provider: ProviderConfig
    path: str = "/"
    recursive: bool = True

import time
import os
START_TIME = time.time()

@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "storage",
        "git_sha": os.getenv("GIT_SHA", "unknown"),
        "start_time": START_TIME
    }

@app.get("/status")
async def get_storage_status():
    """Retrieves the current indexing status and file counts."""
    indexer_state = "PAUSED" if is_indexer_paused() else "IDLE"

    rag_stats = {}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{RAG_SVC}/rag/stats",
                headers={"X-Internal-Secret": INTERNAL_SECRET},
            )
            if resp.status_code == 200:
                rag_data = resp.json()
                rag_stats = {
                    "total_chunks": rag_data.get("total_chunks", 0),
                    "total_documents": rag_data.get("total_documents", 0),
                    "last_indexed": rag_data.get("last_indexed"),
                    "breakdown": rag_data.get("breakdown", {}),
                }
    except Exception as e:
        log.warning(f"Failed to fetch RAG stats for storage status: {e}")

    checkpoint_count = 0
    try:
        cp = CheckpointManager()
        checkpoint_count = len(cp.data)
    except Exception:
        pass

    return {
        "status": "SUCCESS",
        "indexer": indexer_state,
        "checkpointed_files": checkpoint_count,
        "rag_index": rag_stats,
        "message": "Storage system healthy. Ready for discovery." if indexer_state == "IDLE" else "Indexing paused."
    }

@app.post("/index/full", status_code=202)
async def sync_folder_to_chroma(req: IndexScanRequest, background_tasks: BackgroundTasks):
    """Scan structure, extract content, chunk, and sync to RAG in the background."""
    background_tasks.add_task(_run_full_index_task, req)
    return {
        "status": "ACCEPTED",
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
        entries = await provider.list_entries(path=req.path, recursive=req.recursive)
        log.info(f"Scan complete. Found {len(entries)} raw entries.")
        items = build_content_index(entries)
        
        # 2. Extract and chunk with checkpointing
        checkpoint = CheckpointManager()
        chunks = await extract_and_chunk_contents(provider, items, checkpoint=checkpoint)
        
        # 3. Sync to RAG
        user_id = req.provider.settings.get("username", "admin").lower()
        import time
        session_id = str(int(time.time()))
        indexed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        
        for c in chunks:
            c["metadata"]["session_id"] = session_id
            c["metadata"]["indexed_at"] = indexed_at

        sync_payload = {
            "chunks": chunks,
            "user_id": user_id,
            "collection_name": f"{req.provider.kind}_files"
        }
        
        async with httpx.AsyncClient(timeout=httpx.Timeout(45.0, connect=5.0)) as client:
            try:
                resp = await client.post(
                    f"{RAG_SVC}/rag/sync/files",
                    json=sync_payload,
                    headers={"X-Internal-Secret": INTERNAL_SECRET}
                )
                if resp.status_code != 200:
                    raise httpx.HTTPStatusError(
                        f"RAG sync failed: {resp.status_code} {resp.text}",
                        request=resp.request,
                        response=resp
                    )
                log.info(f"RAG sync successful: {resp.json()}")
                
                # 4. Cleanup old entries
                purge_resp = await client.post(
                    f"{RAG_SVC}/rag/purge/{req.provider.kind}_files",
                    json={
                        "user_id": user_id,
                        "filter": {"session_id": {"$ne": session_id}}
                    },
                    headers={"X-Internal-Secret": INTERNAL_SECRET}
                )
                if purge_resp.status_code != 200:
                    log.warning(f"Purge failed (non-fatal): {purge_resp.status_code} {purge_resp.text}")
                
                log.info(f"Background index complete for {user_id}. Extracted {len(chunks)} chunks.")
            except httpx.HTTPStatusError as e:
                log.error(f"Failed to sync background index to RAG: HTTP {e.response.status_code} - {e.response.text}")
            except Exception as e:
                log.error(f"Failed to sync background index to RAG: {type(e).__name__}: {e}")
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
        entries = await provider.list_entries(path=req.path, recursive=req.recursive)
        
        # Cross-reference with RAG to set indexed status
        user_id = req.provider.settings.get("username", "admin")
        indexed_paths = set()
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                # Query RAG for all indexed paths for this user
                rag_resp = await client.get(
                    f"{RAG_SVC}/rag/indexed-paths?user_id={user_id}",
                    headers={"X-Internal-Secret": INTERNAL_SECRET}
                )
                if rag_resp.status_code == 200:
                    indexed_paths = set(rag_resp.json().get("paths", []))
            except Exception as e:
                log.warning(f"Failed to fetch indexed paths from RAG: {e}")

        # Map indexed status to entries
        result_entries = []
        for e in entries:
            e_dict = e.model_dump()
            e_dict["indexed"] = e_dict["path"] in indexed_paths
            result_entries.append(e_dict)

        return {"status": "SUCCESS", "count": len(result_entries), "entries": result_entries}
    except Exception as e:
        log.error(f"List failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/providers/search")
async def search_provider(query: str = Query(...), req: IndexScanRequest = Body(...)):
    try:
        provider = build_provider(req.provider)
        # Scan root for shallow search (could be optimized)
        entries = await provider.list_entries(path=req.path, recursive=req.recursive)
        
        q_lower = query.lower()
        q_words = set(re.findall(r'\b\w+\b', q_lower))
        matches = []
        
        for e in entries:
            name_lower = e.name.lower()
            name_words = set(re.findall(r'\b\w+\b', name_lower))
            
            if q_lower in name_lower or (q_words & name_words):
                matches.append(e)
                if len(matches) >= 20: break # Limit
                
        return {"status": "SUCCESS", "matches": [e.model_dump() for e in matches]}
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
        upload_fn = getattr(provider, "upload_directory", None)
        if upload_fn is None:
            raise HTTPException(status_code=400, detail="Provider does not support directory mirroring")

        result = await run_in_threadpool(upload_fn, req.remote_path, req.local_path, excludes=req.excludes)
        return {"status": "SUCCESS", "result": result}
    except Exception as e:
        log.error(f"Provider mirror failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

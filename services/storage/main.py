# services/storage/main.py
import logging
import os
import httpx

from fastapi import FastAPI, HTTPException, BackgroundTasks

try:
    from .indexer import (
        build_content_index, summarize_index, extract_and_chunk_contents, 
        set_indexer_pause, CheckpointManager
    )
    from .models import IndexScanRequest, ProviderListRequest
    from .providers import build_provider
except ImportError:
    from indexer import (
        build_content_index, summarize_index, extract_and_chunk_contents, 
        set_indexer_pause, CheckpointManager
    )
    from models import IndexScanRequest, ProviderListRequest
    from providers import build_provider

app = FastAPI(title="SharedLLM Storage Bridge")
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("storage")

INTERNAL_SECRET = os.getenv("INTERNAL_SECRET", "change-me-in-production")
RAG_SVC = os.getenv("RAG_SVC", "http://127.0.0.1:8004")


@app.get("/health")
def health():
    return {"status": "ok", "service": "storage"}


@app.post("/providers/list")
async def list_provider_entries(req: ProviderListRequest):
    """List entries from a configured storage provider."""
    try:
        provider = build_provider(req.provider)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    entries = provider.list_entries(path=req.path, recursive=req.recursive)
    return {"status": "SUCCESS", "provider": req.provider.kind, "entries": [entry.model_dump() for entry in entries]}


@app.post("/index/scan")
async def scan_content_index(req: IndexScanRequest):
    """Build a generic content capability index for a provider path."""
    try:
        provider = build_provider(req.provider)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    entries = provider.list_entries(path=req.path, recursive=req.recursive)
    items = build_content_index(entries)
    summary = summarize_index(items)
    return {
        "status": "SUCCESS",
        "provider": req.provider.kind,
        "root_path": req.path,
        "summary": summary,
        "items": [item.model_dump() for item in items],
    }


@app.post("/index/pause")
async def pause_indexer():
    set_indexer_pause(True)
    return {"status": "SUCCESS", "message": "Indexer paused"}


@app.post("/index/resume")
async def resume_indexer():
    set_indexer_pause(False)
    return {"status": "SUCCESS", "message": "Indexer resumed"}


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
        provider = build_provider(req.provider)
    except (KeyError, ValueError) as exc:
        log.error(f"Failed to build provider: {exc}")
        return

    # 1. Scan structure
    entries = provider.list_entries(path=req.path, recursive=req.recursive)
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


@app.post("/nextcloud/list")
async def list_nextcloud_compat(req: dict):
    """Compatibility shim for existing NextCloud list callers."""
    provider_req = ProviderListRequest(
        provider={
            "kind": "nextcloud",
            "settings": {
                "url": req["nc_url"],
                "username": req["nc_user"],
                "password": req["nc_pass"],
            },
        },
        path=req.get("path", "/"),
        recursive=req.get("recursive", False),
    )
    response = await list_provider_entries(provider_req)
    return {"status": response["status"], "files": response["entries"]}


@app.post("/nextcloud/search")
async def search_nextcloud_compat(req: dict, query: str):
    """Compatibility shim for existing NextCloud search callers."""
    provider_req = ProviderListRequest(
        provider={
            "kind": "nextcloud",
            "settings": {
                "url": req["nc_url"],
                "username": req["nc_user"],
                "password": req["nc_pass"],
            },
        },
        path=req.get("path", "/"),
        recursive=True,
    )
    response = await list_provider_entries(provider_req)
    
    # Try exact name match
    matches = [entry for entry in response["entries"] if query.lower() in entry["name"].lower()]
    
    # Fallback for broad listing queries
    if not matches and any(k in query.lower() for k in ["list", "files", "folders", "what", "show", "get"]):
        matches = response["entries"][:15]
        
    return {"status": "SUCCESS", "matches": matches[:15]}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8005)

# services/storage/main.py
import base64
import hmac
import logging
import re

import aiohttp
from fastapi import BackgroundTasks, Body, Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel

from services.common.http import get_client
from services.config import INTERNAL_SECRET, RAG_SVC_URL
from services.shared.info_endpoint import info_router
from services.storage.indexer import CheckpointManager, build_content_index, extract_and_chunk_contents, is_indexer_paused, set_indexer_pause
from services.storage.models import ProviderFetchRequest, ProviderMirrorRequest, ProviderWriteRequest
from services.storage.models import IndexScanRequest
from services.storage.providers import ProviderConfig, build_provider

log = logging.getLogger("storage")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s")

app = FastAPI(title="Librarian Storage Service")

app.include_router(info_router)

RAG_SVC = RAG_SVC_URL

import os
import time

START_TIME = time.time()

@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "storage",
        "git_sha": os.getenv("GIT_SHA", "unknown"),
        "start_time": START_TIME
    }


def _require_internal_secret(
    x_internal_secret: str | None = Header(None, alias="X-Internal-Secret"),
) -> None:
    """Every data-bearing endpoint requires the shared internal secret.

    This service can list/write/mirror arbitrary provider content and is
    exposed on a host port; without this gate any LAN caller could read
    files or exfiltrate directories to their own WebDAV server.
    """
    expected = os.getenv("INTERNAL_SECRET", INTERNAL_SECRET)
    if not x_internal_secret or not (
        (expected and hmac.compare_digest(x_internal_secret, expected))
        or (INTERNAL_SECRET and hmac.compare_digest(x_internal_secret, INTERNAL_SECRET))
    ):
        raise HTTPException(status_code=403, detail="Forbidden")


@app.get("/status", dependencies=[Depends(_require_internal_secret)])
async def get_storage_status():
    """Retrieves the current indexing status and file counts."""
    indexer_state = "PAUSED" if is_indexer_paused() else "IDLE"

    rag_stats = {}
    try:
        async with get_client() as client:
            resp = await client.get(
                f"{RAG_SVC}/rag/stats",
                headers={"X-Internal-Secret": INTERNAL_SECRET},
                timeout=aiohttp.ClientTimeout(total=10),
            )
            if resp.status == 200:
                rag_data = await resp.json()
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

@app.post("/index/full", status_code=202, dependencies=[Depends(_require_internal_secret)])
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
        checkpoint = None if req.force else CheckpointManager()
        chunks = await extract_and_chunk_contents(provider, items, checkpoint=checkpoint)
        log.info(f"Extracted {len(chunks)} total chunks from {len(items)} files.")

        # 3. Sync to RAG in batches to avoid timeout on large payloads
        user_id = (req.user_id or req.provider.settings.get("username") or "admin").lower()
        import time
        session_id = str(int(time.time()))
        indexed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        for c in chunks:
            c["metadata"]["session_id"] = session_id
            c["metadata"]["indexed_at"] = indexed_at

        collection_name = f"{req.provider.kind}_files"
        BATCH_SIZE = 25
        total_synced = 0
        scanned_paths = {c["metadata"]["path"] for c in chunks if c["metadata"].get("path")}

        async with get_client() as client:
            try:
                await _purge_stale_paths(client, collection_name, user_id, scanned_paths)

                # 3.6. Sync to RAG in batches to avoid timeout on large payloads
                for i in range(0, len(chunks), BATCH_SIZE):
                    batch = chunks[i:i+BATCH_SIZE]
                    batch_num = (i // BATCH_SIZE) + 1
                    total_batches = (len(chunks) + BATCH_SIZE - 1) // BATCH_SIZE

                    sync_payload = {
                        "chunks": batch,
                        "user_id": user_id,
                        "collection_name": collection_name
                    }

                    resp = await client.post(
                        f"{RAG_SVC}/rag/sync/files",
                        json=sync_payload,
                        headers={"X-Internal-Secret": INTERNAL_SECRET},
                        timeout=aiohttp.ClientTimeout(total=60.0, connect=5.0),
                    )
                    if resp.status != 200:
                        raise RuntimeError(
                            f"RAG sync failed (batch {batch_num}/{total_batches}): {resp.status}"
                        )
                    total_synced += len(batch)
                    log.info(f"RAG batch {batch_num}/{total_batches} synced: {len(batch)} chunks")

                log.info(f"Background index complete for {user_id}. Synced {total_synced}/{len(chunks)} chunks.")
            except aiohttp.ClientResponseError as e:
                log.error(f"Failed to sync background index to RAG: HTTP {e.status} - {e.message}")
            except Exception as e:
                log.error(f"Failed to sync background index to RAG: {type(e).__name__}: {e}")
    except Exception as e:
        log.error(f"Background index task failed: {e}")
        import traceback
        log.error(traceback.format_exc())

async def _purge_stale_paths(
    client,
    collection_name: str,
    user_id: str,
    scanned_paths: set[str],
) -> None:
    """Drop only chunks whose source path disappeared upstream.

    The previous implementation purged the whole collection before repopulating
    it, which meant any failure partway through a sync silently shrank the index
    and there was nothing to restore from. Syncing is already idempotent (the
    per-chunk id is derived from user+path+index), so the only thing a purge is
    actually needed for is removing chunks whose file is gone. A lookup failure
    here is logged and ignored: leaving a stale chunk is recoverable, deleting
    live chunks because a bookkeeping call failed is not.
    """
    try:
        listed = await client.get(
            f"{RAG_SVC}/rag/indexed-paths",
            params={"user_id": user_id, "collection_name": collection_name},
            headers={"X-Internal-Secret": INTERNAL_SECRET},
            timeout=aiohttp.ClientTimeout(total=30.0, connect=5.0),
        )
        if listed.status != 200:
            log.warning(
                "Stale-path lookup returned %s; leaving %s untouched for %s",
                listed.status, collection_name, user_id,
            )
            return
        known = set((await listed.json() or {}).get("paths") or [])
    except Exception as e:
        log.warning("Stale-path lookup failed (%s); leaving %s untouched for %s", e, collection_name, user_id)
        return

    stale = sorted(known - scanned_paths)
    if not stale:
        return

    removed = 0
    for path in stale:
        try:
            resp = await client.post(
                f"{RAG_SVC}/rag/purge/{collection_name}",
                json={"user_id": user_id, "filter": {"path": path}},
                headers={"X-Internal-Secret": INTERNAL_SECRET},
                timeout=aiohttp.ClientTimeout(total=30.0, connect=5.0),
            )
            if resp.status == 200:
                removed += 1
            else:
                log.warning("Stale purge for %s returned %s", path, resp.status)
        except Exception as e:
            log.warning("Stale purge for %s failed: %s", path, e)
    log.info("Removed %s stale path(s) from %s for %s", removed, collection_name, user_id)


@app.post("/index/pause", dependencies=[Depends(_require_internal_secret)])
def pause_indexing():
    set_indexer_pause(True)
    return {"status": "PAUSED"}

@app.post("/index/resume", dependencies=[Depends(_require_internal_secret)])
def resume_indexing():
    set_indexer_pause(False)
    return {"status": "RESUMED"}


@app.post("/providers/list", dependencies=[Depends(_require_internal_secret)])
async def list_provider_entries(req: IndexScanRequest):
    try:
        provider = build_provider(req.provider)
        entries = await provider.list_entries(path=req.path, recursive=req.recursive)

        # Cross-reference with RAG to set indexed status
        user_id = req.provider.settings.get("username", "admin")
        indexed_paths = set()

        async with get_client() as client:
            try:
                # Query RAG for all indexed paths for this user
                rag_resp = await client.get(
                    f"{RAG_SVC}/rag/indexed-paths?user_id={user_id}",
                    headers={"X-Internal-Secret": INTERNAL_SECRET},
                    timeout=aiohttp.ClientTimeout(total=10.0),
                )
                if rag_resp.status == 200:
                    indexed_paths = set((await rag_resp.json()).get("paths", []))
            except Exception as e:
                log.warning(f"Failed to fetch indexed paths from RAG: {e}")

        # Map indexed status to entries
        result_entries = []
        for entry in entries:
            e_dict = entry.model_dump()
            e_dict["indexed"] = e_dict["path"] in indexed_paths
            result_entries.append(e_dict)

        return {"status": "SUCCESS", "count": len(result_entries), "entries": result_entries}
    except Exception as e:
        log.error(f"List failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/providers/fetch", dependencies=[Depends(_require_internal_secret)])
async def fetch_provider_file(req: ProviderFetchRequest):
    """Hand a document back as bytes to another service.

    The other side of this is an importer that has to parse the file itself --
    a Bible arriving as an EPUB or a PDF, for instance. Routing the bytes
    through this service rather than having the caller build its own WebDAV
    client is what keeps the Nextcloud credentials in one place; a second copy
    of that client is a second copy of the credentials to rotate.

    ``max_bytes`` is refused rather than truncated. A silently shortened EPUB
    looks to an importer like a complete one that happens to end early, and the
    failure surfaces much later as missing chapters.
    """
    try:
        provider = build_provider(req.provider)
        if req.max_bytes <= 0:
            raise HTTPException(
                status_code=422,
                detail="max_bytes has to be a positive number of bytes.",
            )

        payload = await provider.get_bytes(req.path)
        if payload is None:
            raise HTTPException(
                status_code=502,
                detail=(
                    f"{req.provider.kind} could not read {req.path!r} as bytes. "
                    "Either the file is missing, the credentials are wrong, or "
                    "this provider cannot return binary content."
                ),
            )

        size = len(payload)
        if size > req.max_bytes:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"{req.path!r} is {size} bytes, over the {req.max_bytes} byte limit "
                    "for one fetch. Raise the limit if that file really is wanted."
                ),
            )

        return {
            "status": "SUCCESS",
            "path": req.path,
            "name": req.path.rsplit("/", 1)[-1],
            "size": size,
            "content_b64": base64.b64encode(payload).decode("ascii"),
        }
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Fetch failed for {req.path}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/providers/search", dependencies=[Depends(_require_internal_secret)])
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


@app.post("/providers/write", dependencies=[Depends(_require_internal_secret)])
async def write_provider_content(req: ProviderWriteRequest):
    try:
        import base64
        provider = build_provider(req.provider)

        content: str | bytes | None = req.content
        is_binary = False
        if req.content_b64:
            content = base64.b64decode(req.content_b64)
            is_binary = True

        if content is None:
            raise HTTPException(status_code=400, detail="Either content or content_b64 must be provided")

        result = await provider.write_content(
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


@app.post("/providers/mirror", dependencies=[Depends(_require_internal_secret)])
async def mirror_provider_directory(req: ProviderMirrorRequest):
    try:
        provider = build_provider(req.provider)
        upload_fn = getattr(provider, "upload_directory", None)
        if upload_fn is None:
            raise HTTPException(status_code=400, detail="Provider does not support directory mirroring")

        result = await upload_fn(req.remote_path, req.local_path, excludes=req.excludes)
        return {"status": "SUCCESS", "result": result}
    except Exception as e:
        log.error(f"Provider mirror failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

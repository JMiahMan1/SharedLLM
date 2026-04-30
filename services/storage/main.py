# services/storage/main.py
import logging

from fastapi import FastAPI, HTTPException

try:
    from .indexer import build_content_index, summarize_index
    from .models import IndexScanRequest, ProviderListRequest
    from .providers import build_provider
except ImportError:
    from indexer import build_content_index, summarize_index
    from models import IndexScanRequest, ProviderListRequest
    from providers import build_provider

app = FastAPI(title="SharedLLM Storage Bridge")
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("storage")


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
    log.info(f"Searching Nextcloud: {len(response['entries'])} entries found, query='{query}'")
    
    # Try exact name match
    matches = [entry for entry in response["entries"] if query.lower() in entry["name"].lower()]
    
    # Fallback for broad listing queries
    if not matches and any(k in query.lower() for k in ["list", "files", "folders", "what", "show", "get"]):
        log.info("No direct match, returning top-level entries for broad query")
        matches = [e for e in response["entries"] if e["path"].count("/") <= 1]
        
    return {"status": "SUCCESS", "matches": matches[:15]}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8005)

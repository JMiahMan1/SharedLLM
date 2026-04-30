# services/storage/main.py
from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel
import logging
try:
    from .nextcloud_client import NextCloudClient
except ImportError:
    from nextcloud_client import NextCloudClient

app = FastAPI(title="SharedLLM Storage Bridge")
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("storage")

class StorageRequest(BaseModel):
    nc_url: str
    nc_user: str
    nc_pass: str
    path: str = "/"

@app.get("/health")
def health():
    return {"status": "ok", "service": "storage"}

@app.post("/nextcloud/list")
async def list_nextcloud(req: StorageRequest):
    """List files in NextCloud."""
    client = NextCloudClient(req.nc_url, req.nc_user, req.nc_pass)
    files = client.list_files(req.path)
    
    # Format files for the LLM
    result = []
    for f in files:
        # easywebdav returns File objects
        result.append({
            "name": f.name,
            "size": f.size,
            "mtime": f.mtime,
            "content_type": f.contenttype
        })
    return {"status": "SUCCESS", "files": result}

@app.post("/nextcloud/search")
async def search_nextcloud(req: StorageRequest, query: str):
    """Search for files matching a query (stub)."""
    # For now, just list and filter by name
    client = NextCloudClient(req.nc_url, req.nc_user, req.nc_pass)
    files = client.list_files(req.path)
    matches = [f for f in files if query.lower() in f.name.lower()]
    return {"status": "SUCCESS", "matches": matches}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8005)

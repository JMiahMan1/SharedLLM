# services/execution/handlers/storage.py
import logging
import requests
from typing import Dict, Any
from ..schemas import StorageFileReadRequest, StorageFileWriteRequest, ExecutionResult
from ..nextcloud_client import resolve_credentials, webdav_url

log = logging.getLogger("execution.storage")

def _ok(message: str, detail: dict | None = None) -> ExecutionResult:
    return ExecutionResult(status="SUCCESS", message=message, service="storage", detail=detail)

def _fail(message: str, detail: dict | None = None) -> ExecutionResult:
    return ExecutionResult(status="FAILURE", message=message, service="storage", detail=detail)

async def handle_storage_read(req: StorageFileReadRequest) -> ExecutionResult:
    try:
        url, user, pw = resolve_credentials(req.user_context)
        if not all([url, user, pw]):
            return _fail("Missing Nextcloud credentials.")
            
        file_url = webdav_url(url, user, req.path)
        resp = requests.get(file_url, auth=(user, pw), timeout=30, verify=False)
        
        if resp.status_code == 200:
            content = resp.text
            return _ok(f"Read {len(content)} bytes from storage:{req.path}", {"content": content, "path": req.path})
        elif resp.status_code == 404:
            return _fail(f"File not found in storage: {req.path}")
        else:
            return _fail(f"Nextcloud error {resp.status_code}: {resp.text[:200]}")
            
    except Exception as e:
        log.error(f"Storage read failed: {e}")
        return _fail(str(e))

async def handle_storage_write(req: StorageFileWriteRequest) -> ExecutionResult:
    try:
        url, user, pw = resolve_credentials(req.user_context)
        if not all([url, user, pw]):
            return _fail("Missing Nextcloud credentials.")
            
        file_url = webdav_url(url, user, req.path)
        # Ensure parent directories exist (Nextcloud WebDAV doesn't do this automatically with PUT)
        # For simplicity in this handler, we'll just try the PUT
        resp = requests.put(file_url, auth=(user, pw), data=req.content, timeout=30, verify=False)
        
        if resp.status_code in (200, 201, 204):
            return _ok(f"Successfully wrote to storage:{req.path}")
        else:
            return _fail(f"Nextcloud error {resp.status_code}: {resp.text[:200]}")
            
    except Exception as e:
        log.error(f"Storage write failed: {e}")
        return _fail(str(e))

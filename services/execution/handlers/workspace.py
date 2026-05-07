# services/execution/handlers/workspace.py
import os
import logging
import difflib
from typing import Dict, Any, Optional
from ..schemas import WorkspaceFileReadRequest, WorkspaceFileWriteRequest, ExecutionResult

log = logging.getLogger("execution.workspace")

WORKSPACE_ROOT = os.getenv("WORKSPACE_ROOT", "/workspace/SharedLLM")

def _ok(message: str, detail: dict | None = None) -> ExecutionResult:
    return ExecutionResult(status="SUCCESS", message=message, service="workspace", detail=detail)

def _fail(message: str, detail: dict | None = None) -> ExecutionResult:
    return ExecutionResult(status="FAILURE", message=message, service="workspace", detail=detail)

def resolve_safe_path(path: str) -> str:
    """Ensure the path stays within WORKSPACE_ROOT."""
    # Remove leading slash if present to make it relative to root
    rel_path = path.lstrip("/")
    abs_path = os.path.abspath(os.path.join(WORKSPACE_ROOT, rel_path))
    if not abs_path.startswith(os.path.abspath(WORKSPACE_ROOT)):
        raise ValueError(f"Path traversal detected: {path}")
    return abs_path

async def handle_workspace_read(req: WorkspaceFileReadRequest) -> ExecutionResult:
    try:
        abs_path = resolve_safe_path(req.path)
        if not os.path.exists(abs_path):
            return _fail(f"File not found: {req.path}")
        if not os.path.isfile(abs_path):
            return _fail(f"Path is not a file: {req.path}")
        
        with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
            
        return _ok(f"Read {len(content)} bytes from {req.path}", {"content": content, "path": req.path})
    except Exception as e:
        log.error(f"Workspace read failed: {e}")
        return _fail(str(e))

async def handle_workspace_write(req: WorkspaceFileWriteRequest) -> ExecutionResult:
    try:
        abs_path = resolve_safe_path(req.path)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        
        original_content = ""
        if os.path.exists(abs_path):
            with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                original_content = f.read()
        
        new_content = req.content
        if req.is_patch:
            # Simple unified diff patch support could go here, 
            # but for now we'll assume content IS the new content or handle patch logic.
            # In SharedLLM, 'is_patch' usually means the LLM provided a diff.
            # However, for simplicity and reliability, we prefer full writes.
            pass

        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(new_content)
            
        message = f"Successfully wrote to {req.path}."
        if req.commit_after:
            # We would normally trigger git commit here, but for now we'll just log it
            message += " (Commit pending)"
            
        return _ok(message, {"path": req.path, "bytes": len(new_content)})
    except Exception as e:
        log.error(f"Workspace write failed: {e}")
        return _fail(str(e))

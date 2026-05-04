import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field


log = logging.getLogger("workspace_runtime")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s")

INTERNAL_SECRET = os.getenv("INTERNAL_SECRET", "change-me-in-production")
WORKSPACE_REGISTRY_PATH = os.getenv("WORKSPACE_REGISTRY_PATH", "/app/config/workspaces.json")
WORKSPACE_ROOT = Path(os.getenv("WORKSPACE_RUNTIME_ROOT", "/workspace")).resolve()
DEFAULT_PYTEST_TIMEOUT_SECONDS = int(os.getenv("WORKSPACE_RUNTIME_PYTEST_TIMEOUT_SECONDS", "90"))
DEFAULT_FILE_READ_LIMIT = int(os.getenv("WORKSPACE_RUNTIME_FILE_READ_LIMIT", "20000"))

app = FastAPI(title="SharedLLM Workspace Runtime")


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    err_msg = f"Workspace Runtime Error: {type(exc).__name__}: {str(exc)}"
    log.error(err_msg, exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"status": "ERROR", "message": "Internal Workspace Runtime Error", "detail": str(exc)},
    )


class WorkspaceRef(BaseModel):
    workspace_id: Optional[str] = None
    local_path: Optional[str] = None


class FileReadRequest(WorkspaceRef):
    relative_path: str
    max_bytes: int = Field(default=DEFAULT_FILE_READ_LIMIT, ge=1, le=200000)


class PytestRequest(WorkspaceRef):
    targets: list[str] = Field(default_factory=list)
    timeout_seconds: int = Field(default=DEFAULT_PYTEST_TIMEOUT_SECONDS, ge=1, le=900)


class DiffRequest(WorkspaceRef):
    ref: str = "HEAD"
    pathspecs: list[str] = Field(default_factory=list)


def _require_internal_secret(x_internal_secret: Optional[str]) -> None:
    if x_internal_secret != INTERNAL_SECRET:
        raise HTTPException(status_code=403, detail="Invalid internal secret")


def _load_registry() -> list[dict[str, Any]]:
    registry_path = Path(WORKSPACE_REGISTRY_PATH)
    if not registry_path.exists():
        log.warning("Workspace registry not found at %s", registry_path)
        return []
    try:
        data = json.loads(registry_path.read_text())
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to load workspace registry: {exc}")

    if isinstance(data, dict):
        items = data.get("workspaces", [])
    elif isinstance(data, list):
        items = data
    else:
        raise HTTPException(status_code=500, detail="Workspace registry format is invalid")

    return [item for item in items if isinstance(item, dict)]


def _safe_workspace_path(local_path: str) -> Path:
    candidate = Path(local_path)
    if not candidate.is_absolute():
        candidate = WORKSPACE_ROOT / candidate
    resolved = candidate.resolve()
    try:
        resolved.relative_to(WORKSPACE_ROOT)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Workspace path escapes root: {resolved}") from exc
    if not resolved.exists():
        raise HTTPException(status_code=404, detail=f"Workspace path not found: {resolved}")
    if not resolved.is_dir():
        raise HTTPException(status_code=400, detail=f"Workspace path is not a directory: {resolved}")
    return resolved


def _resolve_workspace(ref: WorkspaceRef) -> dict[str, Any]:
    registry = _load_registry()
    match = None

    if ref.workspace_id:
        match = next((item for item in registry if item.get("id") == ref.workspace_id), None)
    elif ref.local_path:
        match = next((item for item in registry if item.get("local_path") == ref.local_path), None)
        if match is None:
            match = {"id": "ad_hoc", "display_name": "Ad Hoc Workspace", "local_path": ref.local_path}
    else:
        raise HTTPException(status_code=400, detail="workspace_id or local_path is required")

    if match is None:
        raise HTTPException(status_code=404, detail="Workspace not found")

    resolved_path = _safe_workspace_path(str(match["local_path"]))
    workspace = dict(match)
    workspace["resolved_path"] = str(resolved_path)
    return workspace


def _safe_file_path(workspace_path: Path, relative_path: str) -> Path:
    target = (workspace_path / relative_path).resolve()
    try:
        target.relative_to(workspace_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"File path escapes workspace: {relative_path}") from exc
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {relative_path}")
    if not target.is_file():
        raise HTTPException(status_code=400, detail=f"Path is not a file: {relative_path}")
    return target


def _run_command(workspace_path: Path, args: list[str], timeout_seconds: int = 30) -> dict[str, Any]:
    proc = subprocess.run(
        args,
        cwd=workspace_path,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )
    return {
        "args": args,
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }


def _sanitize_targets(targets: list[str]) -> list[str]:
    cleaned = []
    for value in targets:
        if not value:
            continue
        target = str(value).strip()
        if target.startswith("-"):
            raise HTTPException(status_code=400, detail=f"Unsupported pytest argument: {target}")
        normalized = Path(target)
        if normalized.is_absolute():
            raise HTTPException(status_code=400, detail=f"Absolute pytest target not allowed: {target}")
        if ".." in normalized.parts:
            raise HTTPException(status_code=400, detail=f"Parent traversal not allowed in pytest target: {target}")
        cleaned.append(target)
    return cleaned


@app.get("/health")
def health():
    return {"status": "ok", "service": "workspace_runtime", "workspace_root": str(WORKSPACE_ROOT)}


@app.get("/workspaces")
def list_workspaces(x_internal_secret: Optional[str] = Header(default=None)):
    _require_internal_secret(x_internal_secret)
    items = []
    for entry in _load_registry():
        item = dict(entry)
        try:
            item["resolved_path"] = str(_safe_workspace_path(str(entry["local_path"])))
            item["available"] = True
        except HTTPException:
            item["resolved_path"] = None
            item["available"] = False
        items.append(item)
    return {"status": "SUCCESS", "workspaces": items}


@app.post("/workspace/resolve")
def resolve_workspace(req: WorkspaceRef, x_internal_secret: Optional[str] = Header(default=None)):
    _require_internal_secret(x_internal_secret)
    workspace = _resolve_workspace(req)
    return {"status": "SUCCESS", "workspace": workspace}


@app.post("/files/read")
def read_file(req: FileReadRequest, x_internal_secret: Optional[str] = Header(default=None)):
    _require_internal_secret(x_internal_secret)
    workspace = _resolve_workspace(req)
    workspace_path = Path(workspace["resolved_path"])
    target = _safe_file_path(workspace_path, req.relative_path)
    content = target.read_text(errors="replace")
    return {
        "status": "SUCCESS",
        "workspace": workspace,
        "relative_path": req.relative_path,
        "content": content[: req.max_bytes],
        "truncated": len(content) > req.max_bytes,
    }


@app.post("/git/status")
def git_status(req: WorkspaceRef, x_internal_secret: Optional[str] = Header(default=None)):
    _require_internal_secret(x_internal_secret)
    workspace = _resolve_workspace(req)
    workspace_path = Path(workspace["resolved_path"])
    branch = _run_command(workspace_path, ["git", "branch", "--show-current"])
    porcelain = _run_command(workspace_path, ["git", "status", "--short"])
    upstream = _run_command(workspace_path, ["git", "rev-parse", "--abbrev-ref", "@{upstream}"])
    return {
        "status": "SUCCESS",
        "workspace": workspace,
        "branch": branch["stdout"].strip(),
        "upstream": upstream["stdout"].strip() if upstream["returncode"] == 0 else None,
        "porcelain": porcelain["stdout"].splitlines(),
        "dirty": bool(porcelain["stdout"].strip()),
    }


@app.post("/git/diff")
def git_diff(req: DiffRequest, x_internal_secret: Optional[str] = Header(default=None)):
    _require_internal_secret(x_internal_secret)
    workspace = _resolve_workspace(req)
    workspace_path = Path(workspace["resolved_path"])
    pathspecs = _sanitize_targets(req.pathspecs)
    result = _run_command(workspace_path, ["git", "diff", req.ref, "--", *pathspecs] if pathspecs else ["git", "diff", req.ref])
    if result["returncode"] != 0:
        raise HTTPException(status_code=400, detail=result["stderr"].strip() or "git diff failed")
    return {"status": "SUCCESS", "workspace": workspace, "diff": result["stdout"]}


@app.post("/tests/pytest")
def run_pytest(req: PytestRequest, x_internal_secret: Optional[str] = Header(default=None)):
    _require_internal_secret(x_internal_secret)
    workspace = _resolve_workspace(req)
    workspace_path = Path(workspace["resolved_path"])
    targets = _sanitize_targets(req.targets)
    args = ["python", "-m", "pytest", "-q", *targets] if targets else ["python", "-m", "pytest", "-q"]
    result = _run_command(workspace_path, args, timeout_seconds=req.timeout_seconds)
    return {
        "status": "SUCCESS",
        "workspace": workspace,
        "command": result["args"],
        "returncode": result["returncode"],
        "stdout": result["stdout"],
        "stderr": result["stderr"],
        "passed": result["returncode"] == 0,
    }

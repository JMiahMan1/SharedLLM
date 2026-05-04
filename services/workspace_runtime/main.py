import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any, Optional

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field


log = logging.getLogger("workspace_runtime")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s")

INTERNAL_SECRET = os.getenv("INTERNAL_SECRET", "change-me-in-production")
IDENTITY_SVC_URL = os.getenv("IDENTITY_SVC_URL", "http://127.0.0.1:8001")
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
    rag_user: Optional[str] = None
    voice_id: Optional[str] = None
    device_id: Optional[str] = None


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


def _normalize_allowed_users(entry: dict[str, Any]) -> list[str]:
    raw = entry.get("allowed_users")
    if raw is None:
        raw = entry.get("owners")
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise HTTPException(status_code=500, detail="Workspace registry allowed_users must be a list")
    return [str(item).strip() for item in raw if str(item).strip()]


def _resolve_identity_context(ref: WorkspaceRef) -> Optional[dict[str, Any]]:
    payload = {}
    if ref.rag_user:
        payload["rag_user"] = ref.rag_user
    if ref.voice_id:
        payload["voice_id"] = ref.voice_id
    if ref.device_id:
        payload["device_id"] = ref.device_id
    if not payload:
        return None

    try:
        resp = httpx.post(
            f"{IDENTITY_SVC_URL}/api/resolve",
            json=payload,
            headers={"X-Internal-Secret": INTERNAL_SECRET},
            timeout=5.0,
        )
    except httpx.RequestError as exc:
        raise HTTPException(status_code=503, detail=f"Identity service unreachable: {exc}") from exc

    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail=f"Identity resolution failed: {resp.text}")

    data = resp.json()
    user = str(data.get("user") or "").strip() if isinstance(data, dict) else ""
    if not user:
        raise HTTPException(status_code=500, detail="Identity resolution did not return a user")
    return {"user": user, "is_admin": bool(data.get("is_admin"))}


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
    identity = _resolve_identity_context(ref)
    resolved_user = identity["user"] if identity else None
    is_admin = bool(identity and identity.get("is_admin"))
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

    allowed_users = _normalize_allowed_users(match)
    if allowed_users:
        if not resolved_user:
            raise HTTPException(status_code=400, detail="User context is required for this workspace")
        if resolved_user not in allowed_users and not is_admin:
            raise HTTPException(status_code=403, detail=f"Workspace '{match.get('id')}' is not available for user '{resolved_user}'")

    resolved_path = _safe_workspace_path(str(match["local_path"]))
    workspace = dict(match)
    workspace["resolved_path"] = str(resolved_path)
    workspace["scope"] = str(workspace.get("scope") or "user")
    workspace["capabilities"] = _workspace_capabilities(workspace)
    if resolved_user:
        workspace["resolved_user"] = resolved_user
    if identity:
        workspace["resolved_identity"] = identity
    return workspace


def _workspace_capabilities(workspace: dict[str, Any]) -> list[str]:
    raw = workspace.get("capabilities")
    if isinstance(raw, list) and raw:
        return [str(item).strip() for item in raw if str(item).strip()]
    scope = str(workspace.get("scope") or "user").strip().lower()
    if scope == "system":
        return ["read", "git_status", "git_diff"]
    return ["read", "git_status", "git_diff", "pytest"]


def _require_workspace_capability(workspace: dict[str, Any], capability: str) -> None:
    identity = workspace.get("resolved_identity") or {}
    if identity.get("is_admin"):
        return
    capabilities = _workspace_capabilities(workspace)
    if capability not in capabilities:
        raise HTTPException(
            status_code=403,
            detail=f"Workspace '{workspace.get('id')}' does not allow capability '{capability}'",
        )


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
def list_workspaces(
    rag_user: Optional[str] = None,
    voice_id: Optional[str] = None,
    device_id: Optional[str] = None,
    x_internal_secret: Optional[str] = Header(default=None),
):
    _require_internal_secret(x_internal_secret)
    ref = WorkspaceRef(rag_user=rag_user, voice_id=voice_id, device_id=device_id)
    identity = _resolve_identity_context(ref)
    resolved_user = identity["user"] if identity else None
    is_admin = bool(identity and identity.get("is_admin"))
    items = []
    for entry in _load_registry():
        item = dict(entry)
        allowed_users = _normalize_allowed_users(entry)
        if allowed_users and resolved_user and resolved_user not in allowed_users and not is_admin:
            continue
        if allowed_users and not resolved_user:
            item["available"] = False
            item["resolved_path"] = None
            item["requires_user_context"] = True
            items.append(item)
            continue
        try:
            item["resolved_path"] = str(_safe_workspace_path(str(entry["local_path"])))
            item["available"] = True
        except HTTPException:
            item["resolved_path"] = None
            item["available"] = False
        item["scope"] = str(item.get("scope") or "user")
        item["capabilities"] = _workspace_capabilities(item)
        if allowed_users:
            item["allowed_users"] = allowed_users
        if resolved_user:
            item["resolved_user"] = resolved_user
        if identity:
            item["resolved_identity"] = identity
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
    _require_workspace_capability(workspace, "read")
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
    _require_workspace_capability(workspace, "git_status")
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
    _require_workspace_capability(workspace, "git_diff")
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
    _require_workspace_capability(workspace, "pytest")
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

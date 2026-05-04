import json
import logging
import os
import subprocess
import hashlib
import tempfile
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field


log = logging.getLogger("workspace_runtime")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s")

INTERNAL_SECRET = os.getenv("INTERNAL_SECRET", "change-me-in-production")
IDENTITY_SVC_URL = os.getenv("IDENTITY_SVC_URL", "http://127.0.0.1:8001")
STORAGE_SVC_URL = os.getenv("STORAGE_SVC_URL", "http://127.0.0.1:8005")
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


class FileListRequest(WorkspaceRef):
    relative_path: str = "."
    recursive: bool = False
    max_depth: int = Field(default=2, ge=0, le=8)
    max_entries: int = Field(default=200, ge=1, le=2000)
    include_dirs: bool = True


class FileWriteRequest(WorkspaceRef):
    relative_path: str
    content: str
    expected_sha256: Optional[str] = None
    create_parents: bool = False


class PytestRequest(WorkspaceRef):
    targets: list[str] = Field(default_factory=list)
    timeout_seconds: int = Field(default=DEFAULT_PYTEST_TIMEOUT_SECONDS, ge=1, le=900)


class DiffRequest(WorkspaceRef):
    ref: str = "HEAD"
    pathspecs: list[str] = Field(default_factory=list)


class GitAddRequest(WorkspaceRef):
    pathspecs: list[str] = Field(default_factory=list)


class GitCommitRequest(WorkspaceRef):
    message: str
    pathspecs: list[str] = Field(default_factory=list)
    author_name: Optional[str] = None
    author_email: Optional[str] = None
    allow_empty: bool = False


class GitBranchCreateRequest(WorkspaceRef):
    branch_name: str
    from_ref: Optional[str] = None
    checkout: bool = True


class GitPushRequest(WorkspaceRef):
    remote: Optional[str] = None
    branch: Optional[str] = None
    set_upstream: bool = False


class ProviderScanRequest(WorkspaceRef):
    recursive: bool = True


class ProviderSyncFileRequest(WorkspaceRef):
    relative_path: str
    create_parents: bool = True
    verify: bool = True


class WorkflowWriteSyncCommitRequest(WorkspaceRef):
    relative_path: str
    content: str
    commit_message: str
    expected_sha256: Optional[str] = None
    create_parents: bool = False
    sync_to_provider: bool = True
    verify_provider_write: bool = True
    pytest_targets: list[str] = Field(default_factory=list)
    pytest_timeout_seconds: int = Field(default=DEFAULT_PYTEST_TIMEOUT_SECONDS, ge=1, le=900)
    push: bool = False
    remote: Optional[str] = None
    branch: Optional[str] = None
    set_upstream: bool = False
    author_name: Optional[str] = None
    author_email: Optional[str] = None
    allow_empty_commit: bool = False


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


def _workspace_access_policy(entry: dict[str, Any]) -> str:
    policy = str(entry.get("access_policy") or "authenticated").strip().lower()
    if policy not in {"authenticated", "admin_only"}:
        raise HTTPException(status_code=500, detail=f"Unsupported workspace access_policy: {policy}")
    return policy


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
    return data


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

    access_policy = _workspace_access_policy(match)
    if access_policy == "authenticated" and not resolved_user:
        raise HTTPException(status_code=400, detail="User context is required for this workspace")
    if access_policy == "admin_only" and not is_admin:
        raise HTTPException(status_code=403, detail=f"Workspace '{match.get('id')}' requires an admin identity")

    resolved_path = _safe_workspace_path(str(match["local_path"]))
    workspace = dict(match)
    workspace["resolved_path"] = str(resolved_path)
    workspace["scope"] = str(workspace.get("scope") or "user")
    workspace["access_policy"] = access_policy
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
    return ["read", "write", "git_status", "git_diff", "git_write", "pytest"]


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


def _safe_target_path(workspace_path: Path, relative_path: str) -> Path:
    target = (workspace_path / relative_path).resolve()
    try:
        target.relative_to(workspace_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"File path escapes workspace: {relative_path}") from exc
    return target


def _list_workspace_entries(
    workspace_path: Path,
    relative_path: str,
    recursive: bool,
    max_depth: int,
    max_entries: int,
    include_dirs: bool,
) -> tuple[Path, list[dict[str, Any]], bool]:
    root = _safe_target_path(workspace_path, relative_path)
    if not root.exists():
        raise HTTPException(status_code=404, detail=f"Path not found: {relative_path}")
    if not root.is_dir():
        raise HTTPException(status_code=400, detail=f"Path is not a directory: {relative_path}")

    entries: list[dict[str, Any]] = []
    truncated = False

    def walk(current: Path, depth: int) -> None:
        nonlocal truncated
        if truncated:
            return
        if recursive and depth > max_depth:
            return

        for child in sorted(current.iterdir(), key=lambda item: item.name.lower()):
            try:
                child.relative_to(workspace_path)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=f"Listed path escapes workspace: {child}") from exc

            rel_path = child.relative_to(workspace_path).as_posix()
            is_dir = child.is_dir()
            if include_dirs or not is_dir:
                entries.append(
                    {
                        "path": rel_path,
                        "name": child.name,
                        "is_dir": is_dir,
                        "size": child.stat().st_size if child.is_file() else None,
                    }
                )
                if len(entries) >= max_entries:
                    truncated = True
                    return

            if recursive and is_dir and depth < max_depth:
                walk(child, depth + 1)
                if truncated:
                    return

    walk(root, 0)
    return root, entries, truncated


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _run_command(
    workspace_path: Path,
    args: list[str],
    timeout_seconds: int = 30,
    env_overrides: Optional[dict[str, str]] = None,
) -> dict[str, Any]:
    env = os.environ.copy()
    if env_overrides:
        env.update(env_overrides)
    proc = subprocess.run(
        args,
        cwd=workspace_path,
        capture_output=True,
        text=True,
        env=env,
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


def _derive_git_author(identity: dict[str, Any], author_name: Optional[str], author_email: Optional[str]) -> tuple[str, str]:
    name = (author_name or identity.get("user") or "sharedllm").strip()
    email = (author_email or "").strip()
    if email:
        return name, email

    github_user = str(identity.get("github_user") or "").strip()
    gitlab_user = str(identity.get("gitlab_user") or "").strip()
    resolved_user = str(identity.get("user") or "sharedllm").strip()
    if github_user:
        return name, f"{github_user}@users.noreply.github.com"
    if gitlab_user:
        return name, f"{gitlab_user}@users.noreply.gitlab.local"
    return name, f"{resolved_user}@sharedllm.local"


def _validate_branch_name(branch_name: str) -> str:
    branch = branch_name.strip()
    if not branch:
        raise HTTPException(status_code=400, detail="branch_name is required")
    result = subprocess.run(
        ["git", "check-ref-format", "--branch", branch],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise HTTPException(status_code=400, detail=f"Invalid branch name: {branch}")
    return branch


def _git_remote_url(workspace_path: Path, remote_name: str) -> str:
    result = _run_command(workspace_path, ["git", "config", "--get", f"remote.{remote_name}.url"])
    if result["returncode"] != 0:
        raise HTTPException(status_code=400, detail=f"Git remote '{remote_name}' is not configured")
    remote_url = result["stdout"].strip()
    if not remote_url:
        raise HTTPException(status_code=400, detail=f"Git remote '{remote_name}' is empty")
    return remote_url


def _workspace_provider_binding(workspace: dict[str, Any], identity: dict[str, Any]) -> tuple[str, dict[str, Any], str]:
    provider_kind = str(workspace.get("provider_kind") or "").strip().lower()
    nextcloud_path = str(workspace.get("nextcloud_path") or "").strip()
    if not provider_kind and nextcloud_path:
        provider_kind = "nextcloud"

    if provider_kind == "nextcloud":
        url = str(identity.get("nextcloud_url") or "").strip()
        username = str(identity.get("nextcloud_user") or "").strip()
        password = str(identity.get("nextcloud_pass") or "").strip()
        if not (url and username and password):
            raise HTTPException(status_code=400, detail="Resolved identity does not include Nextcloud credentials")
        if not nextcloud_path:
            raise HTTPException(status_code=400, detail="Workspace does not define a nextcloud_path")
        return (
            "nextcloud",
            {"url": url, "username": username, "password": password},
            nextcloud_path,
        )

    raise HTTPException(status_code=400, detail="Workspace does not define a supported provider binding")


def _provider_child_path(base_path: str, relative_path: str) -> str:
    clean_base = "/" + str(base_path).strip("/")
    clean_relative = str(relative_path).strip("/")
    return clean_base if not clean_relative else f"{clean_base}/{clean_relative}"


def _storage_post(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        resp = httpx.post(path, json=payload, timeout=30.0)
    except httpx.RequestError as exc:
        raise HTTPException(status_code=503, detail=f"Storage service unreachable: {exc}") from exc
    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail=f"Storage request failed: {resp.text}")
    data = resp.json()
    if data.get("status") != "SUCCESS":
        raise HTTPException(status_code=500, detail=f"Storage request failed: {data}")
    return data


def _git_https_credentials(identity: dict[str, Any], remote_url: str) -> Optional[tuple[str, str]]:
    parsed = urlparse(remote_url)
    if parsed.scheme not in {"http", "https"}:
        return None

    remote_host = (parsed.hostname or "").lower()
    github_host = urlparse(str(identity.get("github_url") or "")).hostname or ""
    gitlab_host = urlparse(str(identity.get("gitlab_url") or "")).hostname or ""
    github_host = github_host.lower()
    gitlab_host = gitlab_host.lower()

    github_user = str(identity.get("github_user") or "").strip()
    github_token = str(identity.get("github_token") or "").strip()
    gitlab_user = str(identity.get("gitlab_user") or "").strip()
    gitlab_token = str(identity.get("gitlab_token") or "").strip()

    if github_token and ("github" in remote_host or (github_host and remote_host == github_host)):
        return (github_user or "x-access-token", github_token)
    if gitlab_token and ("gitlab" in remote_host or (gitlab_host and remote_host == gitlab_host)):
        return (gitlab_user or "oauth2", gitlab_token)
    return None


def _run_git_with_optional_askpass(
    workspace_path: Path,
    args: list[str],
    identity: dict[str, Any],
    remote_url: str,
    timeout_seconds: int = 60,
) -> dict[str, Any]:
    credentials = _git_https_credentials(identity, remote_url)
    if not credentials:
        return _run_command(workspace_path, args, timeout_seconds=timeout_seconds)

    username, password = credentials
    askpass_file = tempfile.NamedTemporaryFile("w", delete=False, prefix="sharedllm-git-askpass-", suffix=".sh")
    try:
        askpass_file.write("#!/bin/sh\n")
        askpass_file.write('case "$1" in\n')
        askpass_file.write('  *Username*) printf \'%s\\n\' \"$SHAREDLLM_GIT_USERNAME\" ;;\n')
        askpass_file.write('  *) printf \'%s\\n\' \"$SHAREDLLM_GIT_PASSWORD\" ;;\n')
        askpass_file.write("esac\n")
        askpass_file.flush()
        askpass_file.close()
        os.chmod(askpass_file.name, 0o700)
        return _run_command(
            workspace_path,
            args,
            timeout_seconds=timeout_seconds,
            env_overrides={
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_ASKPASS": askpass_file.name,
                "SHAREDLLM_GIT_USERNAME": username,
                "SHAREDLLM_GIT_PASSWORD": password,
            },
        )
    finally:
        try:
            os.unlink(askpass_file.name)
        except FileNotFoundError:
            pass


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
        access_policy = _workspace_access_policy(entry)
        if access_policy == "admin_only" and resolved_user and not is_admin:
            continue
        if access_policy in {"authenticated", "admin_only"} and not resolved_user:
            item["available"] = False
            item["resolved_path"] = None
            item["requires_user_context"] = True
            item["access_policy"] = access_policy
            items.append(item)
            continue
        try:
            item["resolved_path"] = str(_safe_workspace_path(str(entry["local_path"])))
            item["available"] = True
        except HTTPException:
            item["resolved_path"] = None
            item["available"] = False
        item["scope"] = str(item.get("scope") or "user")
        item["access_policy"] = access_policy
        item["capabilities"] = _workspace_capabilities(item)
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


@app.post("/files/list")
def list_files(req: FileListRequest, x_internal_secret: Optional[str] = Header(default=None)):
    _require_internal_secret(x_internal_secret)
    workspace = _resolve_workspace(req)
    _require_workspace_capability(workspace, "read")
    workspace_path = Path(workspace["resolved_path"])
    root, entries, truncated = _list_workspace_entries(
        workspace_path,
        req.relative_path,
        recursive=req.recursive,
        max_depth=req.max_depth,
        max_entries=req.max_entries,
        include_dirs=req.include_dirs,
    )
    return {
        "status": "SUCCESS",
        "workspace": workspace,
        "relative_path": req.relative_path,
        "resolved_path": root.relative_to(workspace_path).as_posix() if root != workspace_path else ".",
        "entries": entries,
        "truncated": truncated,
    }


@app.post("/files/write")
def write_file(req: FileWriteRequest, x_internal_secret: Optional[str] = Header(default=None)):
    _require_internal_secret(x_internal_secret)
    workspace = _resolve_workspace(req)
    _require_workspace_capability(workspace, "write")
    workspace_path = Path(workspace["resolved_path"])
    target = _safe_target_path(workspace_path, req.relative_path)

    if target.exists() and not target.is_file():
        raise HTTPException(status_code=400, detail=f"Path is not a file: {req.relative_path}")

    created = not target.exists()
    previous_sha256 = None
    if target.exists():
        current_bytes = target.read_bytes()
        previous_sha256 = _sha256_bytes(current_bytes)
        if req.expected_sha256 and req.expected_sha256 != previous_sha256:
            raise HTTPException(
                status_code=409,
                detail=f"File contents changed for {req.relative_path}; expected {req.expected_sha256}, found {previous_sha256}",
            )
    elif req.expected_sha256 not in (None, "", "new"):
        raise HTTPException(status_code=409, detail=f"File does not yet exist: {req.relative_path}")

    parent = target.parent
    if not parent.exists():
        if not req.create_parents:
            raise HTTPException(status_code=400, detail=f"Parent directory does not exist: {parent.relative_to(workspace_path)}")
        parent.mkdir(parents=True, exist_ok=True)

    target.write_text(req.content)
    written_bytes = req.content.encode()
    return {
        "status": "SUCCESS",
        "workspace": workspace,
        "relative_path": req.relative_path,
        "created": created,
        "bytes_written": len(written_bytes),
        "sha256": _sha256_bytes(written_bytes),
        "previous_sha256": previous_sha256,
    }


@app.post("/provider/scan")
def provider_scan(req: ProviderScanRequest, x_internal_secret: Optional[str] = Header(default=None)):
    _require_internal_secret(x_internal_secret)
    workspace = _resolve_workspace(req)
    _require_workspace_capability(workspace, "read")
    identity = workspace.get("resolved_identity") or {}
    provider_kind, provider_settings, provider_path = _workspace_provider_binding(workspace, identity)
    data = _storage_post(
        f"{STORAGE_SVC_URL}/providers/list",
        {
            "provider": {"kind": provider_kind, "settings": provider_settings},
            "path": provider_path,
            "recursive": req.recursive,
        },
    )
    return {
        "status": "SUCCESS",
        "workspace": workspace,
        "provider_kind": provider_kind,
        "provider_path": provider_path,
        "count": data.get("count", 0),
        "entries": data.get("entries", []),
    }


@app.post("/provider/sync/file")
def provider_sync_file(req: ProviderSyncFileRequest, x_internal_secret: Optional[str] = Header(default=None)):
    _require_internal_secret(x_internal_secret)
    workspace = _resolve_workspace(req)
    _require_workspace_capability(workspace, "write")
    workspace_path = Path(workspace["resolved_path"])
    local_file = _safe_file_path(workspace_path, req.relative_path)
    identity = workspace.get("resolved_identity") or {}
    provider_kind, provider_settings, provider_root = _workspace_provider_binding(workspace, identity)
    provider_path = _provider_child_path(provider_root, req.relative_path)
    content = local_file.read_text(errors="replace")
    data = _storage_post(
        f"{STORAGE_SVC_URL}/providers/write",
        {
            "provider": {"kind": provider_kind, "settings": provider_settings},
            "path": provider_path,
            "content": content,
            "create_parents": req.create_parents,
            "verify": req.verify,
        },
    )
    return {
        "status": "SUCCESS",
        "workspace": workspace,
        "relative_path": req.relative_path,
        "provider_kind": provider_kind,
        "provider_path": provider_path,
        "result": data.get("result"),
    }


@app.post("/workflow/write-sync-commit")
def workflow_write_sync_commit(req: WorkflowWriteSyncCommitRequest, x_internal_secret: Optional[str] = Header(default=None)):
    _require_internal_secret(x_internal_secret)

    write_result = write_file(
        FileWriteRequest(
            workspace_id=req.workspace_id,
            local_path=req.local_path,
            rag_user=req.rag_user,
            voice_id=req.voice_id,
            device_id=req.device_id,
            relative_path=req.relative_path,
            content=req.content,
            expected_sha256=req.expected_sha256,
            create_parents=req.create_parents,
        ),
        x_internal_secret,
    )

    provider_sync_result = None
    if req.sync_to_provider:
        provider_sync_result = provider_sync_file(
            ProviderSyncFileRequest(
                workspace_id=req.workspace_id,
                local_path=req.local_path,
                rag_user=req.rag_user,
                voice_id=req.voice_id,
                device_id=req.device_id,
                relative_path=req.relative_path,
                create_parents=req.create_parents,
                verify=req.verify_provider_write,
            ),
            x_internal_secret,
        )

    pytest_result = None
    if req.pytest_targets:
        pytest_result = run_pytest(
            PytestRequest(
                workspace_id=req.workspace_id,
                local_path=req.local_path,
                rag_user=req.rag_user,
                voice_id=req.voice_id,
                device_id=req.device_id,
                targets=req.pytest_targets,
                timeout_seconds=req.pytest_timeout_seconds,
            ),
            x_internal_secret,
        )
        if not pytest_result.get("passed"):
            raise HTTPException(
                status_code=400,
                detail=f"Pytest failed for workflow request on {req.relative_path}",
            )

    commit_result = git_commit(
        GitCommitRequest(
            workspace_id=req.workspace_id,
            local_path=req.local_path,
            rag_user=req.rag_user,
            voice_id=req.voice_id,
            device_id=req.device_id,
            message=req.commit_message,
            pathspecs=[req.relative_path],
            author_name=req.author_name,
            author_email=req.author_email,
            allow_empty=req.allow_empty_commit,
        ),
        x_internal_secret,
    )

    push_result = None
    if req.push:
        push_result = git_push(
            GitPushRequest(
                workspace_id=req.workspace_id,
                local_path=req.local_path,
                rag_user=req.rag_user,
                voice_id=req.voice_id,
                device_id=req.device_id,
                remote=req.remote,
                branch=req.branch,
                set_upstream=req.set_upstream,
            ),
            x_internal_secret,
        )

    return {
        "status": "SUCCESS",
        "relative_path": req.relative_path,
        "write": write_result,
        "provider_sync": provider_sync_result,
        "pytest": pytest_result,
        "commit": commit_result,
        "push": push_result,
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


@app.post("/git/add")
def git_add(req: GitAddRequest, x_internal_secret: Optional[str] = Header(default=None)):
    _require_internal_secret(x_internal_secret)
    workspace = _resolve_workspace(req)
    _require_workspace_capability(workspace, "git_write")
    workspace_path = Path(workspace["resolved_path"])
    pathspecs = _sanitize_targets(req.pathspecs)
    args = ["git", "add", "--", *pathspecs] if pathspecs else ["git", "add", "-A"]
    result = _run_command(workspace_path, args)
    if result["returncode"] != 0:
        raise HTTPException(status_code=400, detail=result["stderr"].strip() or "git add failed")
    status_result = _run_command(workspace_path, ["git", "status", "--short"])
    return {
        "status": "SUCCESS",
        "workspace": workspace,
        "command": result["args"],
        "porcelain": status_result["stdout"].splitlines(),
    }


@app.post("/git/commit")
def git_commit(req: GitCommitRequest, x_internal_secret: Optional[str] = Header(default=None)):
    _require_internal_secret(x_internal_secret)
    workspace = _resolve_workspace(req)
    _require_workspace_capability(workspace, "git_write")
    workspace_path = Path(workspace["resolved_path"])
    identity = workspace.get("resolved_identity") or {}
    if req.pathspecs:
        add_req = GitAddRequest(
            workspace_id=req.workspace_id,
            local_path=req.local_path,
            rag_user=req.rag_user,
            voice_id=req.voice_id,
            device_id=req.device_id,
            pathspecs=req.pathspecs,
        )
        git_add(add_req, x_internal_secret)

    author_name, author_email = _derive_git_author(identity, req.author_name, req.author_email)
    args = ["git", "commit", "-m", req.message]
    if req.allow_empty:
        args.append("--allow-empty")
    result = _run_command(
        workspace_path,
        args,
        env_overrides={
            "GIT_AUTHOR_NAME": author_name,
            "GIT_AUTHOR_EMAIL": author_email,
            "GIT_COMMITTER_NAME": author_name,
            "GIT_COMMITTER_EMAIL": author_email,
        },
    )
    if result["returncode"] != 0:
        raise HTTPException(status_code=400, detail=result["stderr"].strip() or result["stdout"].strip() or "git commit failed")
    commit_ref = _run_command(workspace_path, ["git", "rev-parse", "HEAD"])
    return {
        "status": "SUCCESS",
        "workspace": workspace,
        "command": result["args"],
        "stdout": result["stdout"],
        "stderr": result["stderr"],
        "commit": commit_ref["stdout"].strip() if commit_ref["returncode"] == 0 else None,
        "author_name": author_name,
        "author_email": author_email,
    }


@app.post("/git/branch/create")
def git_branch_create(req: GitBranchCreateRequest, x_internal_secret: Optional[str] = Header(default=None)):
    _require_internal_secret(x_internal_secret)
    workspace = _resolve_workspace(req)
    _require_workspace_capability(workspace, "git_write")
    workspace_path = Path(workspace["resolved_path"])
    branch_name = _validate_branch_name(req.branch_name)
    if req.checkout:
        args = ["git", "checkout", "-b", branch_name]
        if req.from_ref:
            args.append(req.from_ref)
    else:
        args = ["git", "branch", branch_name]
        if req.from_ref:
            args.append(req.from_ref)
    result = _run_command(workspace_path, args)
    if result["returncode"] != 0:
        raise HTTPException(status_code=400, detail=result["stderr"].strip() or result["stdout"].strip() or "git branch create failed")
    current_branch = _run_command(workspace_path, ["git", "branch", "--show-current"])
    return {
        "status": "SUCCESS",
        "workspace": workspace,
        "command": result["args"],
        "stdout": result["stdout"],
        "stderr": result["stderr"],
        "branch": branch_name,
        "current_branch": current_branch["stdout"].strip(),
    }


@app.post("/git/push")
def git_push(req: GitPushRequest, x_internal_secret: Optional[str] = Header(default=None)):
    _require_internal_secret(x_internal_secret)
    workspace = _resolve_workspace(req)
    _require_workspace_capability(workspace, "git_write")
    workspace_path = Path(workspace["resolved_path"])
    identity = workspace.get("resolved_identity") or {}
    remote_name = (req.remote or workspace.get("git_remote") or "origin").strip()
    current_branch = _run_command(workspace_path, ["git", "branch", "--show-current"])
    branch_name = (req.branch or current_branch["stdout"].strip()).strip()
    if not branch_name:
        raise HTTPException(status_code=400, detail="Unable to determine branch to push")
    remote_url = _git_remote_url(workspace_path, remote_name)
    args = ["git", "push"]
    if req.set_upstream:
        args.append("-u")
    args.extend([remote_name, branch_name])
    result = _run_git_with_optional_askpass(
        workspace_path,
        args,
        identity=identity,
        remote_url=remote_url,
    )
    if result["returncode"] != 0:
        raise HTTPException(status_code=400, detail=result["stderr"].strip() or result["stdout"].strip() or "git push failed")
    upstream = _run_command(workspace_path, ["git", "rev-parse", "--abbrev-ref", "@{upstream}"])
    return {
        "status": "SUCCESS",
        "workspace": workspace,
        "command": args,
        "stdout": result["stdout"],
        "stderr": result["stderr"],
        "remote": remote_name,
        "branch": branch_name,
        "upstream": upstream["stdout"].strip() if upstream["returncode"] == 0 else None,
    }


@app.post("/tests/pytest")
def run_pytest(req: PytestRequest, x_internal_secret: Optional[str] = Header(default=None)):
    _require_internal_secret(x_internal_secret)
    workspace = _resolve_workspace(req)
    _require_workspace_capability(workspace, "pytest")
    workspace_path = Path(workspace["resolved_path"])
    targets = _sanitize_targets(req.targets)
    args = ["python", "-m", "pytest", "-q", *targets] if targets else ["python", "-m", "pytest", "-q"]
    result = _run_command(
        workspace_path,
        args,
        timeout_seconds=req.timeout_seconds,
        env_overrides={"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
    )
    return {
        "status": "SUCCESS",
        "workspace": workspace,
        "command": result["args"],
        "returncode": result["returncode"],
        "stdout": result["stdout"],
        "stderr": result["stderr"],
        "passed": result["returncode"] == 0,
    }

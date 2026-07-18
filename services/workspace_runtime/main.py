import asyncio
import fnmatch
import hashlib
import json
import logging
import os
import re
import subprocess
import tempfile
import threading
import time
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import aiohttp
import redis
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from services.common.http import get_client
from services.config import (
    IDENTITY_SVC_URL,
    INTERNAL_SECRET,
    REDIS_URL,
    STORAGE_SVC_URL,
    WORKSPACE_RUNTIME_FILE_READ_LIMIT,
    WORKSPACE_RUNTIME_PYTEST_TIMEOUT_SECONDS,
)
from services.config import (
    WORKSPACE_REGISTRY_PATH as _WRP,
)
from services.shared.info_endpoint import info_router

from .crypto import decrypt, encrypt
from .database import engine, init_db
from .models import Workspace

log = logging.getLogger("workspace_runtime")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s")

WORKSPACE_SYNC_LOCKS: dict[str, threading.RLock] = {}
WORKSPACE_LOCKS_MUTEX = threading.Lock()

def get_workspace_lock(workspace_id: str) -> threading.RLock:
    with WORKSPACE_LOCKS_MUTEX:
        if workspace_id not in WORKSPACE_SYNC_LOCKS:
            WORKSPACE_SYNC_LOCKS[workspace_id] = threading.RLock()
        return WORKSPACE_SYNC_LOCKS[workspace_id]

ASYNC_SYNC_LOCKS: dict[str, asyncio.Lock] = {}
ASYNC_LOCKS_MUTEX = threading.Lock()

def get_async_sync_lock(workspace_id: str) -> asyncio.Lock:
    with ASYNC_LOCKS_MUTEX:
        if workspace_id not in ASYNC_SYNC_LOCKS:
            ASYNC_SYNC_LOCKS[workspace_id] = asyncio.Lock()
        return ASYNC_SYNC_LOCKS[workspace_id]

WORKSPACE_REGISTRY_PATH = _WRP or "/app/config/workspaces.json"
_DEFAULT_WORKSPACE_ROOT = Path(os.getenv("WORKSPACE_RUNTIME_ROOT", "/workspace")).resolve()

async def _get_workspace_root_async() -> Path:
    """Async implementation of get_workspace_root."""
    try:
        # We use a short timeout and cache or just fallback if identity is down
        async with get_client() as client:
            resp = await client.get(
                f"{IDENTITY_SVC_URL}/api/settings/workspace_runtime_root",
                headers={"X-Internal-Secret": INTERNAL_SECRET},
                timeout=aiohttp.ClientTimeout(total=2.0),
            )
            if resp.status == 200:
                data = await resp.json()
                val = data.get("value")
                if val:
                    return Path(val).resolve()
    except Exception as e:
        log.debug(f"Failed to fetch workspace_runtime_root from identity: {e}")

    return _DEFAULT_WORKSPACE_ROOT


def get_workspace_root() -> Path:
    """Fetch the current workspace root from global settings or fallback to env/default."""
    try:
        return asyncio.run(_get_workspace_root_async())
    except Exception as e:
        log.debug(f"Failed to fetch workspace_runtime_root from identity: {e}")
        return _DEFAULT_WORKSPACE_ROOT


async def _get_config_timezone_async() -> str:
    """Return the Config DB `timezone` value (e.g. "America/Phoenix"), or UTC."""
    try:
        async with get_client() as client:
            resp = await client.get(
                f"{IDENTITY_SVC_URL}/api/settings/timezone",
                headers={"X-Internal-Secret": INTERNAL_SECRET},
                timeout=aiohttp.ClientTimeout(total=2.0),
            )
            if resp.status == 200:
                data = await resp.json()
                val = (data.get("value") or "").strip()
                if val:
                    return val
    except Exception as e:
        log.debug(f"Failed to fetch timezone from identity: {e}")
    return "UTC"


def get_config_timezone() -> str:
    """Synchronously resolve the configured timezone name (falls back to UTC)."""
    try:
        return asyncio.run(_get_config_timezone_async())
    except Exception as e:
        log.debug(f"Failed to resolve config timezone: {e}")
        return "UTC"


def _now_in_config_tz() -> datetime:
    """Current time as a timezone-aware datetime in the configured Config DB tz."""
    tz_name = get_config_timezone()
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("UTC")
    return datetime.now(tz)


WORKSPACE_ROOT = _DEFAULT_WORKSPACE_ROOT


# Cache the configured timezone (Config DB `timezone` setting) so per-workspace
# serialization doesn't hit identity on every request. Refreshed hourly.
_CONFIG_TZ_CACHE: dict[str, float] = {"tz": "UTC", "ts": 0.0}


def _cached_config_tz() -> str:
    now = time.time()
    if now - _CONFIG_TZ_CACHE["ts"] > 3600:
        _CONFIG_TZ_CACHE["tz"] = get_config_timezone()
        _CONFIG_TZ_CACHE["ts"] = now
    return _CONFIG_TZ_CACHE["tz"]


def _created_at_in_config_tz(value: "datetime | None") -> "str | None":
    """Render a stored (UTC) created_at as an offset-aware ISO string in the
    configured Config DB timezone, so the UI shows the operator's local time."""
    if not value:
        return None
    # SQLite TIMESTAMP columns return naive datetimes; treat them as UTC.
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    try:
        tz = ZoneInfo(_cached_config_tz())
    except Exception:
        tz = timezone.utc
    return value.astimezone(tz).isoformat()

DEFAULT_PYTEST_TIMEOUT_SECONDS = WORKSPACE_RUNTIME_PYTEST_TIMEOUT_SECONDS
DEFAULT_FILE_READ_LIMIT = WORKSPACE_RUNTIME_FILE_READ_LIMIT
DEFAULT_PROTECTED_BRANCH_PATTERNS = [
    pattern.strip()
    for pattern in os.getenv(
        "WORKSPACE_RUNTIME_PROTECTED_BRANCHES",
        "main,master,development,dev,release/*",
    ).split(",")
    if pattern.strip()
]

app = FastAPI(title="SharedLLM Workspace Runtime")

# --- Response sanitization: never leak secrets to clients ---
# The workspace dict carries a `resolved_identity` block (plaintext provider
# tokens: nextcloud / ha / github / mass / audiobookshelf / skylight / ...)
# that is only used internally (git auth, provider sync). It must never reach
# the API or the browser. Strip it (plus decrypted webhook tokens) from every
# JSON response body. Internal callers read secrets from the in-memory dict
# before the response is serialized, so this is purely an output boundary.
# Implemented as a raw ASGI middleware (not BaseHTTPMiddleware) to avoid the
# known body-streaming quirks that prevent rewriting the response body.
_STRIP_RESPONSE_KEYS = {"resolved_identity", "webhook_token", "webhook_token_enc"}


def _redact_secrets(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _redact_secrets(v) for k, v in obj.items() if k not in _STRIP_RESPONSE_KEYS}
    if isinstance(obj, list):
        return [_redact_secrets(v) for v in obj]
    return obj


class _RedactSecretsMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        start_message: dict = {}
        body_chunks: list[bytes] = []
        content_type = ""

        async def send_wrapper(message: dict):
            nonlocal start_message, content_type
            if message["type"] == "http.response.start":
                start_message = message
                for raw_k, raw_v in message.get("headers", []):
                    if raw_k.lower() == b"content-type":
                        content_type = raw_v.decode("latin-1", "replace")
                return
            if message["type"] == "http.response.body":
                body_chunks.append(message.get("body") or b"")
                if message.get("more_body"):
                    return
                # Full body received; rewrite JSON responses.
                body = b"".join(body_chunks)
                if "application/json" in content_type:
                    try:
                        data = json.loads(body)
                        body = json.dumps(_redact_secrets(data)).encode("utf-8")
                    except (ValueError, UnicodeDecodeError):
                        pass
                    new_headers = [
                        (k, v) for k, v in start_message.get("headers", [])
                        if k.lower() != b"content-length"
                    ]
                    new_headers.append((b"content-length", str(len(body)).encode()))
                    await send(
                        {
                            "type": "http.response.start",
                            "status": start_message.get("status", 200),
                            "headers": new_headers,
                        }
                    )
                else:
                    await send(start_message)
                await send({"type": "http.response.body", "body": body, "more_body": False})
                return

        await self.app(scope, receive, send_wrapper)



# --- Auto-Quarantine Configuration ---
def _safe_int_env(key: str, default: int) -> int:
    val = os.getenv(key)
    if val is None or val.strip() == "":
        return default
    try:
        return int(float(val))
    except ValueError:
        return default

RAVEN_QUARANTINE_THRESHOLD = _safe_int_env("RAVEN_QUARANTINE_THRESHOLD", 3)
RAVEN_QUARANTINE_WINDOW_SECONDS = _safe_int_env("RAVEN_QUARANTINE_WINDOW", 600)

_redis_client: redis.Redis | None = None

def _get_redis() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.from_url(REDIS_URL, decode_responses=True)
    return _redis_client

def _record_verification_failure(file_path: str) -> int:
    """Record a lint/pytest failure for a file and return the current count within the window."""
    r = _get_redis()
    key = f"workspace:quarantine:{file_path}"
    now = time.time()
    cutoff = now - RAVEN_QUARANTINE_WINDOW_SECONDS
    r.zremrangebyscore(key, 0, cutoff)
    r.zadd(key, {str(now): now})
    r.expire(key, RAVEN_QUARANTINE_WINDOW_SECONDS + 60)
    return r.zcard(key)  # type: ignore[return-value]

def _clear_verification_failures(file_path: str) -> None:
    """Clear failure history for a file after a successful run."""
    try:
        r = _get_redis()
        r.delete(f"workspace:quarantine:{file_path}")
    except Exception:
        pass

def _auto_quarantine_workspace(workspace_id: str, file_path: str, failure_count: int) -> None:
    """Flag a workspace as quarantined when a file exceeds the failure threshold."""
    try:
        with Session(engine) as session:
            ws = session.get(Workspace, workspace_id)
            if ws and not ws.quarantined:
                ws.quarantined = True
                log.warning(f"[Quarantine] Workspace {workspace_id} auto-quarantined: {file_path} failed {failure_count} times in {RAVEN_QUARANTINE_WINDOW_SECONDS}s")
                session.add(ws)
                session.commit()
    except Exception as e:
        log.error(f"[Quarantine] Failed to auto-quarantine workspace {workspace_id}: {e}")


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    err_msg = f"Workspace Runtime Error: {type(exc).__name__}: {exc!s}"
    log.error(err_msg, exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"status": "ERROR", "message": "Internal Workspace Runtime Error", "detail": str(exc)},
    )


class WorkspaceRef(BaseModel):
    workspace_id: str | None = None
    local_path: str | None = None
    rag_user: str | None = None
    voice_id: str | None = None
    device_id: str | None = None
    user_context: dict[str, Any] | None = None

    class Config:
        extra = "ignore"


class FileReadRequest(WorkspaceRef):
    relative_path: str
    max_bytes: int = Field(default=DEFAULT_FILE_READ_LIMIT, ge=1, le=200000)


class FileRawRequest(WorkspaceRef):
    relative_path: str


class FileListRequest(WorkspaceRef):
    relative_path: str = "."
    recursive: bool = False
    max_depth: int = Field(default=2, ge=0, le=8)
    max_entries: int = Field(default=200, ge=1, le=2000)
    include_dirs: bool = True


class FileWriteRequest(WorkspaceRef):
    relative_path: str
    content: str | None = None
    content_base64: str | None = None
    patch: str | None = None
    expected_sha256: str | None = None
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
    author_name: str | None = None
    author_email: str | None = None
    allow_empty: bool = False


class GitBranchCreateRequest(WorkspaceRef):
    branch_name: str
    from_ref: str | None = None
    checkout: bool = True


class GitPushRequest(WorkspaceRef):
    remote: str | None = None
    branch: str | None = None
    set_upstream: bool = False


class GitFetchRequest(WorkspaceRef):
    remote: str | None = None
    prune: bool = False


class GitPullRequest(WorkspaceRef):
    remote: str | None = None
    branch: str | None = None
    rebase: bool = False


class GitRevertRequest(WorkspaceRef):
    commit: str | None = None
    hard: bool = False


class GitRebaseRequest(WorkspaceRef):
    upstream: str
    branch: str | None = None


class GitLogRequest(WorkspaceRef):
    max_count: int = Field(default=20, ge=1, le=100)
    ref: str | None = None
    file_path: str | None = None
    oneline: bool = False


class GitCheckoutRequest(WorkspaceRef):
    branch: str
    create: bool = False
    from_ref: str | None = None


class GitStashRequest(WorkspaceRef):
    action: str = "save"  # save, pop, list, apply, drop
    message: str | None = None
    stash_index: int = 0


class GitRemoteRequest(WorkspaceRef):
    action: str = "list"  # list, add, remove, set_url
    name: str | None = None
    url: str | None = None


class GitShowRequest(WorkspaceRef):
    ref: str = "HEAD"
    file_path: str | None = None


class FileSearchRequest(WorkspaceRef):
    query: str
    relative_path: str = "."
    case_sensitive: bool = False
    max_results: int = Field(default=100, ge=1, le=500)
    file_pattern: str | None = None


class ProviderScanRequest(WorkspaceRef):
    recursive: bool = True


class ProviderSyncFileRequest(WorkspaceRef):
    relative_path: str
    create_parents: bool = True
    verify: bool = True


class ProviderSyncDirectoryRequest(WorkspaceRef):
    relative_path: str = "."
    recursive: bool = True


class WorkflowWriteSyncCommitRequest(WorkspaceRef):
    relative_path: str
    content: str
    commit_message: str
    expected_sha256: str | None = None
    create_parents: bool = False
    sync_to_provider: bool = True
    verify_provider_write: bool = True
    auto_create_review_branch: bool = True
    review_branch_prefix: str = "raven"
    lint_paths: list[str] = Field(default_factory=list)
    pytest_targets: list[str] = Field(default_factory=list)
    pytest_timeout_seconds: int = Field(default=DEFAULT_PYTEST_TIMEOUT_SECONDS, ge=1, le=900)
    push: bool = False
    remote: str | None = None
    branch: str | None = None
    set_upstream: bool = False
    author_name: str | None = None
    author_email: str | None = None
    allow_empty_commit: bool = False


class WorkspaceBootstrapRequest(WorkspaceRef):
    repo_url: str | None = None
    branch: str | None = None
    remote: str | None = None
    display_name: str | None = None
    create_if_missing: bool = False
    create_repo: bool = False
    repo_name: str | None = None
    repo_private: bool = True
    repo_description: str | None = None


def _require_internal_secret(x_internal_secret: str | None) -> None:
    if x_internal_secret != INTERNAL_SECRET:
        raise HTTPException(status_code=403, detail="Invalid internal secret")


def _load_registry() -> list[dict[str, Any]]:
    with Session(engine) as session:
        items = session.exec(select(Workspace)).all()
        return [_workspace_to_dict(item) for item in items]


def _workspace_to_dict(item: Workspace) -> dict[str, Any]:
    data = item.model_dump()
    # Emit created_at as an offset-aware ISO string in the configured timezone
    # so the UI "Created" label renders in the operator's local time.
    data["created_at"] = _created_at_in_config_tz(getattr(item, "created_at", None))
    data["webhook_token"] = decrypt(item.webhook_token_enc) if item.webhook_token_enc else item.webhook_token
    data.pop("webhook_token_enc", None)

    # Expose only the NAMES of per-workspace env/secret keys in public responses
    # (never the values). The decrypted values are only returned by the trusted
    # internal POST /workspace/resolve endpoint for sandbox env injection.
    _env_enc = getattr(item, "env_enc", None)
    if _env_enc:
        try:
            _env = json.loads(decrypt(_env_enc) or "{}")
            data["env_keys"] = sorted(_env.keys()) if isinstance(_env, dict) else []
        except Exception:
            data["env_keys"] = []
    else:
        data["env_keys"] = []
    data.pop("env_enc", None)

    # Ensure excludes is a parsed list of strings
    excludes = data.get("excludes")
    if isinstance(excludes, str):
        try:
            parsed = json.loads(excludes)
            if isinstance(parsed, list):
                data["excludes"] = parsed
            else:
                data["excludes"] = [excludes] if excludes else []
        except Exception:
            data["excludes"] = [excludes] if excludes else []
    elif excludes is None:
        data["excludes"] = []

    # Ensure capabilities is a parsed list of strings
    capabilities = data.get("capabilities")
    if isinstance(capabilities, str):
        try:
            parsed = json.loads(capabilities)
            if isinstance(parsed, list):
                data["capabilities"] = parsed
            else:
                data["capabilities"] = [capabilities] if capabilities else []
        except Exception:
            data["capabilities"] = [capabilities] if capabilities else []
    elif capabilities is None:
        data["capabilities"] = []

    return data


def _store_workspace_secret_fields(workspace: Workspace, updates: dict[str, Any] | None = None) -> None:
    incoming = updates if updates is not None else workspace.model_dump()
    if "webhook_token" in incoming:
        value = incoming.get("webhook_token")
        if isinstance(value, str):
            value = value.strip()
        value = value or None
        workspace.webhook_token_enc = encrypt(value) if value else None
        workspace.webhook_token = None


def _seed_db_from_json():
    registry_path = Path(WORKSPACE_REGISTRY_PATH)
    if not registry_path.exists():
        return

    with Session(engine) as session:
        # Only seed if DB is empty
        if session.exec(select(Workspace)).first():
            return

        log.info("Seeding DB from %s", registry_path)
        try:
            data = json.loads(registry_path.read_text())
            workspaces_data = data.get("workspaces", []) if isinstance(data, dict) else data
            for ws_data in workspaces_data:
                ws = Workspace(**ws_data)
                _store_workspace_secret_fields(ws)
                session.add(ws)
            session.commit()
            log.info("Successfully seeded %d workspaces", len(workspaces_data))
        except Exception as exc:
            log.error("Failed to seed DB: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Resolve runtime config from Identity service
    from services.config import resolve_runtime_config
    await resolve_runtime_config()

    # Startup logic
    init_db()

    # Handle dubious ownership in mounted volumes
    try:
        import subprocess
        subprocess.run(["git", "--version"], capture_output=True, check=True) # Basic check
        subprocess.run(["git", "config", "--global", "--add", "safe.directory", "*"], check=True, env={**os.environ, "HOME": "/home/sharedllm"})
        log.info("Added '*' to git safe.directory")
    except Exception as e:
        log.warning(f"Failed to set git safe.directory: {e}")

    _seed_db_from_json()

    with Session(engine) as session:
        pending = session.exec(select(Workspace).where(Workspace.webhook_token is not None)).all()
        migrated = 0
        for workspace in pending:
            if workspace.webhook_token_enc:
                continue
            _store_workspace_secret_fields(workspace)
            session.add(workspace)
            migrated += 1
        if migrated:
            session.commit()
            log.info("Migrated %d workspace webhook secrets to encrypted storage", migrated)

    log.info("Workspace Runtime service ready.")

    yield

    # Shutdown logic
    log.info("Workspace Runtime service shutting down.")

app = FastAPI(title="Jarvis Workspace Runtime", version="1.0.0", lifespan=lifespan)

app.add_middleware(_RedactSecretsMiddleware)
app.include_router(info_router)


def _workspace_access_policy(entry: dict[str, Any]) -> str:
    policy = str(entry.get("access_policy") or "authenticated").strip().lower()
    if policy not in {"authenticated", "admin_only"}:
        raise HTTPException(status_code=500, detail=f"Unsupported workspace access_policy: {policy}")
    return policy


# Dedicated event loop + aiohttp session for synchronous identity resolution.
# FastAPI runs endpoint handlers in a threadpool; calling async HTTP there
# requires its own loop. A single long-lived loop (run in a daemon thread)
# with one session avoids the "Event loop is closed" errors that occur when
# creating/closing a loop per call.
_IDENTITY_LOOP: asyncio.AbstractEventLoop | None = None
_IDENTITY_SESSION: aiohttp.ClientSession | None = None
_IDENTITY_LOCK = threading.Lock()


async def _ensure_identity_session() -> aiohttp.ClientSession:
    global _IDENTITY_SESSION
    if _IDENTITY_SESSION is None or _IDENTITY_SESSION.closed:
        # Created inside the loop thread so it binds to _IDENTITY_LOOP
        # (creating aiohttp.ClientSession() from a worker thread with no
        # running loop raises "RuntimeError: no running event loop").
        _IDENTITY_SESSION = aiohttp.ClientSession()
    return _IDENTITY_SESSION


def _get_identity_loop() -> asyncio.AbstractEventLoop:
    global _IDENTITY_LOOP
    with _IDENTITY_LOCK:
        if _IDENTITY_LOOP is None or _IDENTITY_LOOP.is_closed():
            _IDENTITY_LOOP = asyncio.new_event_loop()

            def _run() -> None:
                asyncio.set_event_loop(_IDENTITY_LOOP)
                _IDENTITY_LOOP.run_forever()

            threading.Thread(target=_run, daemon=True).start()
            # Give the loop thread a moment to start.
            _IDENTITY_LOOP.call_soon_threadsafe(lambda: None)
        return _IDENTITY_LOOP


def _resolve_identity_context(ref: WorkspaceRef) -> dict[str, Any] | None:
    if ref.user_context:
        return ref.user_context

    payload = {}
    if ref.rag_user:
        payload["rag_user"] = ref.rag_user
    if ref.voice_id:
        payload["voice_id"] = ref.voice_id
    if ref.device_id:
        payload["device_id"] = ref.device_id
    if not payload:
        return None

    loop = _get_identity_loop()
    session = asyncio.run_coroutine_threadsafe(_ensure_identity_session(), loop).result(timeout=30.0)
    future = asyncio.run_coroutine_threadsafe(
        _http_post_async(
            f"{IDENTITY_SVC_URL}/api/resolve",
            json=payload,
            headers={"X-Internal-Secret": INTERNAL_SECRET},
            timeout=45.0,
            session=session,
        ),
        loop,
    )
    try:
        data = future.result(timeout=60.0)
    except aiohttp.ClientError as exc:
        raise HTTPException(status_code=503, detail=f"Identity service unreachable: {exc}") from exc
    except Exception as exc:  # noqa: BLE001 - surface any resolution failure clearly
        raise HTTPException(status_code=500, detail=f"Identity resolution failed: {exc}") from exc

    if not isinstance(data, dict):
        raise HTTPException(status_code=500, detail="Identity resolution returned invalid response")

    user = str(data.get("user") or "").strip()
    if not user:
        raise HTTPException(status_code=500, detail="Identity resolution did not return a user")
    return data


async def _http_post_async(url: str, **kwargs) -> Any:
    """Async HTTP POST helper using aiohttp.

    If ``session`` is provided it is used directly (so callers can bind the
    request to a specific event loop); otherwise the shared ``get_client()``
    session is used.
    """
    timeout = kwargs.pop("timeout", 30.0)
    session = kwargs.pop("session", None)
    if session is not None:
        resp = await session.post(url, timeout=aiohttp.ClientTimeout(total=timeout), **kwargs)
    else:
        async with get_client() as client:
            resp = await client.post(url, timeout=aiohttp.ClientTimeout(total=timeout), **kwargs)
    if resp.status != 200:
        text = await resp.text()
        raise HTTPException(status_code=resp.status, detail=f"Request failed: {text}")
    return await resp.json()


def resolve_safe_path(base: Path, relative: str, must_exist: bool = True) -> Path:
    """
    Resolves a path against a base directory for user workspaces, or uses it directly
    for absolute paths (system workspaces). Ensures user workspaces don't escape the
    workspace root, but allows absolute paths for system workspaces.
    """
    try:
        # If path is already absolute, use it directly (system workspaces)
        if os.path.isabs(relative):
            target = Path(relative).resolve()
            if must_exist and not target.exists():
                raise HTTPException(status_code=404, detail=f"Path not found: {relative}")
            return target

        # User workspace: join with base and check for path escapes
        target = (base / relative).resolve()

        # Ensure the resolved path is within the base directory
        try:
            target.relative_to(base)
        except (ValueError, RuntimeError):
            log.warning(f"SECURITY ALERT: Path traversal attempt blocked! Base='{base}', Relative='{relative}'")
            raise HTTPException(status_code=403, detail="Forbidden: Path traversal detected") from None

        # Existence check if required
        if must_exist and not target.exists():
            raise HTTPException(status_code=404, detail=f"Path not found: {relative}")

        return target
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Unexpected error resolving path: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to resolve path: {relative}") from None


def _strip_workspace_path_prefix(relative_path: str, workspace: dict[str, Any]) -> str:
    """Normalize a model-supplied relative path.

    Models (especially smaller ones) sometimes copy ``local_path`` /
    ``resolved_path`` from workspace metadata and use it as a ``relative_path``
    (e.g. ``users/default/<id>/main.py`` or even just ``users/default/<id>``).
    Stripping that redundant prefix resolves the file at the correct location
    instead of producing a doubled 'Path not found' error.
    """
    if not relative_path:
        return relative_path
    norm = os.path.normpath(str(relative_path)).replace("\\", "/").strip()
    if norm in (".", "/"):
        return ""
    for key in ("resolved_path", "local_path"):
        base = workspace.get(key)
        if not base:
            continue
        base_norm = os.path.normpath(str(base)).replace("\\", "/").strip()
        if not base_norm or base_norm in (".", "/"):
            continue
        if norm == base_norm:
            return ""
        if norm.startswith(base_norm + "/"):
            return norm[len(base_norm) + 1:].strip("/")
    return relative_path


WORKSPACE_CLONING_IN_PROGRESS: set[str] = set()
WORKSPACE_CLONING_MUTEX = threading.Lock()

def _ensure_workspace_recovered(workspace: dict[str, Any]) -> None:
    ws_id = workspace.get("id")
    if not ws_id:
        return

    repo_url = str(workspace.get("repo_url") or "").strip()
    if not repo_url:
        return

    resolved_path = Path(workspace["resolved_path"])
    if (resolved_path / ".git").is_dir():
        return

    with WORKSPACE_CLONING_MUTEX:
        if ws_id in WORKSPACE_CLONING_IN_PROGRESS:
            lock = get_workspace_lock(ws_id)
            lock.acquire()
            lock.release()
            return
        WORKSPACE_CLONING_IN_PROGRESS.add(ws_id)

    try:
        lock = get_workspace_lock(ws_id)
        with lock:
            if (resolved_path / ".git").is_dir():
                return

            log.warning(f"Git repository missing on disk for workspace '{ws_id}' at {resolved_path}. Re-cloning for auto-recovery.")
            try:
                branch_name = str(workspace.get("default_branch") or "main").strip()
                clone_args = ["git", "clone", "--single-branch"]
                if branch_name:
                    clone_args.extend(["--branch", branch_name])
                clone_args.extend([repo_url, str(resolved_path)])

                if any(resolved_path.iterdir()):
                    backup_path = Path(str(resolved_path) + f"-backup-{int(time.time_ns())}")
                    log.info(f"Workspace path not empty during recovery. Backing up to {backup_path}")
                    resolved_path.rename(backup_path)
                    resolved_path.mkdir(parents=True, exist_ok=True)

                identity = workspace.get("resolved_identity") or {}
                _run_git_with_optional_askpass(
                    resolved_path.parent,
                    clone_args,
                    identity=identity,
                    remote_url=repo_url,
                    timeout_seconds=60
                )
                log.info(f"Auto-recovery successful: cloned {repo_url} into {resolved_path}")
            except Exception as recovery_err:
                log.error(f"Auto-recovery failed to clone repository {repo_url}: {recovery_err}")
                raise HTTPException(
                    status_code=500,
                    detail=f"Workspace repository was missing and auto-recovery clone failed: {recovery_err}"
                ) from recovery_err
    finally:
        with WORKSPACE_CLONING_MUTEX:
            WORKSPACE_CLONING_IN_PROGRESS.discard(ws_id)


def _resolve_workspace(ref: WorkspaceRef, check_recovery: bool = False) -> dict[str, Any]:
    registry = _load_registry()
    identity = _resolve_identity_context(ref)
    resolved_user = identity["user"] if identity else None
    is_admin = bool(identity and identity.get("is_admin"))
    match = None

    if ref.workspace_id:
        match = next((item for item in registry if item.get("id") == ref.workspace_id), None)
    elif ref.local_path:
        match = next(
            (item for item in registry if item.get("local_path") == ref.local_path),
            None
        )
        if match is None:
            match = {"id": "ad_hoc", "display_name": "Ad Hoc Workspace"}
    else:
        raise HTTPException(status_code=400, detail="workspace_id or local_path is required")

    if match is None:
        raise HTTPException(status_code=404, detail="Workspace not found")

    access_policy = _workspace_access_policy(match)
    if access_policy == "authenticated" and not resolved_user:
        raise HTTPException(status_code=400, detail="User context is required for this workspace")
    if access_policy == "admin_only" and not is_admin:
        raise HTTPException(status_code=403, detail=f"Workspace '{match.get('id')}' requires an admin identity")
    owner_user = str(match.get("owner_user") or "").strip()

    is_default_shared = owner_user == "default"
    if owner_user and not is_admin and owner_user != resolved_user and not is_default_shared:
        raise HTTPException(status_code=404, detail="Workspace not found")

    effective_path = str(match.get("local_path", ""))
    resolved_path = resolve_safe_path(get_workspace_root(), effective_path, must_exist=False)
    try:
        os.makedirs(str(resolved_path), exist_ok=True)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to create workspace directory {resolved_path}: {exc}") from None
    if not resolved_path.is_dir():
        raise HTTPException(status_code=400, detail=f"Workspace path is not a directory: {effective_path}")

    workspace: dict[str, Any] = dict(match)
    workspace["resolved_path"] = str(resolved_path)
    workspace["scope"] = str(workspace.get("scope") or "user")
    workspace["access_policy"] = access_policy

    if is_default_shared and not is_admin and owner_user != resolved_user:
        workspace["capabilities"] = ["read", "git_status"]
    else:
        workspace["capabilities"] = _workspace_capabilities(workspace)

    if resolved_user:
        workspace["resolved_user"] = resolved_user
    if identity:
        workspace["resolved_identity"] = identity
    workspace["is_new"] = not bool(workspace.get("repo_url"))
    workspace["has_repo"] = bool(workspace.get("repo_url"))
    workspace["needs_repo"] = workspace["is_new"]
    if check_recovery:
        _ensure_workspace_recovered(workspace)
    return workspace


def _resolve_workspace_for_bootstrap(ref: WorkspaceBootstrapRequest) -> dict[str, Any]:
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
            match = {"id": "ad_hoc", "display_name": "Ad Hoc Workspace"}
    else:
        raise HTTPException(status_code=400, detail="workspace_id or local_path is required")

    if match is None:
        if not ref.create_if_missing:
            raise HTTPException(status_code=404, detail="Workspace not found")
        if not resolved_user:
            raise HTTPException(status_code=400, detail="User context is required to create a workspace")
        repo_url = str(ref.repo_url or "").strip()
        if not repo_url:
            raise HTTPException(status_code=400, detail="repo_url is required to create a workspace")

        # Determine scope: system workspaces require admin
        scope = "system" if is_admin else "user"

        workspace_id = _derive_workspace_id(ref.workspace_id, resolved_user, repo_url)

        # Reserved name validation
        slug = _normalize_workspace_slug(workspace_id)
        if slug in RESERVED_WORKSPACE_NAMES:
            raise HTTPException(status_code=400, detail=f"Workspace ID '{workspace_id}' is reserved. Cannot use: {', '.join(sorted(RESERVED_WORKSPACE_NAMES))}")

        owner_user = resolved_user if scope == "user" else "system"
        local_path = str(ref.local_path or _derive_workspace_container_path(workspace_id, scope, owner_user)).strip()
        match = {
            "id": workspace_id,
            "display_name": str(ref.display_name or workspace_id).strip(),
            "access_policy": "authenticated",
            "local_path": local_path,
            "repo_url": repo_url,
            "git_remote": str(ref.remote or "origin").strip(),
            "default_branch": str(ref.branch or "main").strip(),
            "sync_mode": "local_git_authoritative",
            "scope": scope,
            "owner_user": owner_user,
        }
        with Session(engine) as session:
            existing = session.get(Workspace, workspace_id)
            if existing:
                raise HTTPException(status_code=409, detail=f"Workspace {workspace_id} already exists")
            session.add(Workspace(
                id=workspace_id,
                display_name=str(match.get("display_name", workspace_id)),
                access_policy=str(match.get("access_policy", "authenticated")),
                local_path=match.get("local_path"),
                scope=match.get("scope", "user"),
                owner_user=match.get("owner_user"),
            ))
            session.commit()

    access_policy = _workspace_access_policy(match)
    if access_policy == "authenticated" and not resolved_user:
        raise HTTPException(status_code=400, detail="User context is required for this workspace")
    if access_policy == "admin_only" and not is_admin:
        raise HTTPException(status_code=403, detail=f"Workspace '{match.get('id')}' requires an admin identity")
    owner_user = str(match.get("owner_user") or "").strip()
    if owner_user and not is_admin and owner_user != resolved_user:
        raise HTTPException(status_code=404, detail="Workspace not found")

    # local_path is the only user-facing path field
    effective_path = str(match.get("local_path", ""))
    resolved_path = resolve_safe_path(get_workspace_root(), effective_path, must_exist=False)
    workspace = dict(match)
    workspace["resolved_path"] = str(resolved_path)
    workspace["exists"] = resolved_path.exists()
    workspace["scope"] = str(workspace.get("scope") or "user")
    workspace["access_policy"] = access_policy
    workspace["capabilities"] = _workspace_capabilities(workspace)
    if resolved_user:
        workspace["resolved_user"] = resolved_user
    if identity:
        workspace["resolved_identity"] = identity
    workspace["is_new"] = not bool(workspace.get("repo_url"))
    workspace["has_repo"] = bool(workspace.get("repo_url"))
    workspace["needs_repo"] = workspace["is_new"]
    return workspace


def _normalize_workspace_slug(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", (value or "").strip().lower()).strip("-")
    return cleaned or "workspace"


RESERVED_WORKSPACE_NAMES = {"users", "workspaces", "system"}


def _derive_repo_name(repo_url: str) -> str:
    parsed = urlparse(repo_url)
    path = parsed.path if parsed.scheme else repo_url
    candidate = path.rstrip("/").split("/")[-1].split(":")[-1]
    if candidate.endswith(".git"):
        candidate = candidate[:-4]
    return _normalize_workspace_slug(candidate)


def _derive_workspace_id(requested_id: str | None, resolved_user: str, repo_url: str) -> str:
    if requested_id and requested_id.strip():
        return requested_id.strip()
    return f"{_normalize_workspace_slug(resolved_user)}-{_derive_repo_name(repo_url)}"


def _derive_workspace_container_path(workspace_id: str, scope: str = "user", owner_user: str | None = None) -> str:
    """Derive the container mount path based on scope.

    System workspaces: system/{workspace_id}
    User workspaces: users/{owner_user}/{workspace_id}
    """
    ws_slug = _normalize_workspace_slug(workspace_id)
    if scope == "system":
        return f"system/{ws_slug}"
    user_slug = _normalize_workspace_slug(owner_user or "default")
    return f"users/{user_slug}/{ws_slug}"


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


# Removed redundant _safe_file_path and _safe_target_path in favor of resolve_safe_path


def _list_workspace_entries(
    workspace_path: Path,
    relative_path: str,
    recursive: bool,
    max_depth: int,
    max_entries: int,
    include_dirs: bool,
) -> tuple[Path, list[dict[str, Any]], bool]:
    root = resolve_safe_path(workspace_path, relative_path)
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
            except (ValueError, RuntimeError):
                raise HTTPException(status_code=403, detail="Forbidden: Path traversal") from None

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
    env_overrides: dict[str, str] | None = None,
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


def _run_lint_for_file(workspace_path: Path, relative_path: str) -> dict[str, Any]:
    target = resolve_safe_path(workspace_path, relative_path)
    if not target.is_file():
        raise HTTPException(status_code=400, detail=f"Path is not a file: {relative_path}")

    ext = target.suffix.lower()
    results: list[dict[str, Any]] = []
    passed = True

    def _lint(cmd: list[str]) -> tuple[int, str]:
        result = _run_command(workspace_path, cmd, timeout_seconds=30)
        output = result["stdout"].strip() or result["stderr"].strip()
        return result["returncode"], output

    if ext == ".py":
        rc, output = _lint(["black", "--check", "--diff", str(target)])
        results.append({"tool": "black", "returncode": rc, "output": output})
        passed = passed and rc == 0
        rc, output = _lint(["flake8", "--max-line-length=120", str(target)])
        results.append({"tool": "flake8", "returncode": rc, "output": output})
        passed = passed and rc == 0
    elif ext in {".js", ".ts", ".jsx", ".tsx", ".mjs"}:
        rc, output = _lint(["eslint", str(target)])
        results.append({"tool": "eslint", "returncode": rc, "output": output})
        passed = passed and rc == 0
    elif ext == ".json":
        rc, output = _lint(["python3", "-m", "json.tool", str(target)])
        results.append({"tool": "json.tool", "returncode": rc, "output": output})
        passed = passed and rc == 0
    elif ext in {".yaml", ".yml"}:
        rc, output = _lint(["yamllint", "-d", "relaxed", str(target)])
        results.append({"tool": "yamllint", "returncode": rc, "output": output})
        passed = passed and rc == 0
    else:
        results.append({"tool": "none", "returncode": 0, "output": f"No linter configured for {ext or '[no extension]'}"})

    return {
        "path": relative_path,
        "passed": passed,
        "results": results,
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


def _derive_git_author(identity: dict[str, Any], author_name: str | None, author_email: str | None) -> tuple[str, str]:
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


def _protected_branch_patterns(identity: dict[str, Any]) -> list[str]:
    raw_patterns = identity.get("forbidden_branches", DEFAULT_PROTECTED_BRANCH_PATTERNS)
    if isinstance(raw_patterns, str):
        patterns = [part.strip() for part in raw_patterns.split(",") if part.strip()]
    elif isinstance(raw_patterns, list):
        patterns = [str(part).strip() for part in raw_patterns if str(part).strip()]
    else:
        patterns = []
    return patterns or DEFAULT_PROTECTED_BRANCH_PATTERNS


def _is_protected_branch(branch_name: str, identity: dict[str, Any]) -> bool:
    branch = str(branch_name or "").strip()
    if not branch:
        return False
    return any(fnmatch.fnmatch(branch, pattern) for pattern in _protected_branch_patterns(identity))


def _slugify_branch_component(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", str(value or "").strip().lower()).strip("-./")
    return cleaned or "change"


def _build_review_metadata(
    workspace: dict[str, Any],
    relative_path: str,
    commit_message: str,
    branch_name: str,
    commit_result: dict[str, Any],
    lint_results: list[dict[str, Any]],
    pytest_result: dict[str, Any] | None,
    push_result: dict[str, Any] | None,
) -> dict[str, Any]:
    base_branch = str(workspace.get("default_branch") or "main").strip() or "main"
    changed_files: list[str] = [relative_path] + [
        str(item.get("path"))
        for item in lint_results
        if item.get("path") and item.get("path") != relative_path
    ]
    unique_changed_files = list(dict.fromkeys(changed_files))
    lint_summary = [
        {
            "path": item.get("path"),
            "passed": bool(item.get("passed")),
            "tools": [result.get("tool") for result in item.get("results", [])],
        }
        for item in lint_results
    ]
    pytest_summary = None
    if pytest_result:
        pytest_summary = {
            "passed": bool(pytest_result.get("passed")),
            "targets": pytest_result.get("command", [])[3:],
        }

    summary_lines = [
        f"- Workspace: {workspace.get('id')}",
        f"- Branch: {branch_name}",
        f"- Commit: {commit_result.get('commit') or 'pending'}",
        f"- Changed files: {', '.join(unique_changed_files) if unique_changed_files else 'none'}",
        "- Verification:",
    ]
    for item in lint_summary:
        tools_list: list[str] = item.get("tools") or []  # type: ignore[assignment]
        tools = ", ".join(tools_list) or "none"
        summary_lines.append(
            f"  - Lint {item['path']}: {'PASS' if item['passed'] else 'FAIL'} via {tools}"
        )
    if pytest_summary:
        targets_list: list[str] = pytest_summary.get("targets") or []  # type: ignore[assignment]
        targets = ", ".join(targets_list) or "(full suite)"
        summary_lines.append(
            f"  - Pytest: {'PASS' if pytest_summary['passed'] else 'FAIL'} on {targets}"
        )
    else:
        summary_lines.append("  - Pytest: not run")
    summary_lines.extend(
        [
            "",
            "## Reviewer Checklist",
            "- Confirm the branch targets the correct protected base branch.",
            "- Review the diff for unintended side effects.",
            "- Confirm lint and test coverage are adequate for the touched files.",
        ]
    )

    return {
        "title": commit_message.strip() or (commit_result.get("commit") or "Raven change set"),
        "head": branch_name,
        "base": base_branch,
        "draft": False,
        "summary": {
            "workspace_id": workspace.get("id"),
            "branch": branch_name,
            "base_branch": base_branch,
            "commit": commit_result.get("commit"),
            "changed_files": unique_changed_files,
            "lint": lint_summary,
            "pytest": pytest_summary,
            "pushed": bool(push_result),
            "remote": push_result.get("remote") if push_result else None,
        },
        "body": "\n".join(summary_lines),
    }


def _git_remote_url(workspace_path: Path, remote_name: str) -> str:
    log.info(f"Resolving git remote URL for '{remote_name}' in {workspace_path}")
    result = _run_command(workspace_path, ["git", "config", "--get", f"remote.{remote_name}.url"])
    if result["returncode"] != 0:
        log.warning(f"Git remote lookup failed for '{remote_name}': {result['stderr']}")
        raise HTTPException(status_code=400, detail=f"Git remote '{remote_name}' is not configured")
    remote_url = result["stdout"].strip()
    if not remote_url:
        raise HTTPException(status_code=400, detail=f"Git remote '{remote_name}' is empty")
    return remote_url


def _git_webhook_pull_remote(remote_url: str, remote_name: str) -> str:
    value = str(remote_url or "").strip()
    if not value:
        return remote_name

    ssh_match = re.match(r"^git@([^:]+):(.+)$", value)
    if ssh_match:
        host, path = ssh_match.groups()
        return f"https://{host}/{path}"

    parsed = urlparse(value)
    if parsed.scheme == "ssh" and parsed.hostname and parsed.username == "git":
        path = (parsed.path or "").lstrip("/")
        if path:
            return f"https://{parsed.hostname}/{path}"

    return value


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


async def _storage_post(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        async with get_client() as client:
            resp = await client.post(
                f"{STORAGE_SVC_URL}{path}" if not path.startswith("http") else path,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=30.0),
            )
            if resp.status != 200:
                text = await resp.text()
                raise HTTPException(status_code=resp.status, detail=f"Storage request failed: {text}")
            data = await resp.json()
    except aiohttp.ClientError as exc:
        raise HTTPException(status_code=503, detail=f"Storage service unreachable: {exc}") from exc
    if data.get("status") != "SUCCESS":
        raise HTTPException(status_code=500, detail=f"Storage request failed: {data}")
    return data


def _git_https_credentials(identity: dict[str, Any], remote_url: str) -> tuple[str, str] | None:
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
    """Host-based git runner with credential injection.

    Used for SYSTEM-level git operations (workspace auto-recovery clones and
    provider sync), which run in this runtime container rather than the
    per-workspace sandbox. User-facing git (commit/push/...) is served from
    git_ops.py and runs inside the sandbox container.
    """
    credentials = _git_https_credentials(identity, remote_url)
    if not credentials:
        return _run_command(workspace_path, args, timeout_seconds=timeout_seconds)

    username, password = credentials
    with tempfile.NamedTemporaryFile("w", delete=False, prefix="sharedllm-git-askpass-", suffix=".sh") as askpass_file:
        askpass_path = askpass_file.name
        askpass_file.write("#!/bin/sh\n")
        askpass_file.write('case "$1" in\n')
        askpass_file.write('  *Username*) printf \'%s\\n\' "$SHAREDLLM_GIT_USERNAME" ;;\n')
        askpass_file.write('  *) printf \'%s\\n\' "$SHAREDLLM_GIT_PASSWORD" ;;\n')
        askpass_file.write("esac\n")
        askpass_file.flush()
    os.chmod(askpass_path, 0o700)
    try:
        return _run_command(
            workspace_path,
            args,
            timeout_seconds=timeout_seconds,
            env_overrides={
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_ASKPASS": askpass_path,
                "SHAREDLLM_GIT_USERNAME": username,
                "SHAREDLLM_GIT_PASSWORD": password,
            },
        )
    finally:
        with suppress(FileNotFoundError):
            os.unlink(askpass_path)


START_TIME = time.time()

@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "workspace_runtime",
        "workspace_root": str(get_workspace_root()),
        "git_sha": os.getenv("GIT_SHA", "unknown"),
        "start_time": START_TIME
    }


@app.get("/workspaces")
def list_workspaces(
    rag_user: str | None = None,
    voice_id: str | None = None,
    device_id: str | None = None,
    x_internal_secret: str | None = Header(default=None),
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
        owner_user = str(item.get("owner_user") or "").strip()
        is_default_shared = owner_user == "default"
        # Show workspace if: no owner, user is admin, user is owner, OR workspace is default-shared
        if owner_user and not is_admin and owner_user != resolved_user and not is_default_shared:
            continue
        if access_policy == "admin_only" and resolved_user and not is_admin:
            continue
        if access_policy in {"authenticated", "admin_only"} and not resolved_user:
            item["available"] = False
            item["resolved_path"] = None
            item["requires_user_context"] = True
            item["access_policy"] = access_policy
            item["is_new"] = not bool(item.get("repo_url"))
            item["has_repo"] = bool(item.get("repo_url"))
            item["needs_repo"] = item["is_new"]
            items.append(item)
            continue
        try:
            # local_path is the only user-facing path field (relative for users, absolute for system)
            effective_path = str(entry.get("local_path", "."))
            item["resolved_path"] = str(resolve_safe_path(get_workspace_root(), effective_path))
            item["available"] = True
        except HTTPException:
            item["resolved_path"] = None
            item["available"] = False
        item["scope"] = str(item.get("scope") or "user")
        item["access_policy"] = access_policy
        item["is_new"] = not bool(item.get("repo_url"))
        item["has_repo"] = bool(item.get("repo_url"))
        item["needs_repo"] = item["is_new"]
        # Default-shared workspaces get restricted capabilities for non-owners
        if is_default_shared and not is_admin and owner_user != resolved_user:
            item["capabilities"] = ["read", "git_status"]
        else:
            item["capabilities"] = _workspace_capabilities(item)
        if resolved_user:
            item["resolved_user"] = resolved_user
        if identity:
            item["resolved_identity"] = identity
        items.append(item)
    return {"status": "SUCCESS", "workspaces": items}


@app.post("/workspace/resolve")
def resolve_workspace(req: WorkspaceRef, x_internal_secret: str | None = Header(default=None)):
    _require_internal_secret(x_internal_secret)
    workspace = _resolve_workspace(req)
    # Trusted internal endpoint: attach the DECRYPTED per-workspace env/secret
    # map so the execution layer can inject it into the sandbox. Public list
    # endpoints only receive masked key names (see _workspace_to_dict).
    _env_enc = workspace.pop("env_enc", None)
    try:
        _env = json.loads(decrypt(_env_enc) or "{}") if _env_enc else {}
    except Exception:
        _env = {}
    workspace["env"] = _env if isinstance(_env, dict) else {}
    return {"status": "SUCCESS", "workspace": workspace}


async def _create_github_repo(
    identity: dict[str, Any],
    repo_name: str,
    private: bool = True,
    description: str | None = None,
) -> str:
    """Create a new GitHub repository via the REST API using the resolved user token.

    Returns the HTTPS clone URL. Raises HTTPException with a clear message on any
    auth/creation failure so callers can surface it to the agent/user.
    """
    token = str(identity.get("github_token") or "").strip()
    if not token:
        raise HTTPException(
            status_code=400,
            detail="Cannot create GitHub repository: no github_token present in the identity context.",
        )
    github_url = str(identity.get("github_url") or "https://github.com").rstrip("/")
    api_base = "https://api.github.com" if "github.com" in github_url else f"{github_url}/api/v3"

    payload: dict[str, Any] = {"name": repo_name, "private": bool(private)}
    if description:
        payload["description"] = description
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    async with get_client() as client, client.post(f"{api_base}/user/repos", json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=60.0)) as resp:
        if resp.status not in (200, 201):
            text = await resp.text()
            raise HTTPException(
                status_code=400,
                detail=f"GitHub repository creation failed ({resp.status}): {text[:400]}",
            )
        data = await resp.json()
    clone_url = data.get("clone_url") or data.get("ssh_url")
    if not clone_url:
        raise HTTPException(status_code=500, detail="GitHub repository created but no clone URL was returned")
    return clone_url


@app.post("/workspaces/bootstrap")
def bootstrap_workspace(req: WorkspaceBootstrapRequest, x_internal_secret: str | None = Header(default=None)):
    _require_internal_secret(x_internal_secret)
    workspace = _resolve_workspace_for_bootstrap(req)
    _require_workspace_capability(workspace, "git_write")

    # Derive target path from local_path (relative for user workspaces, absolute for system)
    effective_path = str(workspace.get("local_path", "."))
    if effective_path == ".":
        repo_url = str(req.repo_url or workspace.get("repo_url") or "").strip()
        if repo_url:
            scope = workspace.get("scope", "user")
            owner_user = workspace.get("owner_user")
            effective_path = _derive_workspace_container_path(workspace["id"], scope, owner_user)
            workspace["local_path"] = effective_path
            resolved_path = resolve_safe_path(get_workspace_root(), effective_path, must_exist=False)
            workspace["resolved_path"] = str(resolved_path)
            with Session(engine) as session:
                ws = session.exec(select(Workspace).where(Workspace.id == workspace["id"])).first()
                if ws:
                    ws.local_path = effective_path
                    session.add(ws)
                    session.commit()
            target_path = Path(workspace["resolved_path"])
        else:
            target_path = Path(workspace["resolved_path"])
    else:
        target_path = Path(workspace["resolved_path"])

    parent_path = target_path.parent
    parent_path.mkdir(parents=True, exist_ok=True)

    if target_path.exists():
        if not target_path.is_dir():
            raise HTTPException(status_code=400, detail=f"Workspace path is not a directory: {target_path}")
        if (target_path / ".git").is_dir():
            remote_name = (req.remote or workspace.get("git_remote") or "origin").strip()
            remote_url = None
            try:
                remote_url = _git_remote_url(target_path, remote_name)
            except HTTPException:
                remote_url = None
            workspace["exists"] = True
            workspace["available"] = True
            return {
                "status": "SUCCESS",
                "workspace": workspace,
                "bootstrapped": False,
                "already_present": True,
                "remote": remote_name,
                "repo_url": remote_url,
            }
        if any(target_path.iterdir()):
            backup_path = Path(str(target_path) + f"-backup-{int(time.time_ns())}")
            log.warning(f"Git repo is missing (.git folder not found) in non-empty workspace {target_path}. Backing up to {backup_path} and recreating/cloning fresh.")
            try:
                target_path.rename(backup_path)
            except Exception as rename_err:
                raise HTTPException(
                    status_code=500,
                    detail=f"Git repo is missing, and failed to back up existing directory to {backup_path}: {rename_err}"
                ) from rename_err

    repo_url = str(req.repo_url or workspace.get("repo_url") or "").strip()
    branch_name = str(req.branch or workspace.get("default_branch") or "main").strip()
    remote_name = (req.remote or workspace.get("git_remote") or "origin").strip()
    identity = workspace.get("resolved_identity") or {}

    created_repo = False
    if not repo_url and req.create_repo:
        repo_name = _normalize_workspace_slug(
            req.repo_name or _derive_repo_name(workspace.get("id", "")) or str(workspace.get("id"))
        )
        if not repo_name:
            raise HTTPException(status_code=400, detail="Workspace bootstrap requires a repo_url or a repo_name to create one")
        try:
            repo_url = asyncio.run(_create_github_repo(identity, repo_name, req.repo_private, req.repo_description))
        except HTTPException:
            raise
        except Exception as e:  # pragma: no cover - network/parse edge cases
            raise HTTPException(status_code=400, detail=f"GitHub repository creation failed: {e}") from None
        created_repo = True
        workspace["repo_url"] = repo_url
        with Session(engine) as session:
            ws = session.get(Workspace, workspace["id"])
            if ws:
                ws.repo_url = repo_url
                session.add(ws)
                session.commit()

    if not repo_url:
        raise HTTPException(status_code=400, detail="Workspace bootstrap requires a repo_url (or create_repo=true)")

    workspace["created_repo"] = created_repo
    workspace["is_new"] = not bool(workspace.get("repo_url"))

    clone_args = ["git", "clone"]
    if not created_repo:
        # Existing repos: clone only the requested branch. A freshly created (empty)
        # repo has no branches yet, so clone it plainly and let the agent push to create one.
        clone_args.append("--single-branch")
        if branch_name:
            clone_args.extend(["--branch", branch_name])
    clone_args.extend([repo_url, str(target_path)])

    result = _run_git_with_optional_askpass(
        parent_path,
        clone_args,
        identity=identity,
        remote_url=repo_url,
        timeout_seconds=180,
    )
    if result["returncode"] != 0:
        raise HTTPException(status_code=400, detail=result["stderr"].strip() or result["stdout"].strip() or "git clone failed")

    if remote_name and remote_name != "origin":
        rename_result = _run_command(target_path, ["git", "remote", "rename", "origin", remote_name])
        if rename_result["returncode"] != 0:
            raise HTTPException(
                status_code=400,
                detail=rename_result["stderr"].strip() or rename_result["stdout"].strip() or "git remote rename failed",
            )

    workspace["exists"] = True
    workspace["available"] = True
    return {
        "status": "SUCCESS",
        "workspace": workspace,
        "bootstrapped": True,
        "already_present": False,
        "created_repo": created_repo,
        "remote": remote_name,
        "repo_url": repo_url,
        "branch": branch_name,
        "stdout": result["stdout"],
        "stderr": result["stderr"],
    }


@app.post("/workspaces")
def create_workspace(ws: Workspace, x_internal_secret: str | None = Header(default=None)):
    _require_internal_secret(x_internal_secret)

    # Reserved name validation
    slug = _normalize_workspace_slug(ws.id)
    if slug in RESERVED_WORKSPACE_NAMES:
        raise HTTPException(status_code=400, detail=f"Workspace ID '{ws.id}' is reserved. Cannot use: {', '.join(sorted(RESERVED_WORKSPACE_NAMES))}")

    # Derive local_path if not provided (required by the NOT NULL column)
    if not ws.local_path:
        ws.local_path = _derive_workspace_container_path(ws.id, ws.scope, ws.owner_user)

    with Session(engine) as session:
        existing = session.get(Workspace, ws.id)
        if existing:
            # Idempotent acquire: a workspace that already exists is treated as
            # "acquired" rather than an error, so callers (e.g. Raven's
            # WorkspaceCreateRequest — the sandbox it must work out of, like a
            # chroot) can adopt it as their working environment on re-runs
            # instead of failing with "already exists". Re-materialize the
            # backing directory in case it was removed, then return the record.
            ws_path = existing.local_path or _derive_workspace_container_path(
                existing.id, existing.scope, existing.owner_user
            )
            resolved_path = resolve_safe_path(get_workspace_root(), ws_path, must_exist=False)
            with suppress(OSError):
                os.makedirs(str(resolved_path), exist_ok=True)
            return {
                "status": "SUCCESS",
                "workspace": _workspace_to_dict(existing),
                "already_existed": True,
                "message": f"Workspace {existing.id} already existed; adopted as working sandbox.",
            }
        _store_workspace_secret_fields(ws)
        # Stamp creation time in UTC (unambiguous at rest). It is converted to
        # the configured Config DB timezone at serialization time so the
        # "Created" label renders in the operator's local time.
        ws.created_at = datetime.now(timezone.utc)
        session.add(ws)
        session.commit()
        session.refresh(ws)
        # Materialize the full workspace directory path on disk immediately so the
        # agent can use it. We do this at creation time (rather than lazily at write
        # time) so that filesystem/permission errors surface here — before the agent
        # starts writing files and fails mid-mission. A missing backing directory is
        # also what previously pushed the agent into the Default Workspace.
        resolved_path = resolve_safe_path(get_workspace_root(), ws.local_path, must_exist=False)
        try:
            os.makedirs(str(resolved_path), exist_ok=True)
        except OSError as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to create workspace directory {resolved_path}: {exc}",
            ) from None
        if not resolved_path.is_dir():
            raise HTTPException(
                status_code=400,
                detail=f"Workspace path is not a directory: {ws.local_path}",
            )
        # Self-verify the workspace is now resolvable from the registry.
        registry = _load_registry()
        if not any(item.get("id") == ws.id for item in registry):
            raise HTTPException(
                status_code=500,
                detail=f"Workspace creation verification failed: {ws.id} not found in registry after commit.",
            )
        return {"status": "SUCCESS", "workspace": _workspace_to_dict(ws)}


@app.patch("/workspaces/{workspace_id}")
def update_workspace(workspace_id: str, updates: dict, x_internal_secret: str | None = Header(default=None)):
    _require_internal_secret(x_internal_secret)
    with Session(engine) as session:
        ws = session.get(Workspace, workspace_id)
        if not ws:
            raise HTTPException(status_code=404, detail="Workspace not found")

        for key, value in updates.items():
            if hasattr(ws, key):
                setattr(ws, key, value)

        # Merge per-workspace env/secret overrides. `env` (dict) adds/overwrites
        # keys; `env_delete` (list of keys) removes them. Keys not mentioned are
        # preserved, so a client can update one secret without resending all
        # others (their (encrypted) values never leave the server). Re-encrypted
        # at rest.
        if "env" in updates or "env_delete" in updates:
            _current_env: dict = {}
            if ws.env_enc:
                try:
                    _current_env = json.loads(decrypt(ws.env_enc) or "{}") or {}
                except Exception:
                    _current_env = {}
            _delete = updates.get("env_delete") or []
            if isinstance(_delete, list):
                for _k in _delete:
                    _current_env.pop(str(_k), None)
            _add = updates.get("env")
            if isinstance(_add, dict):
                for _k, _v in _add.items():
                    _k = str(_k)
                    if _v is None:
                        _current_env.pop(_k, None)
                    else:
                        _current_env[_k] = str(_v)
            elif isinstance(_add, str) and _add.strip():
                try:
                    _parsed = json.loads(_add)
                    if isinstance(_parsed, dict):
                        for _k, _v in _parsed.items():
                            _current_env[str(_k)] = str(_v)
                except Exception:
                    pass
            ws.env_enc = encrypt(json.dumps(_current_env)) if _current_env else None

        _store_workspace_secret_fields(ws, updates)

        session.add(ws)
        session.commit()
        session.refresh(ws)
        return {"status": "SUCCESS", "workspace": _workspace_to_dict(ws)}


@app.delete("/workspaces/{workspace_id}")
def delete_workspace(workspace_id: str, x_internal_secret: str | None = Header(default=None)):
    _require_internal_secret(x_internal_secret)
    with Session(engine) as session:
        ws = session.get(Workspace, workspace_id)
        if not ws:
            raise HTTPException(status_code=404, detail="Workspace not found")
        session.delete(ws)
        session.commit()
        return {"status": "SUCCESS", "message": f"Workspace {workspace_id} deleted"}


@app.post("/files/read")
def read_file(req: FileReadRequest, x_internal_secret: str | None = Header(default=None)):
    _require_internal_secret(x_internal_secret)
    workspace = _resolve_workspace(req)
    _require_workspace_capability(workspace, "read")
    req.relative_path = _strip_workspace_path_prefix(req.relative_path, workspace)
    workspace_path = Path(workspace["resolved_path"])
    target = resolve_safe_path(workspace_path, req.relative_path)
    if not target.is_file():
        raise HTTPException(status_code=400, detail=f"Path is not a file: {req.relative_path}")
    content = target.read_text(errors="replace")
    return {
        "status": "SUCCESS",
        "workspace": workspace,
        "relative_path": req.relative_path,
        "content": content[: req.max_bytes],
        "truncated": len(content) > req.max_bytes,
    }


@app.post("/files/raw")
def read_file_raw(req: FileRawRequest, x_internal_secret: str | None = Header(default=None)):
    _require_internal_secret(x_internal_secret)
    workspace = _resolve_workspace(req)
    _require_workspace_capability(workspace, "read")
    req.relative_path = _strip_workspace_path_prefix(req.relative_path, workspace)
    workspace_path = Path(workspace["resolved_path"])
    target = resolve_safe_path(workspace_path, req.relative_path)
    if not target.is_file():
        raise HTTPException(status_code=400, detail=f"Path is not a file: {req.relative_path}")
    import mimetypes

    media_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    return FileResponse(path=str(target), media_type=media_type, filename=target.name)


@app.post("/files/list")
def list_files(req: FileListRequest, x_internal_secret: str | None = Header(default=None)):
    _require_internal_secret(x_internal_secret)
    workspace = _resolve_workspace(req)
    _require_workspace_capability(workspace, "read")
    req.relative_path = _strip_workspace_path_prefix(req.relative_path, workspace)
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
def write_file(req: FileWriteRequest, x_internal_secret: str | None = Header(default=None)):
    _require_internal_secret(x_internal_secret)
    workspace = _resolve_workspace(req, check_recovery=True)
    _require_workspace_capability(workspace, "write")
    req.relative_path = _strip_workspace_path_prefix(req.relative_path, workspace)
    workspace_path = Path(workspace["resolved_path"])

    lock = get_workspace_lock(workspace["id"])
    lock.acquire()
    try:
        # Ensure the workspace directory exists before writing. The workspace was
        # resolved as valid for this caller; if the backing directory is missing
        # (e.g. a workspace carried over from a prior service restart), create it
        # here rather than failing the write with a misleading path error.
        try:
            workspace_path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to create workspace directory {workspace_path}: {exc}",
            ) from None
        target = resolve_safe_path(workspace_path, req.relative_path, must_exist=False)

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

        target.parent.mkdir(parents=True, exist_ok=True) if req.create_parents else None

        expected_bytes = None
        try:
            if req.patch:
                # Apply patch
                with tempfile.NamedTemporaryFile("w", suffix=".patch") as patch_file:
                    patch_file.write(req.patch)
                    patch_file.flush()
                    args = ["patch", "-u", str(target), "-i", patch_file.name]
                    result = _run_command(workspace_path, args)
                    if result["returncode"] != 0:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Failed to apply patch to {req.relative_path}: {result['stderr'] or result['stdout']}",
                        )
            elif req.content is not None:
                target.write_text(req.content)
                expected_bytes = req.content.encode("utf-8")
            elif req.content_base64 is not None:
                import base64

                expected_bytes = base64.b64decode(req.content_base64)
                target.write_bytes(expected_bytes)
            else:
                raise HTTPException(status_code=400, detail="Either 'content', 'content_base64', or 'patch' must be provided")

            # Self-verify the mutation actually persisted. Catches ephemeral-volume
            # loss, resolution to the wrong path, or permission issues that would
            # otherwise let the caller believe the file exists when it does not.
            try:
                new_bytes = target.read_bytes()
            except OSError as exc:
                raise HTTPException(
                    status_code=500,
                    detail=f"Write verification failed for {req.relative_path}: unable to read back written file: {exc}",
                ) from None
            if expected_bytes is not None and new_bytes != expected_bytes:
                raise HTTPException(
                    status_code=500,
                    detail=(
                        f"Write verification failed for {req.relative_path}: on-disk content "
                        f"({len(new_bytes)} bytes) does not match requested content "
                        f"({len(expected_bytes)} bytes). The write did not persist correctly."
                    ),
                )
            if not new_bytes:
                raise HTTPException(
                    status_code=500,
                    detail=f"Write verification failed for {req.relative_path}: file is empty after write.",
                )
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Write failed for {req.relative_path}: {exc}") from None
        return {
            "status": "SUCCESS",
            "workspace": workspace,
            "relative_path": req.relative_path,
            "created": created,
            "bytes_written": len(new_bytes),
            "sha256": _sha256_bytes(new_bytes),
            "previous_sha256": previous_sha256,
        }
    finally:
        lock.release()


class FileDeleteRequest(WorkspaceRef):
    relative_path: str


class FileMoveRequest(WorkspaceRef):
    relative_path: str
    new_relative_path: str


@app.post("/files/move")
def move_file(req: FileMoveRequest, x_internal_secret: str | None = Header(default=None)):
    """Rename or move a file/directory within a workspace (atomic rename)."""
    _require_internal_secret(x_internal_secret)
    workspace = _resolve_workspace(req, check_recovery=True)
    _require_workspace_capability(workspace, "write")
    req.relative_path = _strip_workspace_path_prefix(req.relative_path, workspace)
    req.new_relative_path = _strip_workspace_path_prefix(req.new_relative_path, workspace)
    workspace_path = Path(workspace["resolved_path"])

    lock = get_workspace_lock(workspace["id"])
    lock.acquire()
    try:
        src = resolve_safe_path(workspace_path, req.relative_path, must_exist=True)
        dst = resolve_safe_path(workspace_path, req.new_relative_path, must_exist=False)

        if not src.exists():
            raise HTTPException(status_code=404, detail=f"Source not found: {req.relative_path}")
        if dst.exists():
            raise HTTPException(status_code=409, detail=f"Destination already exists: {req.new_relative_path}")

        # Reject moving a directory into its own subtree.
        try:
            dst.resolve().relative_to(src.resolve())
            raise HTTPException(status_code=400, detail="Cannot move a directory into itself")
        except ValueError:
            pass

        dst.parent.mkdir(parents=True, exist_ok=True)
        src.rename(dst)

        if src.exists():
            raise HTTPException(
                status_code=500,
                detail=f"Move verification failed for {req.relative_path}: source still present.",
            )

        return {
            "status": "SUCCESS",
            "workspace": workspace,
            "relative_path": req.relative_path,
            "new_relative_path": req.new_relative_path,
            "message": f"Moved {req.relative_path} -> {req.new_relative_path}",
        }
    finally:
        lock.release()


@app.post("/files/delete")
def delete_file(req: FileDeleteRequest, x_internal_secret: str | None = Header(default=None)):
    _require_internal_secret(x_internal_secret)
    workspace = _resolve_workspace(req, check_recovery=True)
    _require_workspace_capability(workspace, "write")
    req.relative_path = _strip_workspace_path_prefix(req.relative_path, workspace)
    workspace_path = Path(workspace["resolved_path"])

    lock = get_workspace_lock(workspace["id"])
    lock.acquire()
    try:
        target = resolve_safe_path(workspace_path, req.relative_path)

        if not target.exists():
            raise HTTPException(status_code=404, detail=f"File not found: {req.relative_path}")

        if target.is_dir():
            import shutil
            shutil.rmtree(target)
        else:
            target.unlink()

        # Self-verify the deletion actually took effect.
        if target.exists():
            raise HTTPException(
                status_code=500,
                detail=f"Delete verification failed for {req.relative_path}: path still exists after deletion.",
            )

        return {
            "status": "SUCCESS",
            "workspace": workspace,
            "relative_path": req.relative_path,
            "message": f"Deleted {req.relative_path}"
        }
    finally:
        lock.release()


@app.post("/provider/scan")
async def provider_scan(req: ProviderScanRequest, x_internal_secret: str | None = Header(default=None)):
    _require_internal_secret(x_internal_secret)
    workspace = _resolve_workspace(req)
    _require_workspace_capability(workspace, "read")
    identity = workspace.get("resolved_identity") or {}
    provider_kind, provider_settings, provider_path = _workspace_provider_binding(workspace, identity)
    data = await _storage_post(
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
async def provider_sync_file(req: ProviderSyncFileRequest, x_internal_secret: str | None = Header(default=None)):
    _require_internal_secret(x_internal_secret)
    workspace = _resolve_workspace(req)
    _require_workspace_capability(workspace, "write")
    req.relative_path = _strip_workspace_path_prefix(req.relative_path, workspace)
    workspace_path = Path(workspace["resolved_path"])
    local_file = resolve_safe_path(workspace_path, req.relative_path)
    if not local_file.is_file():
        raise HTTPException(status_code=400, detail=f"Path is not a file: {req.relative_path}")
    identity = workspace.get("resolved_identity") or {}
    provider_kind, provider_settings, provider_root = _workspace_provider_binding(workspace, identity)
    provider_path = _provider_child_path(provider_root, req.relative_path)

    # Try text first, fallback to base64 for binary
    try:
        content = local_file.read_text()
        payload = {
            "provider": {"kind": provider_kind, "settings": provider_settings},
            "path": provider_path,
            "content": content,
            "create_parents": req.create_parents,
            "verify": req.verify,
        }
    except UnicodeDecodeError:
        import base64
        content_bytes = local_file.read_bytes()
        content_b64 = base64.b64encode(content_bytes).decode("utf-8")
        payload = {
            "provider": {"kind": provider_kind, "settings": provider_settings},
            "path": provider_path,
            "content_b64": content_b64,
            "create_parents": req.create_parents,
            "verify": req.verify,
        }

    data = await _storage_post(f"{STORAGE_SVC_URL}/providers/write", payload)
    return {
        "status": "SUCCESS",
        "workspace": workspace,
        "relative_path": req.relative_path,
        "provider_kind": provider_kind,
        "provider_path": provider_path,
        "result": data.get("result"),
    }


@app.post("/provider/sync/directory")
async def provider_sync_directory(req: ProviderSyncDirectoryRequest, x_internal_secret: str | None = Header(default=None)):
    _require_internal_secret(x_internal_secret)
    workspace = _resolve_workspace(req)
    _require_workspace_capability(workspace, "write")
    workspace_path = Path(workspace["resolved_path"])

    target_dir = resolve_safe_path(workspace_path, req.relative_path)
    if not target_dir.exists() or not target_dir.is_dir():
        raise HTTPException(status_code=400, detail=f"Path is not a directory: {req.relative_path}")

    identity = workspace.get("resolved_identity") or {}
    provider_kind, provider_settings, provider_root = _workspace_provider_binding(workspace, identity)
    provider_path = _provider_child_path(provider_root, req.relative_path)

    # In this SOA, storage service might not have access to the same mount.
    # If storage and workspace_runtime share /workspace, we can use /providers/mirror.
    # Otherwise we'd have to stream files.
    # Assuming they share /workspace mount as per typical dev setups or we can use the absolute path.

    payload = {
        "provider": {"kind": provider_kind, "settings": provider_settings},
        "remote_path": provider_path,
        "local_path": str(target_dir),
    }

    data = await _storage_post(f"{STORAGE_SVC_URL}/providers/mirror", payload)
    return {
        "status": "SUCCESS",
        "workspace": workspace,
        "relative_path": req.relative_path,
        "provider_kind": provider_kind,
        "provider_path": provider_path,
        "result": data.get("result"),
    }




@app.post("/workflow/write-sync-commit")
async def workflow_write_sync_commit(req: WorkflowWriteSyncCommitRequest, x_internal_secret: str | None = Header(default=None)) -> dict:
    _require_internal_secret(x_internal_secret)
    workspace = _resolve_workspace(req, check_recovery=True)
    _require_workspace_capability(workspace, "write")

    # The git operations run inside the per-workspace sandbox container
    # (services.workspace_sandbox) and are therefore async; the synchronous
    # file/lint/pytest helpers are offloaded to a worker thread so the event
    # loop is never blocked.
    from services.workspace_runtime.git_ops import (
        create_review_branch,
        current_branch_name,
        git_commit,
        git_push,
    )

    lock = get_workspace_lock(workspace["id"])
    lock.acquire()
    try:
        ws_id = workspace.get("id", "")
        with Session(engine) as session:
            ws = session.get(Workspace, ws_id)
            if ws and ws.quarantined:
                raise HTTPException(
                    status_code=409,
                    detail=f"Workspace '{ws.display_name}' is quarantined after repeated verification failures. Admin review required.",
                )

        workspace_path = Path(workspace["resolved_path"])
        identity = workspace.get("resolved_identity") or {}
        requested_branch = _validate_branch_name(req.branch) if req.branch else ""
        branch_name = requested_branch or await current_branch_name(ws_id, workspace_path)

        if not requested_branch and req.auto_create_review_branch and (
            not branch_name or _is_protected_branch(branch_name, identity)
        ):
            branch_name = await create_review_branch(
                workspace_id=ws_id,
                workspace_path=workspace_path,
                identity=identity,
                workspace=workspace,
                relative_path=req.relative_path,
                prefix=req.review_branch_prefix,
            )

        if req.push:
            if not branch_name:
                raise HTTPException(status_code=400, detail="Unable to determine branch to push")
            if _is_protected_branch(branch_name, identity):
                raise HTTPException(
                    status_code=403,
                    detail=(
                        f"Autonomous push to protected branch '{branch_name}' is blocked. "
                        "Create or switch to a review branch and open a Pull Request."
                    ),
                )
            if not req.pytest_targets:
                raise HTTPException(
                    status_code=400,
                    detail="pytest_targets are required before autonomous push so Raven can prove the branch is review-ready.",
                )

        write_result = await asyncio.to_thread(
            write_file,
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

        lint_targets = _sanitize_targets(req.lint_paths or [req.relative_path])
        lint_results = []
        for lint_target in lint_targets:
            lint_result = await asyncio.to_thread(_run_lint_for_file, workspace_path, lint_target)
            lint_results.append(lint_result)
            if not lint_result["passed"]:
                failure_count = _record_verification_failure(req.relative_path)
                log.warning(f"[Quarantine] Lint failed for {req.relative_path} (failure #{failure_count} in window)")
                if failure_count >= RAVEN_QUARANTINE_THRESHOLD:
                    _auto_quarantine_workspace(ws_id, req.relative_path, failure_count)
                raise HTTPException(
                    status_code=400,
                    detail=f"Lint failed for workflow request on {lint_target}",
                )

        pytest_result = None
        if req.pytest_targets:
            pytest_result = await asyncio.to_thread(
                run_pytest,
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
                failure_count = _record_verification_failure(req.relative_path)
                log.warning(f"[Quarantine] Pytest failed for {req.relative_path} (failure #{failure_count} in window)")
                if failure_count >= RAVEN_QUARANTINE_THRESHOLD:
                    _auto_quarantine_workspace(ws_id, req.relative_path, failure_count)
                raise HTTPException(
                    status_code=400,
                    detail=f"Pytest failed for workflow request on {req.relative_path}",
                )

        commit_result = await git_commit(
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
            push_result = await git_push(
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

        provider_sync_result = None
        if req.sync_to_provider:
            provider_sync_result = await asyncio.to_thread(
                provider_sync_file,
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

        review = _build_review_metadata(
            workspace=workspace,
            relative_path=req.relative_path,
            commit_message=req.commit_message,
            branch_name=branch_name or await current_branch_name(ws_id, workspace_path),
            commit_result=commit_result,
            lint_results=lint_results,
            pytest_result=pytest_result,
            push_result=push_result,
        )

        _clear_verification_failures(req.relative_path)

        return {
            "status": "SUCCESS",
            "relative_path": req.relative_path,
            "write": write_result,
            "lint": lint_results,
            "pytest": pytest_result,
            "commit": commit_result,
            "push": push_result,
            "provider_sync": provider_sync_result,
            "review": review,
        }
    finally:
        lock.release()


@app.post("/tests/pytest")
def run_pytest(req: PytestRequest, x_internal_secret: str | None = Header(default=None)):
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


@app.post("/api/admin/tests/smoke")
def run_smoke_test(x_internal_secret: str | None = Header(default=None)):
    _require_internal_secret(x_internal_secret)
    # Run soa_smoke_test.py from the root of the repo (which is /workspace in the container)
    workspace_path = get_workspace_root()
    result = _run_command(workspace_path, ["python3", "soa_smoke_test.py"], timeout_seconds=60)
    return {
        "status": "SUCCESS",
        "passed": result["returncode"] == 0,
        "results": result["stdout"] + result["stderr"]
    }


@app.post("/api/admin/tests/unit")
def run_unit_tests(x_internal_secret: str | None = Header(default=None)):
    _require_internal_secret(x_internal_secret)
    # Run the CI unit test script from the root
    workspace_path = get_workspace_root()
    result = _run_command(workspace_path, ["bash", "scripts/run_ci_unit_tests.sh"], timeout_seconds=120)
    return {
        "status": "SUCCESS",
        "passed": result["returncode"] == 0,
        "results": result["stdout"] + result["stderr"]
    }


@app.post("/webhook/git-pull/{workspace_id}")
@app.post("/api/webhook/git-pull/{workspace_id}")
async def git_pull_webhook(
    workspace_id: str,
    background_tasks: BackgroundTasks,
    x_webhook_secret: str | None = Header(None, alias="X-Webhook-Secret"),
    token: str | None = None
):
    """
    Automated webhook endpoint for triggering a git pull.
    Expects a secret in the X-Webhook-Secret header or as a 'token' query parameter.
    """
    from services.config import GIT_WEBHOOK_SECRET
    webhook_secret = GIT_WEBHOOK_SECRET

    # Resolve workspace using admin context (since it's a system webhook)
    try:
        # We need to bypass the user context check since this is a system-level trigger
        with Session(engine) as session:
            match = session.get(Workspace, workspace_id)
            if not match:
                raise HTTPException(status_code=404, detail="Workspace not found")

            # Verify secret (either workspace-specific or global)
            expected_secret = decrypt(match.webhook_token_enc) if match.webhook_token_enc else match.webhook_token or webhook_secret
            if not expected_secret:
                log.warning(f"No webhook secret configured for workspace: {workspace_id}")
                raise HTTPException(status_code=503, detail="Webhook service unavailable")

            provided_secret = x_webhook_secret or token
            if provided_secret != expected_secret:
                log.warning(f"Invalid webhook secret attempt for workspace: {workspace_id}")
                raise HTTPException(status_code=403, detail="Forbidden")

            if not match.auto_pull_enabled:
                log.warning(f"Webhook pull attempted for workspace {workspace_id} but auto_pull_enabled is False")
                raise HTTPException(status_code=403, detail="Webhook pulling is disabled for this workspace")

            resolved_path = resolve_safe_path(get_workspace_root(), str(match.local_path), must_exist=False)
            workspace_path = Path(resolved_path)
            workspace_path.mkdir(parents=True, exist_ok=True)

            remote_name = (match.git_remote or "origin").strip()
            default_branch = (match.default_branch or "main").strip()

            # Check if it's a git repo
            if not (workspace_path / ".git").is_dir():
                log.info(f"Workspace {workspace_id} path {workspace_path} exists but is not a git repo. Checking for unsaved changes...")
                repo_url = match.repo_url
                if not repo_url:
                     raise HTTPException(status_code=400, detail="Cannot clone workspace: repo_url is missing")

                # Check for uncommitted changes if it's a git repo
                git_status_result = _run_command(workspace_path, ["git", "status", "--porcelain"])
                if git_status_result["returncode"] == 0 and git_status_result["stdout"].strip():
                    # Git repo exists but has uncommitted changes
                    untracked = [line.strip() for line in git_status_result["stdout"].strip().split("\n") if line.strip()]
                    log.error(f"Workspace {workspace_id} has {len(untracked)} uncommitted/unstaged changes. Aborting to prevent data loss.")
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "error": "UNSAVED_CHANGES",
                            "message": "Workspace has uncommitted changes that would be lost. Please commit or stash changes before pulling.",
                            "uncommitted_files": untracked[:50],
                            "total_uncommitted": len(untracked)
                        }
                    )

                # Directory exists but is not a git repo - check if it's empty or has content
                dir_contents = list(workspace_path.iterdir())
                if dir_contents:
                    log.info(f"Workspace {workspace_id} directory has existing content ({len(dir_contents)} items), clearing before clone...")
                    import shutil
                    # Remove entire directory tree and recreate it to avoid permission issues
                    for item in dir_contents:
                        try:
                            if item.is_dir():
                                shutil.rmtree(item)
                            else:
                                item.unlink()
                        except PermissionError:
                            # If we can't delete individual items, remove the whole tree
                            log.warning(f"Permission denied clearing {workspace_id}, using fallback removal")
                            try:
                                shutil.rmtree(workspace_path)
                                workspace_path.mkdir(parents=True, exist_ok=True)
                            except Exception as fallback_err:
                                log.error(f"Fallback removal also failed for {workspace_id}: {fallback_err}")
                                raise HTTPException(
                                    status_code=500,
                                    detail=f"Failed to clear workspace directory: {fallback_err!s}"
                                ) from None
                            break

                # Use the HTTPS redirector for the clone too if needed
                clone_url = _git_webhook_pull_remote(repo_url, remote_name)
                args = ["git", "clone", "-b", default_branch, clone_url, "."]
                result = _run_command(workspace_path, args)
                if result["returncode"] != 0:
                    raise HTTPException(status_code=500, detail=f"Initial clone failed: {result['stderr']}")

                return {"status": "SUCCESS", "message": f"Successfully cloned and initialized {workspace_id}", "branch": default_branch}

            log.info(f"Webhook git pull: resolved workspace_path={workspace_path}, remote_name={remote_name}")
            remote_url = _git_remote_url(workspace_path, remote_name)

        log.info(f"Webhook triggered git pull for workspace {workspace_id} on {remote_name}/{default_branch}")

        args = ["git", "pull", _git_webhook_pull_remote(remote_url, remote_name), default_branch]
        result = _run_command(workspace_path, args)

        if result["returncode"] == 0 and match.auto_backup_enabled and match.nextcloud_path:
            log.info(f"Triggering automatic Nextcloud backup for {workspace_id} to {match.nextcloud_path}")
            background_tasks.add_task(_trigger_nextcloud_sync, workspace_id, match.owner_user or "default", str(workspace_path), match.nextcloud_path, match.excludes)

        if result["returncode"] != 0:
            log.error(f"Git pull failed: {result['stderr']}")
            return {
                "status": "ERROR",
                "message": "Git pull failed",
                "detail": result["stderr"].strip()
            }

        return {
            "status": "SUCCESS",
            "message": f"Successfully pulled latest changes for {workspace_id}",
            "branch": default_branch
        }
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Webhook error: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from None


async def _trigger_nextcloud_sync(workspace_id: str, owner_user: str, local_path: str, remote_path: str, excludes: list[str] | None = None):
    """
    Background task to mirror a local workspace directory to Nextcloud.
    Resolves credentials via Identity service and calls Storage mirror endpoint.
    """
    lock = get_async_sync_lock(workspace_id)
    async with lock:
        try:
            log.info(f"Starting Nextcloud sync for {workspace_id} (owner: {owner_user})")
            # 1. Resolve credentials from Identity
            async with get_client() as client:
                resp = await client.post(
                    f"{IDENTITY_SVC_URL}/api/resolve",
                    json={"rag_user": owner_user},
                    headers={"X-Internal-Secret": INTERNAL_SECRET},
                    timeout=aiohttp.ClientTimeout(total=10.0),
                )
                if resp.status != 200:
                    text = await resp.text()
                    log.error(f"Failed to resolve identity for {owner_user}: {text}")
                    return

                creds = await resp.json()
                nc_url = creds.get("nextcloud_url")
                nc_user = creds.get("nextcloud_user")
                nc_pass = creds.get("nextcloud_pass")

                if not all([nc_url, nc_user, nc_pass]):
                    log.warning(f"Nextcloud credentials missing for {owner_user}. Skipping sync.")
                    return

                # 2. Trigger mirror via Storage Service
                mirror_req = {
                    "provider": {
                        "kind": "nextcloud",
                        "settings": {
                            "url": nc_url,
                            "username": nc_user,
                            "password": nc_pass
                        }
                    },
                    "remote_path": remote_path,
                    "local_path": local_path,
                    "excludes": excludes or []
                }

                resp = await client.post(
                    f"{STORAGE_SVC_URL}/providers/mirror",
                    json=mirror_req,
                    headers={"X-Internal-Secret": INTERNAL_SECRET}
                )
                if resp.status == 200:
                    log.info(f"Successfully triggered Nextcloud mirror for {workspace_id}")
                else:
                    text = await resp.text()
                    log.error(f"Failed to trigger Nextcloud mirror: {text}")

        except Exception as e:
            log.error(f"Error in _trigger_nextcloud_sync: {e}")


# Git endpoints are served from the modularized, sandbox-backed router so that
# every git command runs inside the workspace's dedicated container. This import
# is placed at the very bottom so that all shared helpers referenced by
# git_ops.py already exist on this module when it is imported (avoids a cycle).
from services.workspace_runtime.git_ops import git_router  # noqa: E402

app.include_router(git_router)

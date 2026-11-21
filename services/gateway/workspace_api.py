"""The Workspace API, as the gateway calls it.

Everything that touches workspace files or ports (the trainer's self-repair and
sermons, the studio's run/preview/media pipelines) goes through this module, so
there is exactly one place that knows the payload shapes, the internal secret and
the conflict-checked write. ``request`` is the single seam tests replace; nothing
else here performs I/O.
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any

import aiohttp

from services.gateway.config import INTERNAL_SECRET, WORKSPACE_RUNTIME_SVC

log = logging.getLogger("gateway.workspace_api")

#: A larger file is not something a small local model can repair or rewrite.
MAX_FILE_BYTES = 60_000

DEFAULT_TIMEOUT_S = 120.0


class WorkspaceError(RuntimeError):
    """A workspace call could not be completed.

    ``status`` is the Workspace Runtime's HTTP status when it answered (404 for
    a missing workspace, 403 for a capability refusal), ``None`` otherwise.
    """

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class WorkspaceConflict(WorkspaceError):
    """A write was refused because the file is not what the caller expected.

    Either it changed since the caller read it (``expected_sha256`` mismatch), or
    the caller asked to create a new file (``expected_sha256="new"``) and one is
    already there. Never retried blindly: the caller decides what to keep.
    """


class WorkspaceFileNotFound(WorkspaceError):
    """The workspace exists and answered, and the file is not in it."""


#: Pass as ``expected_sha256`` to create a file and refuse when it already exists.
CREATE_ONLY = "new"


def _write_error(path: str, status: int, body: dict[str, Any]) -> WorkspaceError:
    detail = body.get("detail") or body.get("message") or status
    if status == 409:
        return WorkspaceConflict(f"Could not write {path}: {detail}", status)
    return WorkspaceError(f"Could not write {path}: {detail}", status)


def _read_error(path: str, status: int, body: dict[str, Any]) -> WorkspaceError:
    detail = body.get("detail") or body.get("message") or status
    # The runtime answers 404 both for a missing workspace and a missing file;
    # only its "Path not found" means the file.
    if status == 404 and str(detail).startswith("Path not found"):
        return WorkspaceFileNotFound(f"Could not read {path}: {detail}", status)
    return WorkspaceError(f"Could not read {path}: {detail}", status)


def sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


async def request(
    endpoint: str,
    payload: dict[str, Any],
    timeout: float = DEFAULT_TIMEOUT_S,
    *,
    method: str = "POST",
    base_url: str | None = None,
    internal: bool = True,
) -> tuple[int, dict[str, Any]]:
    """Call an internal service (the Workspace Runtime unless ``base_url`` says otherwise).

    The one HTTP seam for the gateway's workspace features (the studio's shell
    runs, media generation and file I/O all come through here), so there is one
    place that knows the headers and the error-body shape. ``internal=False``
    leaves the internal secret off, for a host outside the stack (the image
    model's proxy must never see it).
    """
    from services.gateway.main import shared_http_client

    async with shared_http_client() as client, client.request(
        method,
        f"{base_url or WORKSPACE_RUNTIME_SVC}{endpoint}",
        json=payload if method != "GET" else None,
        headers={"X-Internal-Secret": INTERNAL_SECRET} if internal else {},
        timeout=aiohttp.ClientTimeout(total=timeout),
    ) as resp:
        try:
            body = await resp.json(content_type=None)
        except Exception:
            body = {"status": "ERROR", "message": (await resp.text())[:500]}
        return resp.status, body if isinstance(body, dict) else {"detail": body}


async def read_file(
    workspace_id: str, path: str, user: str = "default", *, max_bytes: int = MAX_FILE_BYTES
) -> str:
    """One file's text, through the Workspace API (which enforces the capability)."""
    status, body = await request(
        "/files/read",
        {
            "workspace_id": workspace_id,
            "relative_path": path,
            "max_bytes": max_bytes,
            "user_context": {"user": user},
        },
    )
    if status != 200 or body.get("status") != "SUCCESS":
        raise _read_error(path, status, body)
    return str(body.get("content") or "")


async def read_text_exact(
    workspace_id: str, path: str, *, user: str, max_bytes: int = MAX_FILE_BYTES
) -> str:
    """One file's whole text, or an error: never a silently truncated prefix.

    For read-modify-write callers (a manifest, a sermon), where writing back a
    truncated read would destroy the rest of the file.
    """
    status, body = await request(
        "/files/read",
        {
            "workspace_id": workspace_id,
            "relative_path": path,
            "max_bytes": max_bytes,
            "user_context": {"user": user},
        },
    )
    if status != 200 or body.get("status") != "SUCCESS":
        raise _read_error(path, status, body)
    if body.get("truncated"):
        raise WorkspaceError(f"{path} is larger than {max_bytes:,} bytes; refusing to read only part of it")
    return str(body.get("content") or "")


async def write_file(
    workspace_id: str,
    path: str,
    content: str,
    *,
    expected_sha256: str | None = None,
    user: str = "default",
    create_parents: bool = True,
) -> dict[str, Any]:
    """Write one file, refusing when it changed since the caller read it."""
    status, body = await request(
        "/files/write",
        {
            "workspace_id": workspace_id,
            "relative_path": path,
            "content": content,
            "expected_sha256": expected_sha256,
            "create_parents": create_parents,
            "user_context": {"user": user},
        },
    )
    if status not in (200, 201) or body.get("status") not in (None, "SUCCESS"):
        raise _write_error(path, status, body)
    return body


async def read_bytes(
    workspace_id: str,
    path: str,
    *,
    user: str = "default",
    max_bytes: int = 64 * 1024 * 1024,
    timeout: float = 120.0,
) -> bytes:
    """One file's raw bytes -- a mesh, a picture, a zip -- through ``/files/raw``.

    ``read_file`` decodes as text, which is right for source and fatal for binary
    STL: the bytes have to arrive exactly as stored. ``/files/raw`` streams the
    file, so this is the only honest way to open a model.

    ``max_bytes`` is enforced *before* buffering: a declared length over the cap
    is refused without reading, and the stream is abandoned the moment it passes
    the cap, so an oversized file never sits in the gateway's memory.
    """
    from services.gateway.main import shared_http_client

    async with shared_http_client() as client, client.post(
        f"{WORKSPACE_RUNTIME_SVC}/files/raw",
        json={
            "workspace_id": workspace_id,
            "relative_path": path,
            "user_context": {"user": user},
        },
        headers={"X-Internal-Secret": INTERNAL_SECRET},
        timeout=aiohttp.ClientTimeout(total=timeout),
    ) as resp:
        if resp.status != 200:
            text = (await resp.text())[:300]
            try:
                import json as _json

                parsed = _json.loads(text)
            except ValueError:
                parsed = {}
            body = parsed if isinstance(parsed, dict) else {}
            body.setdefault("detail", f"{resp.status} {text}")
            raise _read_error(path, resp.status, body)
        declared = resp.content_length
        if declared is not None and declared > max_bytes:
            raise WorkspaceError(f"{path} is {declared:,} bytes; the limit is {max_bytes:,}")
        buffer = bytearray()
        async for chunk in resp.content.iter_chunked(1 << 16):
            buffer += chunk
            if len(buffer) > max_bytes:
                raise WorkspaceError(f"{path} is over the {max_bytes:,}-byte limit")
    return bytes(buffer)


async def write_bytes(
    workspace_id: str,
    path: str,
    data: bytes,
    *,
    expected_sha256: str | None = None,
    user: str = "default",
    create_parents: bool = True,
) -> dict[str, Any]:
    """Write binary content (a generated picture, a zip) into the workspace."""
    import base64

    status, body = await request(
        "/files/write",
        {
            "workspace_id": workspace_id,
            "relative_path": path,
            "content_base64": base64.b64encode(data).decode("ascii"),
            "expected_sha256": expected_sha256,
            "create_parents": create_parents,
            "user_context": {"user": user},
        },
    )
    if status not in (200, 201) or body.get("status") not in (None, "SUCCESS"):
        raise _write_error(path, status, body)
    return body


async def run_pytest(
    workspace_id: str,
    targets: list[str] | None = None,
    *,
    user: str = "default",
    timeout_seconds: int = 300,
) -> dict[str, Any]:
    """The workspace's own test gate.

    Returns ``{"passed", "outcome", "returncode", "stdout", "stderr"}`` where
    ``outcome`` is ``"passed"``, ``"failed"`` (pytest ran and a test failed: exit
    code 1) or ``"error"`` (pytest could not judge the code: interrupted, an
    internal or usage error, no tests collected; exit codes 2-5). A call the
    runtime itself refused (non-200, a timeout) raises :class:`WorkspaceError`:
    that is a broken gate, not a failing test, and must not be read as one.
    """
    status, body = await request(
        "/tests/pytest",
        {
            "workspace_id": workspace_id,
            "targets": list(targets or []),
            "timeout_seconds": timeout_seconds,
            "user_context": {"user": user},
        },
        timeout=float(timeout_seconds) + 30.0,
    )
    if status != 200:
        detail = body.get("detail") or body.get("message") or status
        raise WorkspaceError(f"The test gate could not run in {workspace_id}: {detail}")
    returncode = body.get("returncode")
    if returncode == 0:
        outcome = "passed"
    elif returncode == 1:
        outcome = "failed"
    else:
        outcome = "error"
    return {
        "passed": outcome == "passed",
        "outcome": outcome,
        "returncode": returncode,
        "stdout": str(body.get("stdout") or ""),
        "stderr": str(body.get("stderr") or ""),
    }


async def list_files(
    workspace_id: str,
    path: str = ".",
    *,
    user: str = "default",
    recursive: bool = False,
    max_depth: int = 2,
    max_entries: int = 400,
) -> list[dict[str, Any]]:
    """The entries under ``path`` (a project detector needs only the names)."""
    status, body = await request(
        "/files/list",
        {
            "workspace_id": workspace_id,
            "relative_path": path,
            "recursive": recursive,
            "max_depth": max_depth,
            "max_entries": max_entries,
            "include_dirs": False,
            "user_context": {"user": user},
        },
    )
    if status != 200 or body.get("status") != "SUCCESS":
        detail = body.get("detail") or body.get("message") or status
        raise WorkspaceError(f"Could not list {path}: {detail}", status)
    return [entry for entry in (body.get("entries") or []) if isinstance(entry, dict)]


async def file_exists(workspace_id: str, path: str, *, user: str = "default") -> bool:
    """Whether one file exists, without reading it."""
    try:
        await read_file(workspace_id, path, user, max_bytes=1)
    except WorkspaceError:
        return False
    return True


async def expose_port(
    workspace_id: str, container_port: int, *, user: str = "default", host_port: int | None = None
) -> dict[str, Any]:
    """Publish a sandbox port so a browser on the LAN can open it."""
    status, body = await request(
        "/ports/expose",
        {
            "workspace_id": workspace_id,
            "container_port": container_port,
            "host_port": host_port,
            "user_context": {"user": user},
        },
    )
    if status != 200 or body.get("status") not in (None, "SUCCESS", "active"):
        detail = body.get("detail") or body.get("message") or status
        raise WorkspaceError(f"Could not expose port {container_port}: {detail}", status)
    return body


async def unexpose_port(workspace_id: str, host_port: int, *, user: str = "default") -> bool:
    """Stop forwarding one published port."""
    status, body = await request(
        "/ports/unexpose",
        {
            "workspace_id": workspace_id,
            "host_port": host_port,
            "user_context": {"user": user},
        },
    )
    if status != 200:
        detail = body.get("detail") or body.get("message") or status
        raise WorkspaceError(f"Could not unexpose port {host_port}: {detail}", status)
    return bool(body.get("removed", True))


async def list_ports(workspace_id: str) -> list[dict[str, Any]]:
    """The ports currently published for this workspace.

    A failed lookup is an error, not ``[]``: "no ports" would let a caller report
    every forward closed while they are still open.
    """
    status, body = await request(f"/ports/list/{workspace_id}", {}, method="GET")
    if status != 200:
        detail = body.get("detail") or body.get("message") or status
        raise WorkspaceError(f"Could not list the published ports of {workspace_id}: {detail}", status)
    # The runtime answers a bare JSON list, which ``request`` carries as
    # ``{"detail": [...]}``. Reading only ``ports`` here used to find nothing,
    # so a stopped preview "closed" no port and left every forward open.
    ports = body.get("ports", body.get("detail"))
    if not isinstance(ports, list):
        raise WorkspaceError(f"The port list for {workspace_id} was not a list: {body!r:.200}")
    return [port for port in ports if isinstance(port, dict)]

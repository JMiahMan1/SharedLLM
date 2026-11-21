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
    """A workspace call could not be completed."""


def sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


async def request(
    endpoint: str,
    payload: dict[str, Any],
    timeout: float = DEFAULT_TIMEOUT_S,
    *,
    method: str = "POST",
) -> tuple[int, dict[str, Any]]:
    """Call the Workspace Runtime with the internal secret."""
    from services.gateway.main import shared_http_client

    async with shared_http_client() as client, client.request(
        method,
        f"{WORKSPACE_RUNTIME_SVC}{endpoint}",
        json=payload if method != "GET" else None,
        headers={"X-Internal-Secret": INTERNAL_SECRET},
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
        detail = body.get("detail") or body.get("message") or status
        raise WorkspaceError(f"Could not read {path}: {detail}")
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
        detail = body.get("detail") or body.get("message") or status
        raise WorkspaceError(f"Could not write {path}: {detail}")
    return body


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
        detail = body.get("detail") or body.get("message") or status
        raise WorkspaceError(f"Could not write {path}: {detail}")
    return body


async def run_pytest(
    workspace_id: str,
    targets: list[str] | None = None,
    *,
    user: str = "default",
    timeout_seconds: int = 300,
) -> dict[str, Any]:
    """The workspace's own test gate."""
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
        detail = body.get("detail") or status
        return {"passed": False, "returncode": -1, "stdout": "", "stderr": str(detail)}
    return {
        "passed": bool(body.get("passed")),
        "returncode": body.get("returncode"),
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
        raise WorkspaceError(f"Could not list {path}: {detail}")
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
        raise WorkspaceError(f"Could not expose port {container_port}: {detail}")
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
        raise WorkspaceError(f"Could not unexpose port {host_port}: {detail}")
    return bool(body.get("removed", True))


async def list_ports(workspace_id: str, *, user: str = "default") -> list[dict[str, Any]]:
    """The ports currently published for this workspace."""
    status, body = await request(f"/ports/list/{workspace_id}", {}, method="GET")
    if status != 200:
        return []
    ports = body.get("ports") if isinstance(body, dict) else body
    return [port for port in (ports or []) if isinstance(port, dict)]

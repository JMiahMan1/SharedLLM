"""The Workspace API, as the trainer calls it.

Every trainer feature that touches files (self-repair, sermons, illustrations)
goes through this module, so there is exactly one place that knows the payload
shapes, the internal secret and the conflict-checked write. ``request`` is the
single seam tests replace; nothing else here performs I/O.
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any

import aiohttp

from services.gateway.config import INTERNAL_SECRET, WORKSPACE_RUNTIME_SVC

log = logging.getLogger("gateway.trainer.workspace_api")

#: A larger file is not something a small local model can repair or rewrite.
MAX_FILE_BYTES = 60_000

DEFAULT_TIMEOUT_S = 120.0


class WorkspaceError(RuntimeError):
    """A workspace call could not be completed."""


def sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


async def request(
    endpoint: str, payload: dict[str, Any], timeout: float = DEFAULT_TIMEOUT_S
) -> tuple[int, dict[str, Any]]:
    """POST to the Workspace Runtime with the internal secret."""
    from services.gateway.main import shared_http_client

    async with shared_http_client() as client, client.post(
        f"{WORKSPACE_RUNTIME_SVC}{endpoint}",
        json=payload,
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

"""Multi-file and folder uploads into a workspace.

``POST /files/upload`` takes ``multipart/form-data`` so files stream to disk
instead of travelling as base64 inside JSON:

* ``workspace_id``   the target workspace
* ``relative_path``  folder inside the workspace to upload into (default ".")
* ``overwrite``      "true" (default) replaces existing files, "false" skips them
* ``files``          one part per file
* ``paths``          one per file, in the same order: the file's path relative
                     to ``relative_path``. A folder upload sends
                     "photos/2024/a.jpg"; a plain upload sends the file name.

The caller identity arrives in the ``X-Workspace-User-Context`` header
(base64 JSON), set by the Gateway, never by the browser.

Imported at the bottom of ``main.py`` like ``git_ops``.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import os
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from starlette.datastructures import UploadFile

import services.config as config
from services.workspace_runtime.main import (
    WorkspaceRef,
    _require_internal_secret,
    _require_workspace_capability,
    _resolve_workspace,
    _strip_workspace_path_prefix,
    get_workspace_lock,
    resolve_safe_path,
)
from services.workspace_runtime.sync_ops import schedule_sync_after_write

upload_router = APIRouter()

# Starlette rejects forms with more parts than this. The UI sends folders in
# batches well under it.
MAX_FILES_PER_REQUEST = 1000


def _decode_user_context(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        value = json.loads(base64.b64decode(raw, validate=True).decode("utf-8"))
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail=f"X-Workspace-User-Context is not base64 JSON: {exc}") from None
    if not isinstance(value, dict):
        raise HTTPException(status_code=400, detail="X-Workspace-User-Context must encode a JSON object")
    return value


def _clean_upload_path(base: str, rel: str) -> str:
    """Join an upload's own path onto the target folder, refusing tricks."""
    rel = rel.replace("\\", "/")
    parts = [p for p in PurePosixPath(rel).parts if p not in ("", ".")]
    if not parts or rel.startswith("/") or any(p == ".." for p in parts):
        raise ValueError(f"invalid file path {rel!r}")
    if ".git" in parts:
        raise ValueError("uploads into .git are not allowed")
    joined = PurePosixPath(base.strip("/") or ".").joinpath(*parts)
    return joined.as_posix().removeprefix("./")


def _store(workspace_path: Path, rel: str, upload: UploadFile, overwrite: bool) -> dict[str, Any]:
    target = resolve_safe_path(workspace_path, rel, must_exist=False)
    if target.exists() and target.is_dir():
        raise ValueError("a folder with that name already exists")
    if target.exists() and not overwrite:
        return {"relative_path": rel, "skipped": True, "reason": "exists"}
    created = not target.exists()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".upload")
    digest = hashlib.sha256()
    size = 0
    try:
        with os.fdopen(fd, "wb") as out:
            upload.file.seek(0)
            for chunk in iter(lambda: upload.file.read(1 << 20), b""):
                out.write(chunk)
                digest.update(chunk)
                size += len(chunk)
        os.replace(tmp_name, target)
    finally:
        Path(tmp_name).unlink(missing_ok=True)
    return {"relative_path": rel, "size": size, "sha256": digest.hexdigest(), "created": created}


@upload_router.post("/files/upload")
async def upload_files(
    request: Request,
    x_internal_secret: str | None = Header(default=None),
    x_workspace_user_context: str | None = Header(default=None),
):
    _require_internal_secret(x_internal_secret)

    length = request.headers.get("content-length")
    if length is None:
        raise HTTPException(status_code=411, detail="Uploads must send Content-Length")
    try:
        declared = int(length)
    except ValueError:
        raise HTTPException(status_code=400, detail="Content-Length is not a number") from None
    limit = config.WORKSPACE_UPLOAD_MAX_BYTES
    if declared > limit:
        raise HTTPException(
            status_code=413,
            detail=f"Upload is {declared} bytes; the limit is {limit} (WORKSPACE_UPLOAD_MAX_BYTES). Send fewer files per request.",
        )

    user_context = _decode_user_context(x_workspace_user_context)
    form = await request.form(max_files=MAX_FILES_PER_REQUEST, max_fields=MAX_FILES_PER_REQUEST + 10)
    try:
        workspace_id = str(form.get("workspace_id") or "").strip()
        if not workspace_id:
            raise HTTPException(status_code=400, detail="workspace_id is required")
        base = str(form.get("relative_path") or ".")
        overwrite = str(form.get("overwrite") or "true").strip().lower() not in {"false", "0", "no"}
        files = [f for f in form.getlist("files") if isinstance(f, UploadFile)]
        paths = [str(p) for p in form.getlist("paths")]
        if not files:
            raise HTTPException(status_code=400, detail="No files in the upload (form field 'files')")
        if paths and len(paths) != len(files):
            raise HTTPException(status_code=400, detail=f"Got {len(files)} files but {len(paths)} paths")
        if not paths:
            paths = [f.filename or "" for f in files]

        ref = WorkspaceRef(workspace_id=workspace_id, user_context=user_context)
        workspace = await asyncio.to_thread(_resolve_workspace, ref, True)
        _require_workspace_capability(workspace, "write")
        base = _strip_workspace_path_prefix(base, workspace)
        workspace_path = Path(workspace["resolved_path"])

        def write_all() -> tuple[list, list, list]:
            uploaded, skipped, errors = [], [], []
            with get_workspace_lock(workspace["id"]):
                workspace_path.mkdir(parents=True, exist_ok=True)
                for upload, raw_path in zip(files, paths, strict=True):
                    try:
                        rel = _clean_upload_path(base, raw_path)
                        result = _store(workspace_path, rel, upload, overwrite)
                    except HTTPException as exc:
                        errors.append({"relative_path": raw_path, "error": str(exc.detail)})
                        continue
                    except (ValueError, OSError) as exc:
                        errors.append({"relative_path": raw_path, "error": str(exc)})
                        continue
                    (skipped if result.get("skipped") else uploaded).append(result)
            return uploaded, skipped, errors

        uploaded, skipped, errors = await asyncio.to_thread(write_all)
    finally:
        await form.close()

    if uploaded:
        schedule_sync_after_write(workspace)
    if errors and not uploaded and not skipped:
        raise HTTPException(status_code=400, detail={"message": "No files were uploaded", "errors": errors})
    return {
        "status": "SUCCESS" if not errors else "PARTIAL",
        "workspace_id": workspace["id"],
        "relative_path": base,
        "uploaded": uploaded,
        "skipped": skipped,
        "errors": errors,
        "bytes_written": sum(item["size"] for item in uploaded),
    }


"""OCR - Extract text from workspace images using the user-configured vision LLM.

Exposes the reusable vision_ocr_screenshot helper as a workspace-scoped Raven
tool endpoint (/execute/ocr). The image must live inside the mission's
workspace; the result is returned as structured ExecutionResult detail so the
agent can act on it (and chain it to other tasks).
"""

import base64
import binascii
import logging
import os
import tempfile

from fastapi import HTTPException

try:
    from schemas import ExecutionResult
except ImportError:  # pragma: no cover - dev fallback
    from services.execution.schemas import ExecutionResult

from services.execution.handlers import vision_ocr
from services.execution.handlers.workspace import (
    _resolve_workspace_info,
    resolve_safe_path,
)

log = logging.getLogger("execution.ocr")


#: An inline photo larger than this is refused rather than sent to the model.
MAX_INLINE_IMAGE_BYTES = 8 * 1024 * 1024


async def handle_ocr(req) -> ExecutionResult:
    """Run OCR on an image inside the mission workspace, or on inline bytes."""
    if getattr(req, "image_base64", None):
        return await _ocr_inline(req)
    if not req.image_path:
        return ExecutionResult(status="FAILURE", message="OCR failed: image_path or image_base64 is required", service="ocr")
    try:
        resolved_path, _ = await _resolve_workspace_info(
            req.workspace_id, getattr(req, "user_context", None)
        )
    except HTTPException as e:
        return ExecutionResult(
            status="FAILURE",
            message=f"OCR failed: {e.detail}",
            service="ocr",
            detail={"workspace_id": req.workspace_id},
        )

    try:
        safe_path = resolve_safe_path(req.image_path, resolved_path)
    except ValueError as e:
        return ExecutionResult(
            status="FAILURE",
            message=f"OCR failed: {e}",
            service="ocr",
            detail={"image_path": req.image_path},
        )

    if not safe_path or not os.path.isfile(safe_path):
        return ExecutionResult(
            status="FAILURE",
            message=f"OCR failed: image not found at '{req.image_path}'",
            service="ocr",
            detail={"image_path": req.image_path, "resolved_path": resolved_path},
        )

    return await _ocr_file(safe_path, req, {"image_path": req.image_path})


async def _ocr_inline(req) -> ExecutionResult:
    """OCR an image sent as base64, through a temporary file that is always removed."""
    try:
        raw = base64.b64decode(req.image_base64, validate=True)
    except (binascii.Error, ValueError):
        return ExecutionResult(status="FAILURE", message="OCR failed: image_base64 is not valid base64", service="ocr")
    if not raw:
        return ExecutionResult(status="FAILURE", message="OCR failed: the image is empty", service="ocr")
    if len(raw) > MAX_INLINE_IMAGE_BYTES:
        return ExecutionResult(status="FAILURE", message="OCR failed: the image is larger than 8 MB", service="ocr")
    fd, path = tempfile.mkstemp(suffix=".img", prefix="ocr-")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
        return await _ocr_file(path, req, {"inline": True})
    finally:
        try:
            os.remove(path)
        except OSError:
            log.warning("OCR temp file %s could not be removed", path)


async def _ocr_file(path: str, req, where: dict) -> ExecutionResult:
    """The shared vision call and result shaping for a file on disk."""
    try:
        result = await vision_ocr.vision_ocr_screenshot(
            path,
            user_context=getattr(req, "user_context", None),
            proxy_url=req.proxy_url,
            model=req.model,
            task=req.task or "general",
        )
    except Exception as exc:  # an unreadable image (PIL) is the caller's problem, said plainly
        return ExecutionResult(status="FAILURE", message=f"OCR failed: {exc}", service="ocr", detail=where)

    error = result.pop("_error", None)
    if error:
        return ExecutionResult(
            status="FAILURE",
            message=f"OCR failed: {error}",
            service="ocr",
            detail=where,
        )
    if not result.get("full_text"):
        return ExecutionResult(
            status="PARTIAL",
            message="OCR completed but returned no text",
            service="ocr",
            detail=result,
        )
    return ExecutionResult(
        status="SUCCESS",
        message="OCR extracted the following text from the image",
        service="ocr",
        detail=result,
    )

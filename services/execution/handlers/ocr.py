"""OCR - Extract text from workspace images using the user-configured vision LLM.

Exposes the reusable vision_ocr_screenshot helper as a workspace-scoped Raven
tool endpoint (/execute/ocr). The image must live inside the mission's
workspace; the result is returned as structured ExecutionResult detail so the
agent can act on it (and chain it to other tasks).
"""

import logging
import os

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


async def handle_ocr(req) -> ExecutionResult:
    """Run OCR on an image inside the mission workspace."""
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

    result = await vision_ocr.vision_ocr_screenshot(
        safe_path,
        user_context=getattr(req, "user_context", None),
        proxy_url=req.proxy_url,
        model=req.model,
        task=req.task or "general",
    )

    error = result.pop("_error", None)
    if error:
        return ExecutionResult(
            status="FAILURE",
            message=f"OCR failed: {error}",
            service="ocr",
            detail={"image_path": req.image_path},
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

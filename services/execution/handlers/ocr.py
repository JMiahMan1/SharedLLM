"""OCR - Extract text from workspace images using the user-configured vision LLM.

Exposes the reusable vision_ocr_screenshot helper as a workspace-scoped Raven
tool endpoint (/execute/ocr). The image must live inside the mission's
workspace; the result is returned as structured ExecutionResult detail so the
agent can act on it (and chain it to other tasks).

A PDF is read too, workspace file or inline: for 'general' and 'document' its
text layer is the exact text, so that is returned; a scan (no text layer), or a
task that needs the picture (a recipe card, a plant), has its pages rendered and
read by the vision model, ``OCR_PDF_MAX_PAGES`` per call from ``start_page``.
"""

import asyncio
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
from services.execution.pdf_text import (
    DEFAULT_MAX_CHARS,
    PdfReadError,
    extract_pdf_text,
    is_pdf,
    render_pdf_pages,
)

log = logging.getLogger("execution.ocr")


#: An inline photo larger than this is refused rather than sent to the model.
MAX_INLINE_IMAGE_BYTES = 8 * 1024 * 1024
#: Pages of a scanned PDF read by the vision model in one call (each is a model call).
OCR_PDF_MAX_PAGES = 5
#: Tasks whose answer is the text itself, so a PDF's text layer answers them exactly.
_TEXT_TASKS = {"general", "document"}


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


def _head(path: str) -> bytes:
    with open(path, "rb") as handle:
        return handle.read(1024)


async def _ocr_file(path: str, req, where: dict) -> ExecutionResult:
    """The shared vision call and result shaping for a file on disk."""
    try:
        pdf = is_pdf(None, await asyncio.to_thread(_head, path))
    except OSError as exc:
        return ExecutionResult(status="FAILURE", message=f"OCR failed: {exc}", service="ocr", detail=where)
    if pdf:
        return await _ocr_pdf(path, req, where)
    return await _ocr_image(path, req, where)


def _read(path: str) -> bytes:
    with open(path, "rb") as handle:
        return handle.read()


async def _ocr_pdf(path: str, req, where: dict) -> ExecutionResult:
    """A PDF: its text layer when that answers the task, else its pages as images."""
    data = await asyncio.to_thread(_read, path)
    start = max(1, int(getattr(req, "start_page", 1) or 1))
    task = req.task or "general"
    try:
        text = await asyncio.to_thread(extract_pdf_text, data, start_page=start, max_chars=DEFAULT_MAX_CHARS)
    except (PdfReadError, ValueError) as exc:
        return ExecutionResult(status="FAILURE", message=f"OCR failed: {exc}", service="ocr", detail=where)
    if not text.scanned and task in _TEXT_TASKS:
        detail = {**where, **text.as_detail(), "full_text": text.text, "headline": text.title, "subtext": "",
                  "badge": "", "source": "pdf-text-layer"}
        return ExecutionResult(
            status="SUCCESS",
            message=f"Read the PDF's text layer ({text.pages_label})",
            service="ocr",
            detail=detail,
        )
    with tempfile.TemporaryDirectory(prefix="ocr-pdf-") as tmp:
        try:
            pages, page_count = await asyncio.to_thread(
                render_pdf_pages, data, tmp, first_page=start, max_pages=OCR_PDF_MAX_PAGES
            )
        except (PdfReadError, ValueError) as exc:
            return ExecutionResult(status="FAILURE", message=f"OCR failed: {exc}", service="ocr", detail=where)
        if not pages:
            return ExecutionResult(
                status="FAILURE",
                message="OCR failed: no page of the PDF could be turned into an image to read.",
                service="ocr",
                detail=where,
            )
        results: list[tuple[int, dict]] = []
        for number, image in pages:
            result = await _vision(image, req, where)
            if isinstance(result, ExecutionResult):
                return result
            results.append((number, result))
    first_page, last_page = results[0][0], results[-1][0]
    if len(results) == 1:
        full_text = str(results[0][1].get("full_text") or "")
    else:
        full_text = "\n\n".join(f"--- Page {n} ---\n{r.get('full_text') or ''}".strip() for n, r in results)
    detail = {**where, **results[0][1], "full_text": full_text, "source": "pdf-pages-ocr",
              "page_count": page_count, "first_page": first_page, "last_page": last_page}
    if last_page < page_count:
        detail["next_start_page"] = last_page + 1
        detail["note"] = f"Read pages {first_page}-{last_page} of {page_count}; call again with start_page={last_page + 1}."
    if not any(str(r.get("full_text") or "").strip() for _, r in results):
        return ExecutionResult(status="PARTIAL", message="OCR completed but returned no text", service="ocr", detail=detail)
    return ExecutionResult(
        status="SUCCESS",
        message=f"OCR read pages {first_page}-{last_page} of the {page_count}-page PDF",
        service="ocr",
        detail=detail,
    )


async def _vision(path: str, req, where: dict):
    """One vision call: the result dict, or a FAILURE result to return as is."""
    try:
        result = await vision_ocr.vision_ocr_screenshot(
            path,
            user_context=getattr(req, "user_context", None),
            proxy_url=req.proxy_url,
            model=req.model,
            task=req.task or "general",
            # Always passed (None when there is none): a vision call that cannot
            # take it is a bug to see, not something to route around.
            context=getattr(req, "context", None),
        )
    except Exception as exc:  # an unreadable image (PIL) is the caller's problem, said plainly
        return ExecutionResult(status="FAILURE", message=f"OCR failed: {exc}", service="ocr", detail=where)
    error = result.pop("_error", None)
    if error:
        return ExecutionResult(status="FAILURE", message=f"OCR failed: {error}", service="ocr", detail=where)
    return result


async def _ocr_image(path: str, req, where: dict) -> ExecutionResult:
    """An image file: one vision call."""
    result = await _vision(path, req, where)
    if isinstance(result, ExecutionResult):
        return result
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

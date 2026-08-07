"""Image Edit - Edit workspace images with the user-configured image editing model.

Sends the source image plus an editing instruction to the LLM proxy's
/v1/images/edits endpoint (qwen-image-edit backend) and saves the edited
result back into the mission workspace. Workspace-scoped so Raven can chain
it with OCR (verify the edit) and other file tasks.
"""

import logging
import os

from fastapi import HTTPException

try:
    from schemas import ExecutionResult
except ImportError:  # pragma: no cover - dev fallback
    from services.execution.schemas import ExecutionResult

from services.config import IDENTITY_SVC_URL, INTERNAL_SECRET, WORKSPACE_RUNTIME_SVC_URL
from services.execution.handlers import vision_ocr
from services.execution.handlers.workspace import (
    _resolve_workspace_info,
    resolve_safe_path,
)

log = logging.getLogger("execution.image_edit")

_MAX_DIMENSION = 2048


async def get_image_edit_model() -> str | None:
    """Fetch the image edit model from Identity settings ('image_edit_model')."""
    try:
        import aiohttp

        async with aiohttp.ClientSession() as client:
            resp = await client.get(
                f"{IDENTITY_SVC_URL}/api/settings",
                headers={"X-Internal-Secret": INTERNAL_SECRET},
                timeout=aiohttp.ClientTimeout(total=5.0),
            )
            if resp.status == 200:
                settings = await resp.json()
                for s in settings:
                    if s.get("key") == "image_edit_model" and s.get("value"):
                        return s["value"].strip()
    except Exception as e:
        log.warning(f"Failed to get image edit model from Identity: {e}")
    return None


def _parse_size(size: str | None, source_size: tuple[int, int]) -> str:
    if not size:
        w, h = source_size
        return f"{w}x{h}"
    normalized = str(size).strip().replace(",", "x").replace("*", "x").replace(" ", "x")
    try:
        w_str, h_str = normalized.lower().split("x")[:2]
        w, h = int(w_str), int(h_str)
    except (ValueError, IndexError):
        raise ValueError(f"Invalid size '{size}'. Use WxH format (e.g. 960x720).") from None
    if w < 1 or h < 1 or w > _MAX_DIMENSION or h > _MAX_DIMENSION:
        raise ValueError(f"Size {w}x{h} out of range (1..{_MAX_DIMENSION} per side).")
    return f"{w}x{h}"


def _default_output_path(image_path: str) -> str:
    stem, ext = os.path.splitext(image_path)
    return f"{stem}_edited{ext or '.png'}"


async def handle_image_edit(req) -> ExecutionResult:
    """Edit an image inside the mission workspace via the LLM proxy."""
    try:
        resolved_path, _ = await _resolve_workspace_info(
            req.workspace_id, getattr(req, "user_context", None)
        )
    except HTTPException as e:
        return ExecutionResult(
            status="FAILURE",
            message=f"Image edit failed: {e.detail}",
            service="image_edit",
            detail={"workspace_id": req.workspace_id},
        )

    try:
        safe_path = resolve_safe_path(req.image_path, resolved_path)
    except ValueError as e:
        return ExecutionResult(
            status="FAILURE",
            message=f"Image edit failed: {e}",
            service="image_edit",
            detail={"image_path": req.image_path},
        )

    if not safe_path or not os.path.isfile(safe_path):
        return ExecutionResult(
            status="FAILURE",
            message=f"Image edit failed: source image not found at '{req.image_path}'",
            service="image_edit",
            detail={"image_path": req.image_path, "resolved_path": resolved_path},
        )

    # Resolve model: explicit override, else the user-configured setting. No
    # silent defaults - if the setting is missing, fail loudly with a clear fix.
    model = (req.model or "").strip()
    if not model:
        model = await get_image_edit_model()
    if not model:
        return ExecutionResult(
            status="FAILURE",
            message="Image edit failed: no image_edit_model configured. Set image_edit_model in Settings > AI & Compute (e.g. qwen-image-edit-rapid-aio:q4_k).",
            service="image_edit",
            detail={"image_path": req.image_path},
        )

    proxy_url = (req.proxy_url or "").strip().rstrip("/")
    if not proxy_url:
        proxy_url = await vision_ocr.get_ollama_url()
    if not proxy_url:
        return ExecutionResult(
            status="FAILURE",
            message="Image edit failed: no LLM proxy URL configured. Set llm_local_url in Settings > AI & Compute.",
            service="image_edit",
            detail={"image_path": req.image_path},
        )

    try:
        import aiohttp
        from PIL import Image

        with Image.open(safe_path) as img:
            source_size = img.size
        size = _parse_size(req.size, source_size)

        with open(safe_path, "rb") as f:
            image_bytes = f.read()
        content_type = "image/png" if safe_path.lower().endswith(".png") else "image/jpeg"

        form = aiohttp.FormData()
        form.add_field("model", model)
        form.add_field("prompt", req.prompt)
        form.add_field("size", size)
        form.add_field("response_format", "b64_json")
        form.add_field("image", image_bytes, filename=os.path.basename(safe_path), content_type=content_type)

        async with aiohttp.ClientSession() as client:
            resp = await client.post(
                f"{proxy_url}/v1/images/edits",
                data=form,
                timeout=aiohttp.ClientTimeout(total=590.0),
            )
            if resp.status != 200:
                body = (await resp.text())[:500]
                return ExecutionResult(
                    status="FAILURE",
                    message=f"Image edit API error {resp.status}: {body}",
                    service="image_edit",
                    detail={"image_path": req.image_path},
                )
            data = await resp.json()

        items = (data or {}).get("data") or []
        b64 = items[0].get("b64_json") if items else None
        if not b64:
            return ExecutionResult(
                status="FAILURE",
                message=f"Image edit returned no image data: {str(data)[:300]}",
                service="image_edit",
                detail={"image_path": req.image_path},
            )

        output_path = (req.output_path or "").strip() or _default_output_path(req.image_path)
        uc = getattr(req, "user_context", None)
        if hasattr(uc, "model_dump"):
            uc = uc.model_dump()
        elif hasattr(uc, "dict"):
            uc = uc.dict()
        async with aiohttp.ClientSession() as client:
            save_resp = await client.post(
                f"{WORKSPACE_RUNTIME_SVC_URL}/files/write",
                json={
                    "workspace_id": req.workspace_id,
                    "relative_path": output_path,
                    "content_base64": b64,
                    "create_parents": True,
                    "user_context": uc,
                },
                headers={"X-Internal-Secret": INTERNAL_SECRET},
                timeout=aiohttp.ClientTimeout(total=60.0),
            )
            if save_resp.status != 200:
                save_detail = (await save_resp.text())[:300]
                return ExecutionResult(
                    status="FAILURE",
                    message=f"Image edited but workspace save failed (status {save_resp.status}): {save_detail}",
                    service="image_edit",
                    detail={"image_path": req.image_path, "output_path": output_path},
                )

        return ExecutionResult(
            status="SUCCESS",
            message=f"Edited image saved to {output_path}",
            service="image_edit",
            detail={
                "image_path": req.image_path,
                "output_path": output_path,
                "model": model,
                "size": size,
            },
        )
    except Exception as e:
        log.error(f"Image edit failed: {e}")
        return ExecutionResult(
            status="FAILURE",
            message=f"Image edit failed: {e}",
            service="image_edit",
            detail={"image_path": req.image_path},
        )

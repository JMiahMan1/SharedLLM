"""Alpaca Vision for the Garden hub: Plant ID, Plant Doctor and growth photos.

One vision stack, used three ways. Every call goes through execution's
``/execute/ocr`` -- the same Qwen2.5-VL pipeline that reads recipe photos and
nutrition labels -- with a ``plant_identify`` or ``plant_diagnose`` task. The
model name and proxy come from Identity (``vision_ocr_model`` /
``llm_local_url``); when they are not configured the call fails here with a
message naming the missing setting, rather than pretending to have looked.

Photos themselves live in the household's Nextcloud under
``Garden/Photos/<plant_id>/``, uploaded by execution (``/execute/photo_upload``),
which also makes the small JPEG the garden keeps. Nextcloud being unconfigured
is not an error for a photo: it is reported as a warning and the journal entry
still records what happened.
"""
from __future__ import annotations

import base64
import json
import logging
import uuid
from typing import Any

import aiohttp

log = logging.getLogger("gateway.garden_vision")

#: A photo larger than this is refused before it is base64'd to the model.
MAX_IMAGE_BYTES = 8 * 1024 * 1024
_VISION_TIMEOUT = aiohttp.ClientTimeout(total=300)
_UPLOAD_TIMEOUT = aiohttp.ClientTimeout(total=120)


class VisionUnavailable(Exception):
    """Vision is not configured (or the model is unreachable): say which setting."""


class VisionFailed(Exception):
    """The model answered, but not with something usable."""


def _image_bytes(raw: bytes) -> bytes:
    if not raw:
        raise VisionFailed("The photo is empty.")
    if len(raw) > MAX_IMAGE_BYTES:
        raise VisionFailed(f"The photo is larger than {MAX_IMAGE_BYTES // (1024 * 1024)} MB.")
    return raw


async def look(raw: bytes, *, task: str, creds: Any, context: str | None = None) -> dict:
    """One vision reading: the task's structured detail, or a clear failure."""
    from services.gateway.config import EXECUTION_SVC, INTERNAL_SECRET
    from services.gateway.main import shared_http_client

    data = _image_bytes(raw)
    payload = {
        "user_context": creds,
        "image_base64": base64.b64encode(data).decode("ascii"),
        "task": task,
    }
    if context:
        payload["context"] = context
    try:
        async with shared_http_client() as client:
            async with client.post(
                f"{EXECUTION_SVC}/execute/ocr",
                json=payload,
                headers={"X-Internal-Secret": INTERNAL_SECRET},
                timeout=_VISION_TIMEOUT,
            ) as resp:
                body = await resp.json(content_type=None) if resp.status == 200 else None
    except Exception as exc:
        log.warning("[garden vision] %s call failed: %s", task, exc)
        raise VisionUnavailable("The vision service did not answer.") from exc

    if not isinstance(body, dict):
        raise VisionUnavailable("The vision service did not answer.")
    message = str(body.get("message") or "")
    if body.get("status") == "FAILURE":
        if "vision_ocr_model" in message or "LLM URL" in message or "LLM" in message:
            raise VisionUnavailable(
                message
                or "Vision is not configured: set vision_ocr_model (and llm_local_url) in Identity settings."
            )
        raise VisionFailed(message or "The photo could not be read.")
    detail = body.get("detail")
    if not isinstance(detail, dict):
        raise VisionFailed("The vision service returned no reading.")
    return detail


def _clean_name(value: Any) -> str:
    return str(value or "").strip()


async def identify(raw: bytes, *, creds: Any, search: Any) -> dict:
    """Snap-to-add: name the plant, then find it in the species catalog.

    ``search(query, category)`` returns the garden's species search result. The
    plant is never created here: the caller shows the match and the person adds
    it, which is what the UI's Add Plant form is for.
    """
    detail = await look(raw, task="plant_identify", creds=creds)
    plant = detail.get("plant") if isinstance(detail.get("plant"), dict) else {}
    identified = {
        "common_name": _clean_name(plant.get("common_name")),
        "scientific_name": _clean_name(plant.get("scientific_name")),
        "category": _clean_name(plant.get("category")).lower() or None,
        "confidence": _clean_name(plant.get("confidence")).lower() or "low",
        "is_seed_packet": bool(plant.get("is_seed_packet")),
        "notes": _clean_name(plant.get("notes")) or None,
        "description": _clean_name(detail.get("full_text")) or None,
    }
    matches: list[dict] = []
    query = identified["common_name"] or identified["scientific_name"]
    if query:
        for candidate in (identified["common_name"], identified["scientific_name"]):
            if not candidate:
                continue
            try:
                found = await search(candidate, identified["category"])
            except Exception as exc:
                log.info("[garden vision] species search '%s' failed: %s", candidate, exc)
                continue
            for row in (found or {}).get("results") or []:
                if isinstance(row, dict) and row.get("key") and row["key"] not in {m.get("key") for m in matches}:
                    matches.append(row)
            if matches:
                break
    return {
        "status": "ok",
        "identified": identified,
        "matches": matches[:5],
        "in_catalog": bool(matches),
        "can_draft_profile": bool(query) and not matches,
        "uncertain": detail.get("uncertain") or [],
    }


def diagnosis_context(*, plant: dict, logs: list[dict], weather: dict | None, symptoms: str | None) -> str:
    """The facts the Plant Doctor is allowed to reason from."""
    lines: list[str] = []
    label = str(plant.get("nickname") or plant.get("common_name") or plant.get("species_ref") or "the plant")
    species = plant.get("species") or {}
    lines.append(f"Plant: {label}")
    if species.get("common_name"):
        lines.append(f"Species: {species.get('common_name')} ({species.get('scientific_name') or 'unknown scientific name'})")
    if species.get("light_level"):
        lines.append(f"Expected light: {species['light_level']}")
    if species.get("watering_interval_days"):
        lines.append(f"Usual watering interval: {species['watering_interval_days']} days")
    if species.get("soil_mix"):
        lines.append(f"Soil mix: {species['soil_mix']}")
    if plant.get("location_name") or plant.get("location_id"):
        lines.append(f"Location: {plant.get('location_name') or plant.get('location_id')}")
    if plant.get("pot_material") or plant.get("pot_size_in"):
        lines.append(f"Pot: {plant.get('pot_material') or 'unknown material'}, {plant.get('pot_size_in') or '?'} in")
    if plant.get("window_direction"):
        lines.append(f"Window: {plant['window_direction']} ({plant.get('distance_from_window') or '?'} ft away)")
    if logs:
        recent = "; ".join(
            f"{row.get('action')} on {str(row.get('created_at'))[:10]} by {row.get('performed_by') or 'someone'}"
            for row in logs[:8]
            if isinstance(row, dict)
        )
        if recent:
            lines.append(f"Recent care: {recent}")
    summary = str(((weather or {}).get("summary")) or "").strip()
    if summary:
        lines.append(f"Weather: {summary}")
    if symptoms:
        lines.append(f"The family says: {symptoms.strip()}")
    return "\n".join(lines)


async def diagnose(
    raw: bytes,
    *,
    creds: Any,
    plant: dict,
    logs: list[dict],
    weather: dict | None,
    symptoms: str | None,
) -> dict:
    """Plant Doctor: a structured diagnosis grounded in this plant's own history."""
    context = diagnosis_context(plant=plant, logs=logs, weather=weather, symptoms=symptoms)
    detail = await look(raw, task="plant_diagnose", creds=creds, context=context)
    diagnosis = detail.get("diagnosis") if isinstance(detail.get("diagnosis"), dict) else {}
    causes = [
        {
            "cause": _clean_name(row.get("cause")),
            "confidence": _clean_name(row.get("confidence")).lower() or "low",
            "why": _clean_name(row.get("why")) or None,
        }
        for row in (diagnosis.get("likely_causes") or [])
        if isinstance(row, dict) and _clean_name(row.get("cause"))
    ]
    result = {
        "likely_causes": causes,
        "confidence": _clean_name(diagnosis.get("confidence")).lower() or "low",
        "urgency": _clean_name(diagnosis.get("urgency")).lower() or "low",
        "recovery_steps": [
            _clean_name(step) for step in (diagnosis.get("recovery_steps") or []) if _clean_name(step)
        ],
        "care_adjustments": [
            _clean_name(step) for step in (diagnosis.get("care_adjustments") or []) if _clean_name(step)
        ],
        "summary": _clean_name(diagnosis.get("summary")) or _clean_name(detail.get("full_text")),
    }
    if not result["summary"] and not result["likely_causes"]:
        raise VisionFailed("The photo could not be diagnosed. Try a closer photo of the affected leaves.")
    result["uncertain"] = detail.get("uncertain") or []
    result["context"] = context
    return result


async def upload_photo(
    raw: bytes,
    *,
    creds: Any,
    plant_id: str,
    thumbnail_max_px: int = 480,
) -> dict:
    """Keep the full photo in Nextcloud and return its link plus a small JPEG.

    Nextcloud being unconfigured is a warning, not a failure: the journal entry
    still records what was seen, and the thumbnail -- made by execution, the one
    stack with Pillow -- still shows in the UI.
    """
    from services.gateway.config import EXECUTION_SVC, INTERNAL_SECRET
    from services.gateway.main import shared_http_client

    data = _image_bytes(raw)
    payload = {
        "user_context": creds,
        "folder": f"Garden/Photos/{plant_id}",
        "filename": f"{uuid.uuid4().hex[:12]}.jpg",
        "data": base64.b64encode(data).decode("ascii"),
        "content_type": "image/jpeg",
        "thumbnail_max_px": thumbnail_max_px,
    }
    try:
        async with shared_http_client() as client:
            async with client.post(
                f"{EXECUTION_SVC}/execute/photo_upload",
                json=payload,
                headers={"X-Internal-Secret": INTERNAL_SECRET},
                timeout=_UPLOAD_TIMEOUT,
            ) as resp:
                body = await resp.json(content_type=None) if resp.status == 200 else None
    except Exception as exc:
        log.warning("[garden vision] photo upload failed: %s", exc)
        return {"nextcloud_path": None, "thumb_base64": None, "warning": "The photo was not stored in Nextcloud."}
    if not isinstance(body, dict) or body.get("status") != "SUCCESS":
        reason = (body or {}).get("message") if isinstance(body, dict) else None
        log.info("[garden vision] photo not stored: %s", reason)
        return {
            "nextcloud_path": None,
            "thumb_base64": None,
            "warning": reason or "The photo was not stored in Nextcloud.",
        }
    detail = body.get("detail") or {}
    # Execution answers SUCCESS even when only the thumbnail was kept, and says
    # so in ``detail.warning`` (Nextcloud) and ``detail.thumbnail_warning``
    # (resizing): both reach the person, never just one.
    warnings = [str(w) for w in (detail.get("warning"), detail.get("thumbnail_warning")) if w]
    return {
        "nextcloud_path": detail.get("path"),
        "thumb_base64": detail.get("thumbnail_base64"),
        "warning": " ".join(warnings) or None,
    }


def as_summary_json(diagnosis: dict) -> str:
    """The diagnosis as the JSON string the Garden stores on the photo record."""
    keep = {
        key: diagnosis.get(key)
        for key in ("likely_causes", "confidence", "urgency", "recovery_steps", "care_adjustments", "summary", "uncertain")
        if diagnosis.get(key)
    }
    return json.dumps(keep, ensure_ascii=False)[:8000]

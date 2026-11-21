"""Pictures and narration, made by the household's own models, kept with the work.

A game needs sprites; a deck needs a title slide; a lesson for the children needs
a voice. All three already exist on the model host (Alpaca's Stable Diffusion
proxy and the TTS engine), so this module is glue with two rules:

* **the file lands in the workspace**, under ``assets/``, with a manifest entry
  naming the prompt and the model -- a game that cannot say how its art was made
  cannot be re-made;
* **nothing is invented**: a failed generation is an error with the backend's own
  words, never a placeholder image.
"""
from __future__ import annotations

import base64
import json
import logging
import time
from dataclasses import asdict, dataclass
from typing import Any

import aiohttp

from services.gateway import workspace_api as ws
from services.gateway.config import EXECUTION_SVC, INTERNAL_SECRET

log = logging.getLogger("gateway.studio.media")

ASSETS_DIR = "assets"
MANIFEST_PATH = f"{ASSETS_DIR}/manifest.json"

DEFAULT_SIZE = "1024x1024"
IMAGE_TIMEOUT_S = 600.0
TTS_TIMEOUT_S = 300.0


class MediaError(RuntimeError):
    """The host could not make the asset."""


@dataclass
class Asset:
    kind: str  # image | audio
    path: str
    prompt: str = ""
    model: str = ""
    size: str = ""
    bytes: int = 0
    made_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def asset_path(
    name: str, *, suffix: str = ".png", folder: str = ASSETS_DIR
) -> str:
    """Where a generated asset goes: ``assets/<slug><suffix>`` by default."""
    from services.gateway.studio.profiles import slug

    return f"{folder}/{slug(name, fallback='asset')}{suffix}"


def merge_manifest(existing: str | None, asset: Asset) -> str:
    """Add one asset to the manifest JSON, keeping what is already there.

    Re-generating art replaces the entry for that path instead of piling up
    duplicates, so the manifest always describes the files that exist.
    """
    try:
        payload = json.loads(existing) if existing else {}
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    assets = payload.get("assets")
    if not isinstance(assets, list):
        assets = []
    assets = [
        item for item in assets if not (isinstance(item, dict) and item.get("path") == asset.path)
    ]
    assets.append(asset.to_dict())
    payload["assets"] = assets
    payload["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return json.dumps(payload, indent=2, ensure_ascii=False)


async def _post(
    service_url: str, endpoint: str, payload: dict[str, Any], timeout: float
) -> dict[str, Any]:
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as client, client.post(
        f"{service_url}{endpoint}",
        json=payload,
        headers={"X-Internal-Secret": INTERNAL_SECRET},
    ) as resp:
        try:
            body = await resp.json(content_type=None)
        except Exception:
            body = {"status": "ERROR", "message": (await resp.text())[:500]}
        return body if isinstance(body, dict) else {"status": "ERROR", "message": str(body)}


async def generate_image(
    workspace_id: str,
    prompt: str,
    *,
    name: str | None = None,
    size: str = DEFAULT_SIZE,
    model: str | None = None,
    user: str = "default",
    folder: str = ASSETS_DIR,
) -> Asset:
    """Generate one picture from a prompt and save it in the workspace."""
    from services.gateway.agent_loop import sd_proxy_base_url

    if not (prompt or "").strip():
        raise MediaError("a prompt is required")
    try:
        base = await sd_proxy_base_url()
    except RuntimeError as exc:
        raise MediaError(str(exc)) from exc

    body = await _post(
        base,
        "/v1/images/generations",
        {
            "prompt": prompt,
            "size": size,
            "n": 1,
            "model": model,
            "response_format": "b64_json",
        },
        IMAGE_TIMEOUT_S,
    )
    items = body.get("data") if isinstance(body.get("data"), list) else []
    encoded = (items[0] or {}).get("b64_json") if items else None
    if not encoded:
        message = body.get("message") or body.get("error") or "the image backend returned no picture"
        raise MediaError(str(message)[:300])

    data = base64.b64decode(encoded)
    path = asset_path(name or prompt, folder=folder)
    await ws.write_bytes(workspace_id, path, data, user=user)
    asset = Asset(
        kind="image",
        path=path,
        prompt=prompt,
        model=str(body.get("model") or model or ""),
        size=size,
        bytes=len(data),
        made_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    )
    await _record(workspace_id, asset, user)
    return asset


async def narrate(
    workspace_id: str,
    text: str,
    *,
    name: str = "narration",
    voice: str | None = None,
    user: str = "default",
) -> Asset:
    """Speak ``text`` into an audio file inside the workspace."""
    if not (text or "").strip():
        raise MediaError("text is required")
    path = asset_path(name, suffix=".wav")
    body = await _post(
        EXECUTION_SVC,
        "/execute/tts",
        {
            "text": text,
            "voice": voice,
            "workspace_id": workspace_id,
            "relative_path": path,
            "user_context": {"user": user},
        },
        TTS_TIMEOUT_S,
    )
    if str(body.get("status") or "").upper() != "SUCCESS":
        raise MediaError(str(body.get("message") or "the voice backend refused")[:300])
    detail = body.get("detail") if isinstance(body.get("detail"), dict) else {}
    asset = Asset(
        kind="audio",
        path=str(detail.get("relative_path") or detail.get("output_path") or path),
        prompt=(text or "")[:200],
        model=str(detail.get("voice") or voice or ""),
        made_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    )
    await _record(workspace_id, asset, user)
    return asset


async def _record(workspace_id: str, asset: Asset, user: str) -> None:
    """Append the asset to ``assets/manifest.json`` (best effort, never fatal)."""
    try:
        try:
            existing = await ws.read_file(workspace_id, MANIFEST_PATH, user, max_bytes=200_000)
        except ws.WorkspaceError:
            existing = ""
        await ws.write_file(workspace_id, MANIFEST_PATH, merge_manifest(existing, asset), user=user)
    except ws.WorkspaceError as exc:  # the asset itself landed; the index is a nicety
        log.warning("[studio] could not update the asset manifest: %s", exc)


async def manifest(workspace_id: str, *, user: str = "default") -> dict[str, Any]:
    """What art and audio this workspace has, and how it was made."""
    try:
        raw = await ws.read_file(workspace_id, MANIFEST_PATH, user, max_bytes=200_000)
    except ws.WorkspaceError:
        return {"assets": [], "updated_at": None}
    try:
        payload = json.loads(raw)
    except ValueError:
        return {"assets": [], "updated_at": None}
    if not isinstance(payload, dict):
        return {"assets": [], "updated_at": None}
    payload.setdefault("assets", [])
    return payload

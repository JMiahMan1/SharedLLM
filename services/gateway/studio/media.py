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
import binascii
import json
import logging
import time
from dataclasses import asdict, dataclass
from typing import Any

from services.gateway import workspace_api as ws
from services.gateway.config import EXECUTION_SVC

log = logging.getLogger("gateway.studio.media")

ASSETS_DIR = "assets"
MANIFEST_PATH = f"{ASSETS_DIR}/manifest.json"

DEFAULT_SIZE = "1024x1024"
IMAGE_TIMEOUT_S = 600.0
TTS_TIMEOUT_S = 300.0

#: The most the Workspace Runtime returns from one read; a larger manifest is
#: refused rather than read in part.
MANIFEST_MAX_BYTES = 200_000

#: Concurrent generations each add to the manifest; a lost race re-reads and
#: merges again rather than overwriting the other writer's entry.
MANIFEST_WRITE_ATTEMPTS = 3


class MediaError(RuntimeError):
    """The host could not make the asset, or its record could not be kept."""


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


def parse_manifest(raw: str | None) -> dict[str, Any]:
    """The manifest as a dict; an unreadable one is an error, never ``{}``.

    Treating a corrupt or hand-broken manifest as empty made the next
    generation write ``{"assets": [<one entry>]}`` over it and lose the record
    of every other asset.
    """
    if not raw or not raw.strip():
        return {}
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise MediaError(f"{MANIFEST_PATH} is not valid JSON ({exc}); fix or remove it") from exc
    if not isinstance(payload, dict):
        raise MediaError(f"{MANIFEST_PATH} must hold a JSON object, not {type(payload).__name__}")
    if not isinstance(payload.get("assets", []), list):
        raise MediaError(f"{MANIFEST_PATH}: 'assets' must be a list")
    return payload


def merge_manifest(existing: str | None, asset: Asset) -> str:
    """Add one asset to the manifest JSON, keeping what is already there.

    Re-generating art replaces the entry for that path instead of piling up
    duplicates, so the manifest always describes the files that exist.
    """
    payload = parse_manifest(existing)
    assets = [
        item
        for item in payload.get("assets", [])
        if not (isinstance(item, dict) and item.get("path") == asset.path)
    ]
    assets.append(asset.to_dict())
    payload["assets"] = assets
    payload["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return json.dumps(payload, indent=2, ensure_ascii=False)


async def _post(
    service_url: str, endpoint: str, payload: dict[str, Any], timeout: float, *, internal: bool
) -> dict[str, Any]:
    """POST through the gateway's one service seam; an HTTP error is a ``MediaError``."""
    status, body = await ws.request(
        endpoint, payload, timeout, base_url=service_url, internal=internal
    )
    if not 200 <= status < 300:
        detail = body.get("detail") or body.get("message") or body.get("error") or body
        raise MediaError(f"{endpoint} answered HTTP {status}: {str(detail)[:300]}")
    return body


async def generate_image(
    workspace_id: str,
    prompt: str,
    *,
    name: str | None = None,
    size: str = DEFAULT_SIZE,
    model: str | None = None,
    user: str,
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
        # The image proxy is the model host, outside the stack: it never gets
        # the internal secret.
        internal=False,
    )
    items = body.get("data") if isinstance(body.get("data"), list) else []
    encoded = (items[0] or {}).get("b64_json") if items else None
    if not encoded:
        message = body.get("message") or body.get("error") or "the image backend returned no picture"
        raise MediaError(str(message)[:300])

    try:
        data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise MediaError(f"the image backend returned a picture that is not base64: {exc}") from exc
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
    user: str,
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
        internal=True,
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


async def _read_manifest(workspace_id: str, user: str) -> tuple[str, str]:
    """The manifest text and the sha256 of exactly that text (``CREATE_ONLY`` if none).

    Only a missing file means "no manifest yet". Any other failure, or a file too
    large to read whole, is raised: nothing may be written over a manifest that
    could not be read.
    """
    try:
        raw = await ws.read_text_exact(
            workspace_id, MANIFEST_PATH, user=user, max_bytes=MANIFEST_MAX_BYTES
        )
    except ws.WorkspaceFileNotFound:
        return "", ws.CREATE_ONLY
    return raw, ws.sha256(raw)


async def _record(workspace_id: str, asset: Asset, user: str) -> None:
    """Add the asset to ``assets/manifest.json`` without losing anyone else's entry.

    Read, merge, and write back only if the file is still what was read
    (``expected_sha256``); a concurrent writer means read and merge again. A
    manifest that cannot be updated is an error naming the asset, which has
    already landed, so the caller knows the index no longer describes it.
    """
    for _attempt in range(MANIFEST_WRITE_ATTEMPTS):
        try:
            raw, sha = await _read_manifest(workspace_id, user)
            merged = merge_manifest(raw, asset)
        except (ws.WorkspaceError, MediaError) as exc:
            raise MediaError(
                f"{asset.path} was written, but {MANIFEST_PATH} could not be read: {exc}"
            ) from exc
        try:
            await ws.write_file(workspace_id, MANIFEST_PATH, merged, user=user, expected_sha256=sha)
            return
        except ws.WorkspaceConflict:
            continue
        except ws.WorkspaceError as exc:
            raise MediaError(
                f"{asset.path} was written, but {MANIFEST_PATH} was not updated: {exc}"
            ) from exc
    raise MediaError(
        f"{asset.path} was written, but {MANIFEST_PATH} kept changing during the update "
        f"({MANIFEST_WRITE_ATTEMPTS} attempts); try again"
    )


async def manifest(workspace_id: str, *, user: str) -> dict[str, Any]:
    """What art and audio this workspace has, and how it was made.

    No manifest file yet is an empty list; every other failure is raised.
    """
    raw, _sha = await _read_manifest(workspace_id, user)
    payload = parse_manifest(raw)
    payload.setdefault("assets", [])
    payload.setdefault("updated_at", None)
    return payload

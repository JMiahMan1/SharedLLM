"""Podcast + speaker tools - render an episode and identify who is speaking.

Three handlers, all delegating to alpaca rather than doing the work here:

* ``podcast_render``    - POST {ALPACA_WEB_URL}/api/podcast/render, which
  synthesizes each turn with that host's Kokoro voice, synthesizes a music
  bed, ducks it under the speech and mixes the result. The mixed WAV is then
  written into the mission workspace.
* ``list_voices``       - GET {ALPACA_WEB_URL}/api/podcast/voices. The caller
  needs this *before* assigning a clone: OpenVoice transfers timbre well only
  onto a source voice of the same gender, and this is where both sides of that
  pair are visible.
* ``speaker_identify``  - POST {ALPACA_AUDIO_URL}/api/voices/identify. This one
  deliberately does NOT go through the dashboard: the comparison needs the
  OpenVoice reference encoder, which only exists in the audio-server container
  because it is the only one with torch loaded. See services/config.py for why
  there are two base URLs.

Every failure is returned as an ``ExecutionResult(status="FAILURE")`` rather
than raised, so a mission gets a diagnostic it can act on instead of a stack
trace. The rendering path is workspace-scoped: the episode lands next to the
script the agent just wrote, which is what makes the write-script-then-render
chaining worth having.
"""

import base64
import logging
import os

try:
    from schemas import ExecutionResult
except ImportError:  # pragma: no cover - dev fallback
    from services.execution.schemas import ExecutionResult

from services.config import (
    ALPACA_AUDIO_URL,
    ALPACA_WEB_URL,
    INTERNAL_SECRET,
    WORKSPACE_RUNTIME_SVC_URL,
)
from services.execution.handlers.workspace import _resolve_workspace_info, resolve_safe_path

log = logging.getLogger("execution.podcast")

# Rendering speaks every turn of the script, so it is bounded by how long a
# person will sit through a whole episode. 30 minutes is past any scripted
# podcast and well inside the dashboard's per-turn TTS budget.
_RENDER_TIMEOUT_S = 1800.0
_IDENTIFY_TIMEOUT_S = 120.0
_VOICES_TIMEOUT_S = 30.0
_SAVE_TIMEOUT_S = 60.0

_DEFAULT_OUTPUT = "podcast.wav"
_AUDIO_SUFFIXES = (".wav", ".mp3", ".m4a", ".ogg", ".flac", ".aac", ".opus")


def _user_context_dict(req) -> dict | None:
    """Pydantic v2 then v1, then nothing - the field is optional downstream."""
    uc = getattr(req, "user_context", None)
    if hasattr(uc, "model_dump"):
        return uc.model_dump()
    if hasattr(uc, "dict"):
        return uc.dict()
    return uc


def _default_output() -> str:
    return _DEFAULT_OUTPUT


async def _write_to_workspace(req, relative_path: str, content_b64: str) -> tuple[bool, str]:
    """POST the bytes to workspace_runtime. Returns (ok, message)."""
    import aiohttp

    async with aiohttp.ClientSession() as client:
        resp = await client.post(
            f"{WORKSPACE_RUNTIME_SVC_URL}/files/write",
            json={
                "workspace_id": req.workspace_id,
                "relative_path": relative_path,
                "content_base64": content_b64,
                "create_parents": True,
                "user_context": _user_context_dict(req),
            },
            headers={"X-Internal-Secret": INTERNAL_SECRET},
            timeout=aiohttp.ClientTimeout(total=_SAVE_TIMEOUT_S),
        )
        if resp.status != 200:
            return False, f"workspace save failed (status {resp.status}): {(await resp.text())[:300]}"
    return True, f"saved to {relative_path}"


async def handle_podcast_render(req) -> ExecutionResult:
    """Render a scripted podcast episode and save the mixed WAV into the workspace."""
    # The workspace only has to EXIST here - there is no input file to resolve,
    # the script arrives in the request body. Resolving it anyway means an
    # unresolvable workspace is a clear FAILURE before a 30-minute render.
    try:
        await _resolve_workspace_info(req.workspace_id, getattr(req, "user_context", None))
    except Exception as e:
        return ExecutionResult(
            status="FAILURE",
            message=f"Podcast render failed: {e}",
            service="podcast_render",
            detail={"workspace_id": req.workspace_id},
        )

    script = (req.script or "").strip()
    if not script:
        return ExecutionResult(
            status="FAILURE",
            message="Podcast render failed: script is empty. Write the speaker-tagged script first.",
            service="podcast_render",
            detail={},
        )

    output_path = (req.output_path or "").strip() or _default_output()

    payload = {
        "script": script,
        "return_data_uri": True,
    }
    if req.pair_id:
        payload["pair_id"] = req.pair_id
    if req.bed_preset:
        payload["bed_preset"] = req.bed_preset
    if req.voice_profiles:
        payload["voice_profiles"] = req.voice_profiles

    try:
        import aiohttp

        async with aiohttp.ClientSession() as client:
            resp = await client.post(
                f"{ALPACA_WEB_URL}/api/podcast/render",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=_RENDER_TIMEOUT_S),
            )
            if resp.status != 200:
                return ExecutionResult(
                    status="FAILURE",
                    message=f"Podcast render failed: dashboard returned {resp.status}: {(await resp.text())[:300]}",
                    service="podcast_render",
                    detail={"output_path": output_path},
                )
            data = await resp.json()
    except Exception as e:
        log.error(f"Podcast render failed: {e}")
        return ExecutionResult(
            status="FAILURE",
            message=f"Podcast render failed: {e}",
            service="podcast_render",
            detail={"output_path": output_path},
        )

    data_uri = (data or {}).get("data_uri")
    if not data_uri:
        return ExecutionResult(
            status="FAILURE",
            message=f"Podcast render returned no audio: {str(data)[:300]}",
            service="podcast_render",
            detail={"output_path": output_path},
        )

    # "data:audio/wav;base64,XXXX" -> XXXX
    _, _, b64 = data_uri.partition(",")
    if not b64:
        return ExecutionResult(
            status="FAILURE",
            message="Podcast render returned a malformed data URI (no base64 payload).",
            service="podcast_render",
            detail={"output_path": output_path},
        )

    ok, save_msg = await _write_to_workspace(req, output_path, b64)
    if not ok:
        return ExecutionResult(
            status="FAILURE",
            message=f"Podcast rendered but {save_msg}",
            service="podcast_render",
            detail={"output_path": output_path},
        )

    warnings = (data or {}).get("warnings") or []
    detail = {
        "output_path": output_path,
        "duration_s": (data or {}).get("duration_s"),
        "turn_count": (data or {}).get("turn_count"),
        "bed_preset": (data or {}).get("bed_preset"),
        "bed_duck_db": (data or {}).get("bed_duck_db"),
        "warnings": warnings,
    }
    suffix = f" ({len(warnings)} warning(s))" if warnings else ""
    return ExecutionResult(
        status="SUCCESS",
        message=f"Podcast episode {save_msg} - {detail['duration_s']}s, {detail['turn_count']} turns{suffix}",
        service="podcast_render",
        detail=detail,
    )


async def handle_speaker_identify(req) -> ExecutionResult:
    """Identify which enrolled speaker a workspace audio clip belongs to."""
    if not ALPACA_AUDIO_URL:
        return ExecutionResult(
            status="FAILURE",
            message=(
                "Speaker identification failed: no audio backend configured. "
                "Set ALPACA_AUDIO_URL to the alpaca audio-server (e.g. http://host:8082)."
            ),
            service="speaker_identify",
            detail={},
        )

    try:
        resolved_path, _ = await _resolve_workspace_info(req.workspace_id, getattr(req, "user_context", None))
    except Exception as e:
        return ExecutionResult(
            status="FAILURE",
            message=f"Speaker identification failed: {e}",
            service="speaker_identify",
            detail={"workspace_id": req.workspace_id},
        )

    try:
        safe_path = resolve_safe_path(req.audio_path, resolved_path)
    except ValueError as e:
        return ExecutionResult(
            status="FAILURE",
            message=f"Speaker identification failed: {e}",
            service="speaker_identify",
            detail={"audio_path": req.audio_path},
        )

    if not safe_path or not os.path.isfile(safe_path):
        return ExecutionResult(
            status="FAILURE",
            message=f"Speaker identification failed: clip not found at '{req.audio_path}'",
            service="speaker_identify",
            detail={"audio_path": req.audio_path},
        )

    if not safe_path.lower().endswith(_AUDIO_SUFFIXES):
        return ExecutionResult(
            status="FAILURE",
            message=(
                f"Speaker identification failed: '{req.audio_path}' is not audio "
                f"(expected one of {', '.join(_AUDIO_SUFFIXES)})."
            ),
            service="speaker_identify",
            detail={"audio_path": req.audio_path},
        )

    with open(safe_path, "rb") as f:
        clip = f.read()
    if not clip:
        return ExecutionResult(
            status="FAILURE",
            message=f"Speaker identification failed: '{req.audio_path}' is empty.",
            service="speaker_identify",
            detail={"audio_path": req.audio_path},
        )

    payload = {"audio_b64": base64.b64encode(clip).decode("ascii")}
    if req.threshold is not None:
        # Omitted on purpose: the audio server derives one from how much its own
        # enrolled speakers vary between takes, which beats any constant we could
        # ship here.
        payload["threshold"] = req.threshold

    try:
        import aiohttp

        async with aiohttp.ClientSession() as client:
            resp = await client.post(
                f"{ALPACA_AUDIO_URL}/api/voices/identify",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=_IDENTIFY_TIMEOUT_S),
            )
            if resp.status == 404:
                return ExecutionResult(
                    status="FAILURE",
                    message="Speaker identification failed: no voices are enrolled yet. Record one in the dashboard's Audio Studio first.",
                    service="speaker_identify",
                    detail={"audio_path": req.audio_path},
                )
            if resp.status == 422:
                return ExecutionResult(
                    status="FAILURE",
                    message=f"Speaker identification failed: not enough speech in the clip. {(await resp.text())[:200]}",
                    service="speaker_identify",
                    detail={"audio_path": req.audio_path},
                )
            if resp.status != 200:
                return ExecutionResult(
                    status="FAILURE",
                    message=f"Speaker identification failed: audio server returned {resp.status}: {(await resp.text())[:300]}",
                    service="speaker_identify",
                    detail={"audio_path": req.audio_path},
                )
            data = await resp.json()
    except Exception as e:
        log.error(f"Speaker identification failed: {e}")
        return ExecutionResult(
            status="FAILURE",
            message=f"Speaker identification failed: {e}",
            service="speaker_identify",
            detail={"audio_path": req.audio_path},
        )

    matched = (data or {}).get("matched_name")
    score = (data or {}).get("score")
    candidates = (data or {}).get("candidates") or []
    runner_up = (data or {}).get("runner_up")
    detail = {
        "audio_path": req.audio_path,
        "matched": (data or {}).get("matched"),
        "matched_name": matched,
        "score": score,
        "threshold": (data or {}).get("threshold"),
        "margin": (data or {}).get("margin"),
        "runner_up": runner_up,
        "candidates": candidates,
    }
    if matched:
        msg = f"Clip matches {matched} (score {score})"
        if runner_up:
            msg += f"; next closest is {runner_up}"
        return ExecutionResult(status="SUCCESS", message=msg, service="speaker_identify", detail=detail)

    # No match still returns the ranking on purpose: "closest is X but it is not
    # them" is what the caller needs to decide whether to enrol or re-record.
    closest = candidates[0].get("name") if candidates and isinstance(candidates[0], dict) else None
    return ExecutionResult(
        status="SUCCESS",
        message=(
            f"Clip does not match any enrolled voice (best score {score}"
            + (f", closest is {closest}" if closest else "")
            + ")."
        ),
        service="speaker_identify",
        detail=detail,
    )


async def handle_list_voices(req) -> ExecutionResult:
    """List the enrolled speaker profiles and the curated podcast host pairs."""
    try:
        import aiohttp

        async with aiohttp.ClientSession() as client:
            resp = await client.get(
                f"{ALPACA_WEB_URL}/api/podcast/voices",
                timeout=aiohttp.ClientTimeout(total=_VOICES_TIMEOUT_S),
            )
            if resp.status != 200:
                return ExecutionResult(
                    status="FAILURE",
                    message=f"List voices failed: dashboard returned {resp.status}: {(await resp.text())[:300]}",
                    service="list_voices",
                    detail={},
                )
            data = await resp.json()
    except Exception as e:
        log.error(f"List voices failed: {e}")
        return ExecutionResult(
            status="FAILURE",
            message=f"List voices failed: {e}",
            service="list_voices",
            detail={},
        )

    profiles = (data or {}).get("saved_profiles") or []
    roster = (data or {}).get("roster") or []
    if req.pair_id:
        roster = [r for r in roster if r.get("pair_id") == req.pair_id]

    hosts = [
        {
            "pair_id": r.get("pair_id"),
            "slot": r.get("slot"),
            "name": r.get("name"),
            "voice": r.get("voice"),
            "role": r.get("role"),
            "gender": r.get("gender"),
            "source_gender": r.get("source_gender"),
            "clone": r.get("clone"),
        }
        for r in roster
        if isinstance(r, dict)
    ]
    saved = [
        {"id": p.get("id"), "name": p.get("name"), "engine": p.get("engine")}
        for p in profiles
        if isinstance(p, dict)
    ]
    return ExecutionResult(
        status="SUCCESS",
        message=f"{len(saved)} enrolled voice profile(s), {len(hosts)} podcast host(s)",
        service="list_voices",
        detail={"saved_profiles": saved, "hosts": hosts},
    )

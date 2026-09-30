"""What the AI tools can actually do right now, and why not when they can't.

The Workspace used to show a model dropdown and nothing else, which reads as
"this works" even when every call returns a backend error -- the image backend
advertised all three models while `/v1/images/edits` failed outright. Listing a
capability and being able to perform it are different facts, and only the second
one is worth putting in front of a user.

Deliberate design choices:

* **Cheap probes only.** Availability is derived from a fast reachability
  check plus observed history, never from a trial generation. A real image edit
  takes minutes on CPU, so probing by doing the work would stall the page.
* **"Unverified" is a real answer.** Whether a backend accepts a second image
  cannot be known without attempting one, so `face_swap` starts out unverified
  and becomes available (or not) from what actually happened, rather than
  guessing either way.
* **Never silently drop a capability.** Every entry carries a `detail` string
  naming the model, the reason, or the setting to change.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field, asdict

# How long an observation stays fresh. A capability that worked 20 minutes ago
# is worth reporting as working; one that failed a second ago is not.
OBSERVATION_TTL_S = 900.0

# Probes are reachability checks, not jobs. Keep them short enough that a dead
# host cannot hold the request open.
PROBE_TIMEOUT_S = 5.0


@dataclass
class Capability:
    key: str
    label: str
    available: bool | None  # None = unverified
    detail: str


@dataclass
class _Observation:
    """The last real outcome of a capability, kept so we report fact not hope."""

    seen_at: float
    available: bool
    detail: str


# Donor's multi-image support is a property of the SD build, not of anything we
# can assert, so it is learned from actual calls. Keyed by proxy URL because a
# settings change points at a different backend with different behaviour.
_observations: dict[str, _Observation] = {}


def record_observation(key: str, available: bool, detail: str = "", *, bucket: str = "default") -> None:
    """Record what actually happened when a capability was exercised."""
    _observations[f"{bucket}:{key}"] = _Observation(time.monotonic(), available, detail)


def _observation(key: str, bucket: str = "default") -> _Observation | None:
    obs = _observations.get(f"{bucket}:{key}")
    if obs is None:
        return None
    if time.monotonic() - obs.seen_at > OBSERVATION_TTL_S:
        return None
    return obs


def _stale_observation(key: str, bucket: str = "default") -> _Observation | None:
    """A known outcome that has aged out. Better than nothing, but must be
    labelled as remembered rather than current."""
    return _observations.get(f"{bucket}:{key}")


async def _probe_image_backend() -> tuple[bool, str]:
    """Is the image backend reachable and does it advertise any model?

    Reachability only. It deliberately does not try a generation: on CPU that
    takes minutes, and a page that waits minutes to learn it is already broken
    is worse than one that says "not confirmed".
    """
    try:
        from services.execution.handlers import image_edit

        proxy_url = await image_edit.vision_ocr.get_ollama_url()
        if not proxy_url:
            return False, "No image backend configured. Set llm_local_url in Settings > AI & Compute."

        import aiohttp

        async with aiohttp.ClientSession() as client:
            resp = await client.get(
                f"{proxy_url}/v1/images/models",
                timeout=aiohttp.ClientTimeout(total=PROBE_TIMEOUT_S),
            )
            if resp.status != 200:
                return False, f"Image backend answered HTTP {resp.status}."
            payload = await resp.json()
        models = [m.get("id") for m in (payload or {}).get("data", []) if m.get("id")]
        if not models:
            return False, "Image backend is up but advertises no image models."
        return True, f"{len(models)} model(s): {', '.join(models[:3])}"
    except asyncio.TimeoutError:
        return False, f"Image backend did not answer within {PROBE_TIMEOUT_S:.0f}s."
    except Exception as e:  # a probe must never take the page down
        return False, f"Image backend unreachable: {type(e).__name__}."


async def _probe_voice_backend() -> tuple[bool, str]:
    """Is the audio/voice service up, and how many profiles are enrolled?"""
    try:
        from services.config import ALPACA_AUDIO_URL

        if not ALPACA_AUDIO_URL:
            return False, "No audio backend configured. Set ALPACA_AUDIO_URL."
        import aiohttp

        async with aiohttp.ClientSession() as client:
            resp = await client.get(
                f"{ALPACA_AUDIO_URL}/health",
                timeout=aiohttp.ClientTimeout(total=PROBE_TIMEOUT_S),
            )
            if resp.status != 200:
                return False, f"Audio backend answered HTTP {resp.status}."
            # Enrolled count is best effort: the endpoint is allowed to be empty
            # or absent, and that is not a reason to call voice unavailable.
            try:
                vresp = await client.get(
                    f"{ALPACA_AUDIO_URL}/api/voices",
                    timeout=aiohttp.ClientTimeout(total=PROBE_TIMEOUT_S),
                )
                if vresp.status == 200:
                    body = await vresp.json()
                    profiles = body if isinstance(body, list) else (body or {}).get("profiles") or []
                    n = len(profiles)
                    return True, (
                        f"{n} enrolled voice profile(s)" if n else "Up, but no voice profiles enrolled yet"
                    )
            except Exception:
                pass
        return True, "Up; enrolled profile count unavailable."
    except asyncio.TimeoutError:
        return False, f"Audio backend did not answer within {PROBE_TIMEOUT_S:.0f}s."
    except Exception as e:
        return False, f"Audio backend unreachable: {type(e).__name__}."


async def collect_capabilities() -> list[dict]:
    """The full AI capability surface, for the Workspace and for Raven."""
    image_up, image_detail = await _probe_image_backend()
    voice_up, voice_detail = await _probe_voice_backend()

    caps: list[Capability] = [
        Capability(
            "image_edit",
            "Edit images",
            image_up,
            image_detail if image_up else image_detail,
        ),
        Capability(
            "face_preserve",
            "New image, same faces",
            image_up,
            (
                f"{image_detail} — identity is held by instruction, so the face is "
                f"approximated rather than preserved exactly"
                if image_up
                else image_detail
            ),
        ),
    ]

    # Two-image support cannot be asserted without attempting one, so this is
    # reported from what actually happened, and labelled as unverified until then.
    obs = _observation("face_swap")
    if obs is not None:
        swap = Capability("face_swap", "Swap a face between two photos", obs.available, obs.detail)
    else:
        stale = _stale_observation("face_swap")
        swap = Capability(
            "face_swap",
            "Swap a face between two photos",
            None,
            (
                f"Last seen: {stale.detail}"
                if stale
                else "Not confirmed yet. Many image backends accept only one source "
                "image, which makes a two-image swap impossible; try one and this "
                "will record whether it worked."
            ),
        )
    caps.append(swap)

    caps.append(Capability("voice_profiles", "Voice profiles", voice_up, voice_detail))

    # Stated rather than omitted: "not installed" is far more useful to a user
    # than a feature silently missing from a list.
    caps.append(
        Capability(
            "video_gen",
            "Generate video",
            False,
            "No video model is installed on this deployment.",
        )
    )
    return [asdict(c) for c in caps]

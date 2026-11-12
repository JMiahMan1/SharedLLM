"""HA's text-to-speech services, spoken with Jarvis's own voice.

HA spoke through its Piper add-on (``tts.speak`` with ``tts.piper``, or the
legacy ``tts.piper`` / ``tts.<engine>_say`` services) and handed the speaker a
URL on HA itself. The hub does the same with Jarvis's Kokoro engine
(execution's ``/execute/tts``): it keeps the audio for a few minutes behind an
unguessable ``/api/tts_proxy/<token>.wav`` URL and asks each media player to
play it. It never calls Jarvis's announce flow, which can itself fall back to
these services, so the two cannot loop.
"""
from __future__ import annotations

import base64
import logging
import os
import secrets
import time

import aiohttp

from services.homehub.core import DeviceUnavailable, Hub, HubError, ServiceCall

log = logging.getLogger("homehub.tts")

KEEP_S = 600.0
SYNTH_TIMEOUT_S = 60.0
DEFAULT_VOICE = "af_heart"
# Legacy per-engine services HA had; all speak the same way here.
LEGACY_SERVICES = ("piper", "google_translate_say", "cloud_say", "speak_piper")

_clips: dict[str, tuple[float, bytes]] = {}


def clip(token: str) -> bytes | None:
    """The audio behind a proxy token, while it is fresh."""
    now = time.monotonic()
    for t in [t for t, (exp, _) in _clips.items() if exp < now]:
        _clips.pop(t, None)
    item = _clips.get(token)
    return item[1] if item else None


def _base_url() -> str:
    """The hub's address as a speaker on the LAN sees it (never loopback: the speaker fetches the clip)."""
    explicit = os.getenv("HOMEHUB_EXTERNAL_URL")
    if explicit:
        return explicit.rstrip("/")
    host = os.getenv("EXECUTION_EXTERNAL_HOST")
    if not host:
        raise HubError("text to speech needs HOMEHUB_EXTERNAL_URL or EXECUTION_EXTERNAL_HOST (this host's LAN "
                       "address) so a speaker can fetch the spoken audio from the hub")
    return f"http://{host}:{os.getenv('HOMEHUB_PORT', '8123')}"


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise HubError(f"text to speech needs {name} (Jarvis's speech engine is reached through execution)")
    return value


async def synthesize(text: str, voice: str | None = None) -> bytes:
    url = _required_env("EXECUTION_SVC_URL").rstrip("/") + "/execute/tts"
    body = {"user_context": {"user": "household"}, "text": text, "voice": voice or DEFAULT_VOICE}
    headers = {"X-Internal-Secret": _required_env("INTERNAL_SECRET")}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=SYNTH_TIMEOUT_S)) as s, \
                s.post(url, json=body, headers=headers) as r:
            data = await r.json(content_type=None)
    except Exception as exc:
        raise DeviceUnavailable(f"Jarvis's speech engine did not answer: {exc}") from exc
    audio = (data.get("detail") or {}).get("audio_base64") if isinstance(data, dict) else None
    if not audio:
        raise HubError(f"speech synthesis failed: {(data or {}).get('message') if isinstance(data, dict) else data}")
    return base64.b64decode(audio)


async def make_clip(message: str, voice: str | None = None) -> str:
    """Speak ``message`` and keep it behind a fresh proxy URL; returns that URL."""
    if not message:
        raise HubError("tts needs a message")
    base = _base_url()  # before synthesis: a hub that cannot hand out a URL should not spend a minute speaking
    audio = await synthesize(message, voice)
    token = secrets.token_urlsafe(24)
    _clips[token] = (time.monotonic() + KEEP_S, audio)
    return f"{base}/api/tts_proxy/{token}.wav"


async def speak(hub: Hub, players: list[str], message: str, voice: str | None = None) -> str:
    if not message:
        raise HubError("tts needs a message")
    if not players:
        raise HubError("tts needs a media player to speak on")
    url = await make_clip(message, voice)
    await hub.call_service("media_player", "play_media", {
        "entity_id": players, "media_content_id": url, "media_content_type": "music", "announce": True})
    return url


async def tts_speak(hub: Hub, call: ServiceCall) -> None:
    """``tts.speak``: target the tts entity, name the speaker in ``media_player_entity_id``."""
    d = call.data
    players = d.get("media_player_entity_id") or []
    players = [players] if isinstance(players, str) else list(players)
    voice = (d.get("options") or {}).get("voice")
    await speak(hub, players, str(d.get("message") or ""), voice)


async def tts_legacy(hub: Hub, call: ServiceCall) -> None:
    """``tts.piper`` and friends: the media players are the target."""
    players = [e for e in call.entity_ids if e.startswith("media_player.")]
    voice = (call.data.get("options") or {}).get("voice")
    await speak(hub, players, str(call.data.get("message") or ""), voice)


def register(hub: Hub) -> None:
    hub.register_service("tts", "speak", tts_speak, {"media_player_entity_id": {}, "message": {}, "options": {}})
    for name in LEGACY_SERVICES:
        hub.register_service("tts", name, tts_legacy, {"entity_id": {}, "message": {}})


def reset() -> None:
    _clips.clear()


__all__ = ["clip", "make_clip", "register", "speak", "synthesize"]

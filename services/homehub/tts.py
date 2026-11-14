"""HA's text-to-speech services, spoken with Jarvis's own voice.

HA spoke through its Piper add-on (``tts.speak`` with ``tts.piper``, or the
legacy ``tts.piper`` / ``tts.<engine>_say`` services) and handed the speaker a
URL on HA itself. The hub does the same with Jarvis's Kokoro engine
(execution's ``/execute/tts``): it keeps the audio for a few minutes behind an
unguessable ``/api/tts_proxy/<token>.wav`` URL and asks each media player to
play it. It never calls Jarvis's announce flow, which can itself fall back to
these services, so the two cannot loop.

The engine is published as ``tts.jarvis`` (``TTS_ENTITY``), like HA's ``tts.piper``: its state is
when it last spoke, so the automation editor can offer it as ``tts.speak``'s target on a hub that
never imported one. ``tts.speak`` speaks with Jarvis whatever tts entity it names, so imported
automations naming ``tts.piper`` keep working. ``options.voice`` is any voice of Jarvis's Kokoro
engine (``/execute/tts/voices``); an unknown voice fails with the engine's error.

``options.voice`` may instead name a cloned voice: a profile on alpaca's audio server (OpenVoice), by its
id or its name. A Kokoro voice id is two letters, an underscore and a word (``af_heart``, a blend joins
them with commas); anything else is looked up among the profiles (``GET {ALPACA_WEB_URL}/api/audio/voices``)
and spoken through ``POST {ALPACA_WEB_URL}/api/audio/tts`` with ``clone`` set, so Kokoro speaks and
OpenVoice re-timbres it. ``ALPACA_WEB_URL`` is read once at start; unset, or a profile nobody enrolled,
fails the call and says which. ``voices()`` lists both kinds for the automation editor.
"""
from __future__ import annotations

import base64
import logging
import os
import re
import secrets
import time
from typing import Any

import aiohttp

from services.homehub.core import DeviceUnavailable, Hub, HubError, ServiceCall, utcnow

log = logging.getLogger("homehub.tts")

KEEP_S = 600.0
SYNTH_TIMEOUT_S = 60.0
DEFAULT_VOICE = "af_heart"
TTS_ENTITY = "tts.jarvis"
# Legacy per-engine services HA had; all speak the same way here.
LEGACY_SERVICES = ("piper", "google_translate_say", "cloud_say", "speak_piper")

#: A cloned voice's first sentence loads OpenVoice on the audio server; alpaca allows ten minutes.
CLONE_TIMEOUT_S = 300.0
VOICES_TIMEOUT_S = 15.0
#: Alpaca's dashboard, which bridges ``/api/audio/*`` to its audio server. Read once, at start.
ALPACA_WEB_URL = (os.getenv("ALPACA_WEB_URL") or "").rstrip("/")
#: A Kokoro voice id (``af_heart``, ``bm_fable``); a comma joins voices into a blend.
_KOKORO_VOICE = re.compile(r"^[a-z]{2}_[a-z0-9]+(,[a-z]{2}_[a-z0-9]+)*$")

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


def is_kokoro_voice(voice: str | None) -> bool:
    """No voice (the default) or a Kokoro voice id or blend; anything else names a cloned voice."""
    return not voice or bool(_KOKORO_VOICE.match(voice.replace(" ", "")))


def _alpaca_url() -> str:
    if not ALPACA_WEB_URL:
        raise HubError("a cloned voice is spoken by alpaca's audio server, and ALPACA_WEB_URL is not set "
                       "(set it in .env to alpaca's dashboard, e.g. http://<llm-host>:5000, and restart the hub)")
    return ALPACA_WEB_URL


async def _alpaca(method: str, path: str, timeout_s: float, body: dict[str, Any] | None = None) -> Any:
    url = _alpaca_url() + path
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout_s)) as s, \
                s.request(method, url, json=body) as r:
            data = await r.json(content_type=None)
            status = r.status
    except Exception as exc:
        raise DeviceUnavailable(f"alpaca's audio server did not answer: {exc}") from exc
    if status != 200:
        detail = data.get("error") if isinstance(data, dict) else data
        raise HubError(f"alpaca's audio server refused {path} ({status}): {detail}")
    return data


async def clone_profiles() -> list[dict[str, Any]]:
    """The cloned voices enrolled on alpaca's audio server, ``[{"id", "name"}]``."""
    data = await _alpaca("GET", "/api/audio/voices", VOICES_TIMEOUT_S)
    rows = data.get("voices") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        raise HubError("alpaca's audio server answered the voice list with something that is not a list")
    return [{"id": str(p["id"]), "name": str(p.get("name") or p["id"])}
            for p in rows if isinstance(p, dict) and p.get("id")]


async def resolve_clone(voice: str) -> dict[str, Any]:
    """The profile ``voice`` names, by id or by name (any case), or a HubError listing the ones there are."""
    profiles = await clone_profiles()
    want = voice.strip()
    for p in profiles:
        if p["id"] == want:
            return p
    named = [p for p in profiles if p["name"].casefold() == want.casefold()]
    if len(named) == 1:
        return named[0]
    known = ", ".join(f"{p['name']} ({p['id']})" for p in profiles) or "none are enrolled"
    if named:
        raise HubError(f"more than one cloned voice is called {want!r}; name it by id: {known}")
    raise HubError(f"{want!r} is neither a Kokoro voice nor a cloned voice on alpaca's audio server ({known})")


async def synthesize_clone(text: str, voice: str) -> bytes:
    profile = await resolve_clone(voice)
    data = await _alpaca("POST", "/api/audio/tts", CLONE_TIMEOUT_S, {"text": text, "clone": profile["id"]})
    audio = data.get("audio_b64") if isinstance(data, dict) else None
    if not audio:
        raise HubError(f"alpaca's audio server returned no audio for the cloned voice {profile['name']}")
    return base64.b64decode(audio)


async def synthesize(text: str, voice: str | None = None) -> bytes:
    """Speak ``text`` with a Kokoro voice (execution) or a cloned voice (alpaca's audio server)."""
    if not is_kokoro_voice(voice):
        return await synthesize_clone(text, str(voice))
    return await synthesize_kokoro(text, voice)


async def kokoro_voices() -> list[str]:
    url = _required_env("EXECUTION_SVC_URL").rstrip("/") + "/execute/tts/voices"
    headers = {"X-Internal-Secret": _required_env("INTERNAL_SECRET")}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=VOICES_TIMEOUT_S)) as s, \
                s.get(url, headers=headers) as r:
            data = await r.json(content_type=None)
    except Exception as exc:
        raise DeviceUnavailable(f"Jarvis's speech engine did not answer: {exc}") from exc
    names = data.get("voices") if isinstance(data, dict) else None
    if not isinstance(names, list):
        raise HubError(f"Jarvis's speech engine did not list its voices: {data}")
    return [str(v) for v in names]


async def voices() -> dict[str, Any]:
    """Every voice ``tts.speak`` takes in ``options.voice``, for the automation editor.

    ``{"voices": [{"id", "name", "kind": "kokoro"|"clone"}], "errors": {"kokoro"?, "clone"?}}``: a source
    that could not be listed is named in ``errors`` with why (ALPACA_WEB_URL unset, a service down), never
    left out without a word.
    """
    out: list[dict[str, Any]] = []
    errors: dict[str, str] = {}
    try:
        out.extend({"id": v, "name": v, "kind": "kokoro"} for v in await kokoro_voices())
    except HubError as exc:
        errors["kokoro"] = str(exc)
    try:
        out.extend({"id": p["id"], "name": p["name"], "kind": "clone"} for p in await clone_profiles())
    except HubError as exc:
        errors["clone"] = str(exc)
    return {"voices": out, "default": DEFAULT_VOICE, "errors": errors}


async def synthesize_kokoro(text: str, voice: str | None = None) -> bytes:
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
    _publish_engine(hub, utcnow().isoformat())
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


def _publish_engine(hub: Hub, state: str) -> None:
    hub.states.set(TTS_ENTITY, state, {"friendly_name": "Jarvis", "icon": "mdi:account-voice"})


def register(hub: Hub) -> None:
    if hub.states.get(TTS_ENTITY) is None:
        _publish_engine(hub, "unknown")
    hub.register_service("tts", "speak", tts_speak, {"media_player_entity_id": {}, "message": {}, "options": {}})
    for name in LEGACY_SERVICES:
        hub.register_service("tts", name, tts_legacy, {"entity_id": {}, "message": {}})


def reset() -> None:
    _clips.clear()


__all__ = ["clip", "make_clip", "register", "speak", "synthesize", "voices"]

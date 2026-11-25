"""The household's names for the Whisper speech-to-text servers, spoken as Home Assistant's names API.

Whisper biases towards the words in its ``initial_prompt``, and the Wyoming Whisper server
(rhasspy/wyoming-whisper 3.8.1) builds that prompt on every utterance from a Home Assistant
it is pointed at (``--hass-api``, ``--hass-token``): it opens ``<api>/websocket``, logs in,
and asks for the exposed entities, the states, the entity registry entries, the areas and
the floors. It fetches while the person is still speaking and waits at most a second
(``--hass-prompt-timeout``) at the end, so the names cost no latency, and it fits as many as
its own token budget allows (``--hass-prompt-max-tokens``, 200 by default), in the order of
its tiers: areas that hold something, then the entities of the domains people name out loud,
then the other areas, then everything else.

Wyoming's transcribe event has no per-request prompt in that version, so this is how the
satellites' speech to text gets the household's vocabulary. The vocabulary itself is built
by execution from the integrations (``services/execution/stt_vocabulary.py``); this route
only presents it in the shape the Whisper server reads:

* every name (artists, playlists, titles, family members) is a ``media_player`` entity, in
  the vocabulary's order (the server takes entities in entity id order), so they are the
  tier the server keeps first;
* the rooms are areas with nothing in them, the tier after;
* the players' names are ``sensor`` entities, the tier cut first.

Read-only: nothing here can change anything, and no other command is answered. The login is
a Jarvis API key (any member's; an admin makes one for the Whisper servers), and only a
caller inside the home network is served.
"""
from __future__ import annotations

import hashlib
import logging
import time
from typing import Any

import aiohttp
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from services.gateway import client_origin
from services.gateway.config import EXECUTION_SVC, IDENTITY_SVC, INTERNAL_SECRET

log = logging.getLogger("gateway.stt_vocabulary")

router = APIRouter()

#: The path the Whisper server opens: ``--hass-api=http://<gateway>/stt-vocabulary``.
PATH = "/stt-vocabulary/websocket"
#: What the server is told it is talking to (it only checks the handshake's message types).
HA_VERSION = "2026.11.0"
#: How long execution's vocabulary is kept between utterances.
VOCABULARY_TTL_S = 60.0
#: How long a checked API key is trusted before Identity is asked again.
TOKEN_TTL_S = 600.0

_vocabulary: tuple[float, dict[str, Any]] | None = None
_tokens: dict[str, float] = {}


def ha_answers(vocab: dict[str, Any]) -> dict[str, Any]:
    """The result of each names-API command the Whisper server sends, for ``vocab``."""
    names = [str(n) for n in vocab.get("names") or [] if n]
    rooms = [str(r) for r in vocab.get("rooms") or [] if r]
    devices = [str(d) for d in vocab.get("devices") or [] if d]
    entities = [(f"media_player.name_{i:03d}", n) for i, n in enumerate(names)]
    entities += [(f"sensor.player_{i:03d}", d) for i, d in enumerate(devices)]
    return {
        "homeassistant/expose_entity/list": {
            "exposed_entities": {eid: {"conversation": True} for eid, _ in entities}},
        "get_states": [{"entity_id": eid, "state": "idle", "attributes": {"friendly_name": name}}
                       for eid, name in entities],
        "config/entity_registry/get_entries": {
            eid: {"entity_id": eid, "name": None, "original_name": None, "aliases": [], "area_id": None,
                  "device_id": None, "disabled_by": None} for eid, _ in entities},
        "config/device_registry/list": [],
        "config/area_registry/list": [{"area_id": f"room_{i:03d}", "name": r, "aliases": [], "floor_id": None}
                                      for i, r in enumerate(rooms)],
        "config/floor_registry/list": [],
    }


async def _fetch_vocabulary() -> dict[str, Any]:
    """Execution's vocabulary, kept ``VOCABULARY_TTL_S``; the last one (or nothing) when it cannot be asked."""
    global _vocabulary
    now = time.monotonic()
    if _vocabulary and now - _vocabulary[0] < VOCABULARY_TTL_S:
        return _vocabulary[1]
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=3)) as session, session.get(
            f"{EXECUTION_SVC}/execute/voice/vocabulary", headers={"X-Internal-Secret": INTERNAL_SECRET},
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
        if isinstance(data, dict):
            _vocabulary = (now, data)
            return data
    except Exception as exc:
        log.warning(f"[stt-vocabulary] could not read the vocabulary from execution: {exc}")
    return _vocabulary[1] if _vocabulary else {}


async def _token_ok(token: str) -> bool:
    """Whether ``token`` is a Jarvis API key (Identity), remembered for ``TOKEN_TTL_S``."""
    if not token:
        return False
    key = hashlib.sha256(token.encode()).hexdigest()
    now = time.monotonic()
    if now - _tokens.get(key, -TOKEN_TTL_S) < TOKEN_TTL_S:
        return True
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session, session.get(
            f"{IDENTITY_SVC}/api/users/me", headers={"Authorization": f"Bearer {token}"},
        ) as resp:
            ok = resp.status == 200
    except Exception as exc:
        log.warning(f"[stt-vocabulary] could not check a speech-to-text server's key: {exc}")
        return False
    if ok:
        _tokens[key] = now
    return ok


@router.websocket(PATH)
async def stt_vocabulary_websocket(websocket: WebSocket) -> None:
    peer = websocket.client.host if websocket.client else None
    if not client_origin.is_inside(peer, websocket.headers.get("X-Forwarded-For")):
        log.warning("[stt-vocabulary] refused a names request from outside the home network")
        await websocket.close(code=1008)
        return
    await websocket.accept()
    try:
        await websocket.send_json({"type": "auth_required", "ha_version": HA_VERSION})
        auth = await websocket.receive_json()
        if not (isinstance(auth, dict) and auth.get("type") == "auth"
                and await _token_ok(str(auth.get("access_token") or ""))):
            log.warning("[stt-vocabulary] a speech-to-text server presented no valid Jarvis API key")
            await websocket.send_json({"type": "auth_invalid", "message": "Invalid access token"})
            await websocket.close(code=1008)
            return
        await websocket.send_json({"type": "auth_ok", "ha_version": HA_VERSION})
        answers = ha_answers(await _fetch_vocabulary())
        while True:
            msg = await websocket.receive_json()
            if not isinstance(msg, dict):
                continue
            mid, kind = msg.get("id"), msg.get("type")
            if kind in answers:
                await websocket.send_json({"id": mid, "type": "result", "success": True, "result": answers[kind]})
            else:
                await websocket.send_json({"id": mid, "type": "result", "success": False,
                                           "error": {"code": "unknown_command",
                                                     "message": f"{kind!r} is not offered here: names only"}})
    except WebSocketDisconnect:
        return


def reset() -> None:
    """Forget the cached vocabulary and keys (tests)."""
    global _vocabulary
    _vocabulary = None
    _tokens.clear()

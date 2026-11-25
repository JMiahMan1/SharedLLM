"""The offline family chat, back online: phones that talked over Bluetooth LE
with no Wi-Fi or data hand their messages to the family chat here.

Over the air (services/ui/android: FamilyMesh, MeshRules) every message is a
``sealed`` envelope, the same AES-256-GCM envelope as the private chat's
(services/common/sealed.py, PrivateEnvelope.java): a fresh 12-byte IV and the
ciphertext with its 16-byte tag, under the household's offline chat key, a
random 32-byte key geo keeps beside the proximity key
(``GET /api/geo/proximity/config`` hands it to signed-in family phones). Relay
phones and anyone listening see ciphertext only.

The family chat online is Nextcloud Talk, which is not end-to-end encrypted:
once a message is synced it is in Talk like every other family message, and
this service, which issues the key, can read it. That is the same as the
online family chat, and it is said so in the design.

* ``POST /api/family-mesh/sync`` - the author's phone, online again, sends the
  sealed messages it wrote. Each message id is claimed once (Redis SET NX), so
  however many times it is sent (a retry, a second device of the same person)
  it is posted to Talk once. Only the signed-in person's own messages are
  posted, as them; a relay phone never posts someone else's words.
* ``POST /api/family-mesh/synced`` - which ids are already in the family chat,
  so every phone can drop its offline copy and the thread shows each message
  once.
"""
from __future__ import annotations

import base64
import json
import logging
import re
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request

log = logging.getLogger(__name__)

router = APIRouter()

#: Claims live as long as a phone may keep an offline message, and some.
CLAIM_TTL_S = 30 * 86400
MAX_ITEMS = 100
TEXT_MAX = 2000
_ID = re.compile(r"[0-9a-f]{32}")
_TOKEN = re.compile(r"[A-Za-z0-9]{4,64}")


def claim_key(msg_id: str) -> str:
    return f"family_mesh:msg:{msg_id}"


def open_sealed(env: Any, key_b64: str) -> dict | None:
    """The payload inside a sealed envelope, or None when it does not open with the key."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if not isinstance(env, dict) or env.get("kind") != "sealed":
        return None
    try:
        iv = base64.b64decode(str(env.get("iv") or ""))
        ct = base64.b64decode(str(env.get("ct") or ""))
        if len(iv) != 12:
            return None
        plain = AESGCM(base64.b64decode(key_b64)).decrypt(iv, ct, None)
        payload = json.loads(plain.decode("utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _clock(sent_at: Any) -> str:
    try:
        return time.strftime("%-I:%M %p", time.localtime(float(sent_at)))
    except (TypeError, ValueError, OverflowError):
        return "earlier"


def talk_text(payload: dict) -> str | None:
    """What the family chat shows for an offline message, or None when it is not one."""
    kind = payload.get("t")
    when = _clock(payload.get("sent_at"))
    if kind == "text":
        text = str(payload.get("text") or "").strip()[:TEXT_MAX]
        return f"(sent offline at {when}) {text}" if text else None
    if kind == "pin":
        try:
            lat, lon = float(payload["lat"]), float(payload["lon"])
        except (KeyError, TypeError, ValueError):
            return None
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            return None
        label = " ".join(str(payload.get("label") or "").split())[:120]
        where = f"https://www.openstreetmap.org/?mlat={lat:.5f}&mlon={lon:.5f}#map=17/{lat:.5f}/{lon:.5f}"
        return f"(sent offline at {when}) Location{': ' + label if label else ''} {where}"
    if kind == "thumb":
        caption = " ".join(str(payload.get("caption") or "").split())[:300]
        return (f"(sent offline at {when}) A photo, as a small preview in Wander's offline chat"
                f"{': ' + caption if caption else ''}")
    return None


async def _chat_key(user: str) -> str:
    from services.gateway.main import _geo_proximity

    config = await _geo_proximity("get", "/proximity/config", params={"user": user})
    key = str(config.get("chat_key") or "")
    if not key:
        raise HTTPException(status_code=503, detail="The offline chat key is not available")
    return key


@router.post("/api/family-mesh/sync")
async def sync_offline_messages(request: Request):
    """Post the caller's own offline messages to the family chat, each once."""
    from services.gateway.main import _execution_post_as, _resolve_identity_from_request
    from services.gateway.notices import _redis

    creds = await _resolve_identity_from_request(request)
    me = str((creds or {}).get("user") or "").split(".")[-1].strip().lower()
    if not me:
        raise HTTPException(status_code=401, detail="Not authenticated")
    body = await request.json()
    items = body.get("items") if isinstance(body, dict) else None
    if not isinstance(items, list):
        raise HTTPException(status_code=422, detail="items must be a list")
    key = await _chat_key(me)
    results: dict[str, str] = {}
    async with _redis() as conn:
        for item in items[:MAX_ITEMS]:
            if not isinstance(item, dict):
                continue
            msg_id = str(item.get("id") or "").lower()
            if not _ID.fullmatch(msg_id):
                continue
            payload = open_sealed(item.get("env"), key)
            if payload is None:
                results[msg_id] = "unreadable"
                continue
            if str(payload.get("from") or "").lower() != me:
                # Someone else's words: only their own phone posts them, as them.
                results[msg_id] = "not_yours"
                continue
            room = str(payload.get("room") or "")
            text = talk_text(payload)
            if not _TOKEN.fullmatch(room) or text is None:
                results[msg_id] = "unreadable"
                continue
            if not await conn.set(claim_key(msg_id), "posting", nx=True, ex=CLAIM_TTL_S):
                state = await conn.get(claim_key(msg_id))
                results[msg_id] = "posted" if state == "posted" else "in_progress"
                continue
            posted = await _execution_post_as(creds, "/execute/talk",
                                              {"action": "send", "token": room, "message": text})
            if posted.status_code == 200:
                await conn.set(claim_key(msg_id), "posted", ex=CLAIM_TTL_S)
                results[msg_id] = "posted"
            else:
                # Give the claim back so the next try can post it.
                await conn.delete(claim_key(msg_id))
                results[msg_id] = "failed"
                log.warning(f"[family-mesh] could not post an offline message: HTTP {posted.status_code}")
    return {"results": results}


@router.post("/api/family-mesh/synced")
async def offline_messages_synced(request: Request):
    """Which of these offline message ids are already in the family chat."""
    from services.gateway.main import _require_authenticated
    from services.gateway.notices import _redis

    await _require_authenticated(request)
    body = await request.json()
    ids = [str(i).lower() for i in (body.get("ids") if isinstance(body, dict) else None) or [] if isinstance(i, str)]
    ids = [i for i in ids if _ID.fullmatch(i)][:500]
    if not ids:
        return {"synced": []}
    async with _redis() as conn:
        states = await conn.mget([claim_key(i) for i in ids])
    return {"synced": [i for i, s in zip(ids, states, strict=True) if s == "posted"]}

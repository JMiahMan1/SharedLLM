"""The offline chat, back online: phones that talked over Bluetooth LE with no
Wi-Fi or data hand their messages to the chat they were written in.

Every chat room the person is in works offline (Talk one-to-one and group
rooms; the parents' end-to-end private chats stay online-only, since the server
cannot post into them). Over the air (services/ui/android: FamilyMesh,
MeshRules) every message is a ``sealed`` envelope, the same AES-256-GCM envelope
as the private chat's (services/common/sealed.py, PrivateEnvelope.java): a fresh
12-byte IV and the ciphertext with its 16-byte tag.

Room keys
---------
Each room has its own random 32-byte key, issued here to each member who asks
(``GET /api/family-mesh/rooms``, fetched by the phone's mesh upkeep beside the
proximity config). Every family phone relays every family message (the HELLO is
still the household proximity key), but only a member of the room holds the key
that opens it. A relay sees an id, sizes, a hop count and a routing tag: an HMAC
of the room key, the period and the message id, so it cannot tell which room,
nor that two messages share one. A room's key is replaced as soon as its
member list changes; a member who stays keeps the previous keys for as long as a
message sealed with them may still be travelling (``PREVIOUS_KEEP_S``), a new
member never gets them, and someone removed gets nothing new.

The household chat key (``chat_key`` in the proximity config) is still used for
a parent's offline commands to a child's phone and their acknowledgements, and
to read the offline messages phones held before room keys (old scheme).

* ``POST /api/family-mesh/sync`` - the author's phone, online again, sends the
  sealed messages it wrote, each with its room. The author must be a member of
  that room (their own Talk room list says so), the envelope must open with that
  room's key and name the same room and the author, and each message id is
  claimed once (Redis SET NX), so a retry or a second device posts nothing
  twice. It is posted into that room as the author, marked "(sent offline at
  ...)". A relay phone never posts someone else's words.
* ``POST /api/family-mesh/synced`` - which ids are already in their chat, so
  every phone can drop its offline copy and the thread shows each message once.

Talk is not end-to-end encrypted: once synced, a message is in Talk like every
other message in that room, and this service, which issues the keys, can read
it. People outside the family in a room get the message when the author syncs.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
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

#: How long a phone keeps an offline message (MeshRules.TTL_S); a retired room key
#: stays with the members who had it this long, and an hour more, so what was
#: sealed with it before the change still opens.
MESSAGE_TTL_S = 72 * 3600
PREVIOUS_KEEP_S = MESSAGE_TTL_S + 3600
MAX_PREVIOUS = 4
#: A room's member list is asked of Talk at most this often (a change made in the
#: app drops it at once, see forget_members).
MEMBERS_CACHE_S = 300
#: How often a phone asks for its room keys again.
ROOMS_REFRESH_S = 900
#: Talk room types that work offline: one-to-one, group, public. Not the changelog.
_OFFLINE_TYPES = {1, 2, 3}


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
        return (f"(sent offline at {when}) A photo, as a small preview in the Jarvis app's offline chat"
                f"{': ' + caption if caption else ''}")
    return None


def room_key(token: str) -> str:
    return f"family_mesh:room:{token}"


def members_key(token: str) -> str:
    return f"family_mesh:members:{token}"


def _lock_key(token: str) -> str:
    return f"family_mesh:room_lock:{token}"


def new_key() -> str:
    return base64.b64encode(os.urandom(32)).decode()


def key_id(key_b64: str) -> str:
    """A short public name for a key (diagnostics only; never sent over the air)."""
    return hashlib.sha256(base64.b64decode(key_b64)).hexdigest()[:12]


def members_fingerprint(participants: list[dict]) -> str:
    """Who is in a room, as one value that changes whenever anyone joins or leaves."""
    actors = sorted({f"{p.get('actor_type') or 'users'}:{str(p.get('actor_id') or '').lower()}"
                     for p in participants if isinstance(p, dict) and p.get("actor_id")})
    return hashlib.sha256("\n".join(actors).encode()).hexdigest()


def route_tag(key_b64: str, period: int, msg_id: str) -> str:
    """The clear routing tag a phone puts beside a message (MeshRules.routeTag): only a
    holder of the room key can make or recognise it, and it differs per period and message."""
    mac = hmac.new(base64.b64decode(key_b64), f"jarvis-mesh-room-v1|{period}|{msg_id}".encode(), hashlib.sha256)
    return mac.hexdigest()[:32]


def advance(record: dict | None, fingerprint: str, user: str, now: float) -> tuple[dict, bool]:
    """A room's key record after ``user`` (a member) asks for it; True when it changed.

    A new room gets a key. A changed member list retires the key (kept, with who
    had it, for PREVIOUS_KEEP_S) and makes a new one. The asker is noted as
    holding the current key, which is what lets a previous key go only to the
    members who held it.
    """
    changed = False
    rec = dict(record or {})
    previous = [p for p in rec.get("previous") or [] if isinstance(p, dict)
                and float(p.get("retired") or 0) + PREVIOUS_KEEP_S > now]
    if len(previous) != len(rec.get("previous") or []):
        changed = True
    if not rec.get("key"):
        rec = {"key": new_key(), "members": fingerprint, "issued": [], "since": now}
        changed = True
    elif rec.get("members") != fingerprint:
        previous.insert(0, {"key": rec["key"], "issued": list(rec.get("issued") or []), "retired": now})
        rec = {"key": new_key(), "members": fingerprint, "issued": [], "since": now}
        changed = True
    rec["previous"] = previous[:MAX_PREVIOUS]
    issued = list(rec.get("issued") or [])
    if user not in issued:
        issued.append(user)
        changed = True
    rec["issued"] = sorted(issued)
    return rec, changed


def keys_for(record: dict | None, user: str, now: float) -> list[str]:
    """The keys of a room ``user`` may open with: the current one, then the previous
    ones they held (never one from before they joined)."""
    if not record or not record.get("key"):
        return []
    out = [str(record["key"])]
    for p in record.get("previous") or []:
        if (isinstance(p, dict) and user in (p.get("issued") or [])
                and float(p.get("retired") or 0) + PREVIOUS_KEEP_S > now and p.get("key")):
            out.append(str(p["key"]))
    return out


async def _load(conn, token: str) -> dict | None:
    raw = await conn.get(room_key(token))
    if not raw:
        return None
    try:
        rec = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return rec if isinstance(rec, dict) else None


async def forget_members(token: str | None) -> None:
    """A member was added or removed in the app: the next ask reads the new list (and rotates)."""
    from services.gateway.notices import _redis

    if not token:
        return
    try:
        async with _redis() as conn:
            await conn.delete(members_key(str(token)))
    except Exception as e:
        log.warning(f"[family-mesh] could not drop a room's member list: {e}")


def _detail(resp: Any) -> dict:
    try:
        body = json.loads(resp.body)
    except (AttributeError, TypeError, ValueError):
        return {}
    detail = body.get("detail") if isinstance(body, dict) else None
    return detail if isinstance(detail, dict) else {}


async def my_rooms(creds: Any) -> list[dict]:
    """The Talk rooms this person is in that work offline (not a private chat, not the changelog).

    Talk lists only the rooms the person is a member of, so this is also the membership check.
    """
    from services.gateway import private_chat
    from services.gateway.main import _execution_post_as

    resp = await _execution_post_as(creds, "/execute/talk", {"action": "list"})
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail="Could not read your chats")
    out = []
    for conv in _detail(resp).get("conversations") or []:
        if not isinstance(conv, dict):
            continue
        token = str(conv.get("token") or "")
        if not _TOKEN.fullmatch(token) or conv.get("type") not in _OFFLINE_TYPES or private_chat.is_private(token):
            continue
        out.append({"room": token, "name": str(conv.get("display_name") or token)[:120], "type": conv.get("type")})
    return out


async def _fingerprint(conn, creds: Any, token: str) -> str | None:
    from services.gateway.main import _execution_post_as

    cached = await conn.get(members_key(token))
    if cached:
        return str(cached)
    resp = await _execution_post_as(creds, "/execute/talk", {"action": "participants", "token": token})
    if resp.status_code != 200:
        return None
    participants = _detail(resp).get("participants")
    if not isinstance(participants, list) or not participants:
        return None
    fp = members_fingerprint(participants)
    await conn.set(members_key(token), fp, ex=MEMBERS_CACHE_S)
    return fp


async def _issue(conn, creds: Any, token: str, me: str, now: float) -> dict | None:
    """This member's view of a room's key record, rotating it first when its members changed."""
    fp = await _fingerprint(conn, creds, token)
    if fp is None:
        # Talk could not say who is in it: hand out what there is, change nothing.
        return await _load(conn, token)
    # One writer at a time per room, so two members asking at once never make two keys.
    for _ in range(20):
        if await conn.set(_lock_key(token), "1", nx=True, ex=10):
            break
        await asyncio.sleep(0.1)
    else:
        return await _load(conn, token)
    try:
        rec, changed = advance(await _load(conn, token), fp, me, now)
        if changed:
            await conn.set(room_key(token), json.dumps(rec))
        return rec
    finally:
        await conn.delete(_lock_key(token))


async def _me(request: Request) -> tuple[Any, str]:
    from services.gateway.main import _resolve_identity_from_request

    creds = await _resolve_identity_from_request(request)
    me = str((creds or {}).get("user") or "").split(".")[-1].strip().lower()
    if not me:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return creds, me


@router.get("/api/family-mesh/rooms")
async def offline_room_keys(request: Request):
    """Each room this person is in, with the key its offline messages are sealed with."""
    from services.gateway.notices import _redis

    creds, me = await _me(request)
    rooms = await my_rooms(creds)
    now = time.time()
    out = []
    async with _redis() as conn:
        for room in rooms:
            rec = await _issue(conn, creds, room["room"], me, now)
            keys = keys_for(rec, me, now)
            if not keys:
                continue
            out.append({**room, "key": keys[0], "key_id": key_id(keys[0]), "previous": keys[1:]})
    return {"rooms": out, "refresh_s": ROOMS_REFRESH_S}


async def _chat_key(user: str) -> str:
    """The household chat key: the scheme before room keys (messages phones still hold)."""
    from services.gateway.main import _geo_proximity

    config = await _geo_proximity("get", "/proximity/config", params={"user": user})
    key = str(config.get("chat_key") or "")
    if not key:
        raise HTTPException(status_code=503, detail="The offline chat key is not available")
    return key


@router.post("/api/family-mesh/sync")
async def sync_offline_messages(request: Request):
    """Post the caller's own offline messages, each into its own room, each once."""
    from services.gateway.main import _execution_post_as
    from services.gateway.notices import _redis

    creds, me = await _me(request)
    body = await request.json()
    items = body.get("items") if isinstance(body, dict) else None
    if not isinstance(items, list):
        raise HTTPException(status_code=422, detail="items must be a list")
    items = [i for i in items[:MAX_ITEMS] if isinstance(i, dict) and _ID.fullmatch(str(i.get("id") or "").lower())]
    if not items:
        return {"results": {}}
    member_of = {r["room"] for r in await my_rooms(creds)}
    household: str | None = None
    now = time.time()
    results: dict[str, str] = {}
    async with _redis() as conn:
        for item in items:
            msg_id = str(item.get("id")).lower()
            hint = str(item.get("room") or "")
            if hint:
                # Sealed with the room's key: it must open with a key this author held for that room.
                rec = await _load(conn, hint) if _TOKEN.fullmatch(hint) else None
                payload = None
                for key in keys_for(rec, me, now):
                    payload = open_sealed(item.get("env"), key)
                    if payload is not None:
                        break
            else:
                # Held since before room keys: the household chat key.
                if household is None:
                    household = await _chat_key(me)
                payload = open_sealed(item.get("env"), household)
            if payload is None:
                results[msg_id] = "unreadable"
                continue
            if str(payload.get("from") or "").lower() != me:
                # Someone else's words: only their own phone posts them, as them.
                results[msg_id] = "not_yours"
                continue
            room = str(payload.get("room") or "")
            text = talk_text(payload)
            if not _TOKEN.fullmatch(room) or text is None or (hint and room != hint):
                results[msg_id] = "unreadable"
                continue
            if room not in member_of:
                results[msg_id] = "not_member"
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
    """Which of these offline message ids are already in their chat."""
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

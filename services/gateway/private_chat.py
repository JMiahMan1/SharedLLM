"""Private chat: an end-to-end encrypted conversation between the two parents.

Like the "Between" couples app, but inside the family chat. Only the parents
(family role ``father`` or ``mother``, never a minor) can start one or be in
one, and only they can read it:

* **The key never leaves a parent's device.** Every chat has a 32-byte data
  key (DK) that encrypts its messages and pictures in the browser
  (services/ui/src/lib/privateChatCrypto.ts). This service keeps a *verifier*
  (the AES-GCM encryption of a fixed constant under DK, which never changes),
  so a wrong password is caught on the device without the server learning
  anything about it. A forgotten password means the history is gone: there is
  nothing here to recover it with.
* **Two record versions.** A *v1* chat (the first ones made) has no wrapped
  key: DK is PBKDF2-SHA256(password, ``salt``, ``iterations``) itself, so its
  password could never change. A *v2* chat keeps ``kdf`` {salt, iterations},
  ``wrapped_key`` {iv, ct} and ``auth_hash``: the device derives 512 bits with
  PBKDF2 from the password and ``kdf``; the first 256 are a key-encryption key
  (KEK) that only wraps DK (``wrapped_key`` = AES-GCM(KEK, DK)), the second 256
  are ``auth``, and the server stores only sha256(auth). New chats are v2.
* **Changing the password** (``POST /chats/<token>/password``, either parent)
  re-wraps the same DK under a new KEK, so nothing already sent is re-encrypted
  and the verifier stays the same (a change that alters it is refused). A v2
  chat must prove the current password with its old ``auth``; a v1 chat has no
  proof to give, so a member's change is accepted as long as the verifier is
  unchanged, and the chat becomes v2. Changes are limited to
  ``PASSWORD_CHANGE_LIMIT`` attempts per chat per ``PASSWORD_CHANGE_WINDOW_S``,
  and the other parent gets a notice with no content in it.
* **Rolling back a bad change (admin, by hand).** Each change pushes the
  record's previous key fields onto ``history`` (newest last, the last
  ``PASSWORD_HISTORY_KEEP`` kept), with ``replaced_at`` and ``replaced_by``.
  To undo the latest change, stop nothing: copy the last ``history`` entry's
  fields (``version`` and, for v1, ``salt``/``iterations``; for v2, ``kdf``,
  ``wrapped_key`` and ``auth_hash``; plus ``verifier``) back over the top-level
  ones in ``chats/<token>.json`` (dropping the v2 fields when restoring v1),
  remove that entry, and save the file. The old password works again at once.
  Because DK never changes, the history holds nothing that could decrypt a
  message without a password.
* **Messages ride on Talk as ciphertext.** Each is a ``sealed`` envelope
  (services/common/sealed.py) with a fresh IV. The Talk routes refuse anything
  else in a private room (``guard_talk``): plain text, Talk file shares, polls,
  games, cards, reactions, and adding members.
* **Pictures never touch Nextcloud.** They are encrypted on the device and kept
  here as opaque blobs (``PRIVATE_CHAT_DIR``), served only to the chat's
  members, so Nextcloud never makes a preview and nothing scans them.
* **Notices carry no content**: "New private message from Sam" (chat_notify),
  "Sam nudged you in your private chat" (``nudge``) and "Sam changed the
  private chat password" (``change_password``).

The registry is one JSON file per chat (``chats/<token>.json``); pictures are
``blobs/<token>/<id>``. Both are ciphertext or public parameters only.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import shutil
import time
from pathlib import Path
from typing import Any

import aiohttp
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from services.common import notify
from services.common.sealed import SEALED_LINE, is_sealed

log = logging.getLogger("gateway.private_chat")

router = APIRouter(prefix="/api/private-chat")

#: Where the registry and the encrypted pictures live (a compose volume). Read once at start.
PRIVATE_CHAT_DIR = Path(os.getenv("PRIVATE_CHAT_DIR", "/app/data/private_chat"))
#: The family roles that may start or be in a private chat.
PARENT_ROLES = frozenset({"father", "mother"})
#: The weakest key stretch accepted (OWASP's 2023 figure for PBKDF2-HMAC-SHA256).
MIN_ITERATIONS = 600_000
#: A ceiling, so a client cannot store a count that would hang the other parent's phone.
MAX_ITERATIONS = 10_000_000
SALT_BYTES = 16
IV_BYTES = 12
#: AES-GCM's tag; a ciphertext is never shorter than this.
TAG_BYTES = 16
#: One encrypted picture: the 25 MB attachment limit plus its IV and tag.
MAX_BLOB_BYTES = 25 * 1024 * 1024 + IV_BYTES + TAG_BYTES
#: The data key and the password-derived ``auth``, in bytes.
KEY_BYTES = 32
#: A wrapped data key: AES-GCM of the 32-byte key, so the key plus its tag.
WRAPPED_KEY_BYTES = KEY_BYTES + 16
#: Password changes (attempts, including refused ones) per chat in a window.
PASSWORD_CHANGE_LIMIT = 5
PASSWORD_CHANGE_WINDOW_S = 3600
#: Previous key records kept on a chat, so an admin can undo a bad change by hand.
PASSWORD_HISTORY_KEEP = 5
#: The record fields a password change replaces (and ``history`` keeps).
_KEY_FIELDS = ("version", "salt", "iterations", "kdf", "wrapped_key", "auth_hash", "verifier")
#: One nudge per chat per sender in this many seconds.
NUDGE_INTERVAL_S = 300
#: The Talk room's name, which is all Nextcloud ever shows about the chat.
ROOM_NAME = "Private chat"

_TOKEN = re.compile(r"^[A-Za-z0-9]{4,64}$")
_BLOB_ID = re.compile(r"^[0-9a-f]{32}$")
_USER = re.compile(r"^[A-Za-z0-9_.@-]{1,64}$")

#: Talk actions a private room allows through the generic Talk routes.
#: ``send``/``edit_message`` only with a sealed envelope (checked in guard_talk).
_ALLOWED_ACTIONS = frozenset({
    "list", "open", "messages", "wait", "mark_read", "delete_message", "delete_conversation",
    "participants", "mentions", "call_join", "call_leave", "send", "edit_message",
})

#: The only error text anyone outside the feature can reach: it names nothing.
NOT_AVAILABLE = "Not available."

_create_lock = asyncio.Lock()
_password_lock = asyncio.Lock()


# --- Store --------------------------------------------------------------------


class Store:
    """The registry and the encrypted pictures under one directory."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def _chat_path(self, token: str) -> Path:
        if not _TOKEN.match(token or ""):
            raise HTTPException(status_code=404, detail=NOT_AVAILABLE)
        return self.root / "chats" / f"{token}.json"

    def _blob_dir(self, token: str) -> Path:
        self._chat_path(token)  # validates the token
        return self.root / "blobs" / token

    def get(self, token: str) -> dict[str, Any] | None:
        if not _TOKEN.match(token or ""):
            return None
        path = self.root / "chats" / f"{token}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text())

    def tokens(self) -> set[str]:
        folder = self.root / "chats"
        if not folder.is_dir():
            return set()
        return {p.stem for p in folder.glob("*.json")}

    def all(self) -> list[dict[str, Any]]:
        return [c for c in (self.get(t) for t in sorted(self.tokens())) if c]

    def save(self, chat: dict[str, Any]) -> None:
        path = self._chat_path(chat["token"])
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(chat, sort_keys=True))
        os.replace(tmp, path)

    def remove(self, token: str) -> None:
        path = self._chat_path(token)
        path.unlink(missing_ok=True)
        shutil.rmtree(self._blob_dir(token), ignore_errors=True)

    def put_blob(self, token: str, data: bytes) -> str:
        folder = self._blob_dir(token)
        folder.mkdir(parents=True, exist_ok=True)
        blob_id = secrets.token_hex(16)
        tmp = folder / f"{blob_id}.tmp"
        tmp.write_bytes(data)
        os.replace(tmp, folder / blob_id)
        return blob_id

    def blob_path(self, token: str, blob_id: str) -> Path:
        if not _BLOB_ID.match(blob_id or ""):
            raise HTTPException(status_code=404, detail="No such picture.")
        return self._blob_dir(token) / blob_id


_store: Store | None = None


def get_store() -> Store:
    global _store
    if _store is None:
        _store = Store(PRIVATE_CHAT_DIR)
    return _store


def private_tokens() -> set[str]:
    """Every private chat's Talk token (for notices and the @Jarvis poller)."""
    return get_store().tokens()


def members(token: str | None) -> list[str]:
    """The chat's two members (empty for anything that is not a private chat)."""
    chat = get_store().get(str(token or ""))
    return [str(m) for m in chat.get("members", [])] if chat else []


def is_private(token: str | None) -> bool:
    return bool(token) and get_store().get(str(token)) is not None


# --- Who is who ---------------------------------------------------------------


async def _people() -> dict[str, dict[str, Any]]:
    """Every account by username, from identity. Fails loudly when identity cannot answer."""
    from services.gateway.main import IDENTITY_SVC, INTERNAL_SECRET, shared_http_client

    try:
        async with shared_http_client() as client:
            resp = await client.get(
                f"{IDENTITY_SVC}/api/users",
                headers={"X-Internal-Secret": INTERNAL_SECRET},
                timeout=aiohttp.ClientTimeout(total=10.0),
            )
            users = await resp.json(content_type=None) if resp.status == 200 else None
    except (TimeoutError, aiohttp.ClientError) as exc:
        log.warning("[private-chat] identity unreachable: %s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Could not check who is in the family; try again.") from exc
    if not isinstance(users, list):
        raise HTTPException(status_code=503, detail="Could not check who is in the family; try again.")
    return {str(u["username"]): u for u in users if isinstance(u, dict) and u.get("username")}


def is_parent(person: dict[str, Any] | None) -> bool:
    """A parent who may be in a private chat: father or mother, never a minor, with a chat account."""
    return bool(
        person
        and person.get("family_role") in PARENT_ROLES
        and not person.get("is_minor")
        and not person.get("is_system_default")
        and person.get("nextcloud_user")
    )


def _display(person: dict[str, Any]) -> str:
    return str(person.get("display_name") or person.get("username") or "Your partner")


async def _caller(request: Request) -> tuple[str, dict[str, dict[str, Any]]]:
    """The signed-in username and the family directory."""
    from services.gateway.main import _resolve_identity_from_request

    ident = await _resolve_identity_from_request(request)
    user = str((ident or {}).get("user") or "")
    if not user:
        raise HTTPException(status_code=401, detail="Sign in first.")
    return user, await _people()


async def _member_chat(request: Request, token: str) -> tuple[str, dict[str, Any], dict[str, dict[str, Any]]]:
    """The caller, the chat and the directory, for a member who is (still) a parent.

    A chat the caller is not in answers 404, as if it did not exist, and
    nothing the caller can reach names the feature.
    """
    user, people = await _caller(request)
    chat = get_store().get(token)
    if not chat or user not in chat.get("members", []):
        raise HTTPException(status_code=404, detail=NOT_AVAILABLE)
    if not is_parent(people.get(user)):
        raise HTTPException(status_code=403, detail=NOT_AVAILABLE)
    return user, chat, people


# --- Crypto parameters (public; checked for shape only) -----------------------


def _b64(value: Any, field: str) -> bytes:
    if not isinstance(value, str) or not value:
        raise HTTPException(status_code=422, detail=f"{field} is required (base64).")
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"{field} is not valid base64.") from exc


def _iterations(value: Any, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not MIN_ITERATIONS <= value <= MAX_ITERATIONS:
        raise HTTPException(status_code=422, detail=f"{field} must be a whole number from {MIN_ITERATIONS} to {MAX_ITERATIONS}.")
    return value


def _sealed(value: Any, field: str, *, ct_len: tuple[int, int]) -> dict[str, str]:
    """An AES-GCM ``{iv, ct}`` pair, checked for shape only."""
    if not isinstance(value, dict):
        raise HTTPException(status_code=422, detail=f"{field} is required: {{iv, ct}}.")
    iv = _b64(value.get("iv"), f"{field}.iv")
    ct = _b64(value.get("ct"), f"{field}.ct")
    low, high = ct_len
    if len(iv) != IV_BYTES or not low <= len(ct) <= high:
        raise HTTPException(status_code=422, detail=f"{field} is not an AES-GCM encryption of the right size.")
    return {"iv": value["iv"], "ct": value["ct"]}


def _verifier(value: Any) -> dict[str, str]:
    return _sealed(value, "verifier", ct_len=(TAG_BYTES + 1, 256))


def _auth(value: Any, field: str) -> bytes:
    raw = _b64(value, field)
    if len(raw) != KEY_BYTES:
        raise HTTPException(status_code=422, detail=f"{field} must be {KEY_BYTES} bytes.")
    return raw


def _auth_hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _wrapping(body: dict[str, Any]) -> dict[str, Any]:
    """A v2 key record from a request body: the KEK's ``kdf``, the ``wrapped_key`` and the verifier."""
    kdf = body.get("kdf")
    if not isinstance(kdf, dict):
        raise HTTPException(status_code=422, detail="kdf is required: {salt, iterations}. Update Jarvis and try again.")
    salt = _b64(kdf.get("salt"), "kdf.salt")
    if len(salt) != SALT_BYTES:
        raise HTTPException(status_code=422, detail=f"kdf.salt must be {SALT_BYTES} bytes.")
    iterations = _iterations(kdf.get("iterations"), "kdf.iterations")
    wrapped = _sealed(body.get("wrapped_key"), "wrapped_key", ct_len=(WRAPPED_KEY_BYTES, WRAPPED_KEY_BYTES))
    return {
        "version": 2,
        "kdf": {"salt": kdf["salt"], "iterations": iterations},
        "wrapped_key": wrapped,
        "verifier": _verifier(body.get("verifier")),
    }


def _key_fields(chat: dict[str, Any]) -> dict[str, Any]:
    return {k: chat[k] for k in _KEY_FIELDS if k in chat}


def _version(chat: dict[str, Any]) -> int:
    return 2 if chat.get("version") == 2 else 1


def _public(chat: dict[str, Any], me: str, people: dict[str, dict[str, Any]]) -> dict[str, Any]:
    partner = next((m for m in chat["members"] if m != me), "")
    return {
        "token": chat["token"],
        "members": chat["members"],
        "partner": partner,
        "partner_name": _display(people.get(partner) or {"username": partner}),
        "created_by": chat["created_by"],
        "created_at": chat["created_at"],
        **_public_key(chat),
    }


def _public_key(chat: dict[str, Any]) -> dict[str, Any]:
    """What a device needs to unlock: never ``auth_hash`` or ``history``."""
    if _version(chat) == 2:
        return {
            "version": 2,
            "kdf": {"name": "PBKDF2-SHA256", **chat["kdf"]},
            "wrapped_key": chat["wrapped_key"],
            "verifier": chat["verifier"],
        }
    return {
        "version": 1,
        "kdf": {"name": "PBKDF2-SHA256", "salt": chat["salt"], "iterations": chat["iterations"]},
        "verifier": chat["verifier"],
    }


# --- Talk ---------------------------------------------------------------------


async def _talk(request: Request, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """One /execute/talk call as the caller: its status and body."""
    from services.gateway.main import _proxy_execution_with_identity

    resp = await _proxy_execution_with_identity(request, "/execute/talk", payload)
    try:
        body = json.loads(resp.body)
    except (TypeError, ValueError):
        body = {}
    return resp.status_code, body if isinstance(body, dict) else {}


def guard_talk(token: str | None, action: str, message: str | None = None) -> None:
    """Refuse a generic Talk action that would put plain content into a private chat.

    Called by every Talk route in main.py that writes to a room (and the push
    Reply), so neither the app nor a stale client nor a notification's inline
    reply can post readable text, a Nextcloud file share, a poll, a reaction or
    a new member into a private room.
    """
    if not token or not is_private(token):
        return
    if action not in _ALLOWED_ACTIONS:
        raise HTTPException(status_code=403, detail=NOT_AVAILABLE)
    if action in ("send", "edit_message") and not is_sealed(message):
        # Generic on purpose: someone outside the chat who knows its token learns nothing.
        raise HTTPException(status_code=403, detail=NOT_AVAILABLE)


def scrub_conversations(content: dict[str, Any]) -> dict[str, Any]:
    """Mark private chats in a conversation list and drop their last message.

    The last message is ciphertext anyway; the list says "Encrypted message"
    and a client draws a lock, and the preview never reaches a search box.
    """
    detail = content.get("detail") if isinstance(content, dict) else None
    conversations = detail.get("conversations") if isinstance(detail, dict) else None
    if not isinstance(conversations, list):
        return content
    tokens = private_tokens()
    for conv in conversations:
        if not isinstance(conv, dict):
            continue
        if conv.get("token") in tokens or is_sealed(conv.get("last_message")):
            conv["private"] = True
            conv["last_message"] = SEALED_LINE
            conv["description"] = None
    return content


# --- Routes -------------------------------------------------------------------


@router.get("/status")
async def status(request: Request):
    """The caller's private chats and who they could start one with.

    Anyone who may not use the feature (not a father or mother, a minor, no
    chat account) gets exactly ``{"eligible": false, "chats": [], "partners": []}``:
    no reason and nothing else that says the feature exists.
    """
    user, people = await _caller(request)
    me = people.get(user)
    if not is_parent(me):
        return {"eligible": False, "chats": [], "partners": []}
    chats = [_public(c, user, people) for c in get_store().all() if user in c.get("members", [])]
    partners = [
        {"username": name, "display_name": _display(p)}
        for name, p in sorted(people.items())
        if name != user and is_parent(p)
    ]
    return {"eligible": True, "chats": chats, "partners": partners, "min_iterations": MIN_ITERATIONS}


@router.post("/chats")
async def create_chat(request: Request):
    """Start a private chat with the other parent.

    The body is a v2 key record the creator's device made: the KEK's ``kdf``,
    the ``wrapped_key``, the ``verifier`` and ``auth`` (stored only as its
    sha256). The password and the data key never come here.
    """
    try:
        body = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="A JSON body is required.") from exc
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="A JSON body is required.")
    user, people = await _caller(request)
    me = people.get(user)
    if not is_parent(me):
        raise HTTPException(status_code=403, detail=NOT_AVAILABLE)
    partner = str(body.get("partner") or "")
    if not _USER.match(partner) or partner == user:
        raise HTTPException(status_code=422, detail="Choose the other parent.")
    other = people.get(partner)
    if not is_parent(other):
        raise HTTPException(status_code=403, detail=NOT_AVAILABLE)
    params = _wrapping(body)
    params["auth_hash"] = _auth_hash(_auth(body.get("auth"), "auth"))
    members = sorted([user, partner])

    async with _create_lock:
        existing = next((c for c in get_store().all() if sorted(c.get("members", [])) == members), None)
        if existing:
            return JSONResponse(status_code=409, content={
                "detail": "You already have a private chat together.", "token": existing["token"]})
        status_code, result = await _talk(request, {
            "action": "create_group", "message": ROOM_NAME, "participants": [other["nextcloud_user"]],
        })
        detail = result.get("detail") if isinstance(result.get("detail"), dict) else {}
        token = str(((detail.get("conversation") or {}).get("token")) or "")
        if status_code != 200 or result.get("status") != "SUCCESS" or not _TOKEN.match(token):
            raise HTTPException(status_code=502, detail=result.get("message") or "Could not create the chat room.")
        if detail.get("not_added"):
            await _talk(request, {"action": "delete_conversation", "token": token})
            raise HTTPException(status_code=502, detail=f"Could not add {_display(other)} to the chat.")
        chat = {
            "token": token,
            "members": members,
            "created_by": user,
            "created_at": int(time.time()),
            **params,
        }
        try:
            get_store().save(chat)
        except OSError as exc:
            log.error("[private-chat] could not save the registry for %s: %s", token, exc)
            await _talk(request, {"action": "delete_conversation", "token": token})
            raise HTTPException(status_code=500, detail="Could not save the private chat (PRIVATE_CHAT_DIR not writable).") from exc
    log.info("[private-chat] %s started a private chat with %s (%s)", user, partner, token)
    return _public(chat, user, people)


@router.get("/chats/{token}")
async def get_chat(token: str, request: Request):
    user, chat, people = await _member_chat(request, token)
    return _public(chat, user, people)


@router.delete("/chats/{token}")
async def delete_chat(token: str, request: Request):
    """Delete the chat for both parents: the Talk room, the registry and every picture."""
    await _member_chat(request, token)
    return await delete_room(request, token)


async def delete_room(request: Request, token: str) -> dict[str, Any]:
    """Delete the Talk room as the caller; on success forget the chat and its pictures."""
    status_code, result = await _talk(request, {"action": "delete_conversation", "token": token})
    detail = result.get("detail") if isinstance(result.get("detail"), dict) else {}
    if status_code != 200 or result.get("status") != "SUCCESS":
        raise HTTPException(status_code=502, detail=result.get("message") or "Could not delete the chat.")
    if detail.get("deleted"):
        get_store().remove(token)
        log.info("[private-chat] %s deleted", token)
    return {"status": "SUCCESS", "message": result.get("message") or "Deleted.", "detail": detail}


@router.post("/chats/{token}/blobs")
async def put_blob(token: str, request: Request):
    """Keep one encrypted picture (raw bytes: IV then AES-GCM ciphertext)."""
    await _member_chat(request, token)
    data = await request.body()
    if len(data) <= IV_BYTES + TAG_BYTES:
        raise HTTPException(status_code=422, detail="That is not an encrypted picture.")
    if len(data) > MAX_BLOB_BYTES:
        raise HTTPException(status_code=413, detail="Pictures are limited to 25 MB.")
    blob_id = await asyncio.to_thread(get_store().put_blob, token, data)
    return {"id": blob_id, "size": len(data)}


@router.get("/chats/{token}/blobs/{blob_id}")
async def get_blob(token: str, blob_id: str, request: Request):
    await _member_chat(request, token)
    path = get_store().blob_path(token, blob_id)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="No such picture.")
    data = await asyncio.to_thread(path.read_bytes)
    return Response(content=data, media_type="application/octet-stream",
                    headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


@router.delete("/chats/{token}/blobs/{blob_id}")
async def delete_blob(token: str, blob_id: str, request: Request):
    await _member_chat(request, token)
    get_store().blob_path(token, blob_id).unlink(missing_ok=True)
    return {"deleted": blob_id}


def _claim_nudge(key: str) -> int:
    """0 when this nudge may go; otherwise seconds until the next one may."""
    from services.gateway.cache import get_redis

    r = get_redis()
    if r.set(key, "1", nx=True, ex=NUDGE_INTERVAL_S):
        return 0
    ttl = r.ttl(key)
    return int(ttl) if isinstance(ttl, int) and ttl > 0 else NUDGE_INTERVAL_S


def _release_nudge(key: str) -> None:
    from services.gateway.cache import get_redis

    get_redis().delete(key)


@router.post("/chats/{token}/nudge")
async def nudge(token: str, request: Request):
    """Ping the other parent about a message of yours they have not read.

    The notice says who nudged and nothing else. Only while your latest message
    is unread, and once per ``NUDGE_INTERVAL_S`` per chat per sender (429 with
    Retry-After otherwise).
    """
    user, chat, people = await _member_chat(request, token)
    me = people[user]
    status_code, result = await _talk(request, {"action": "open", "token": token})
    conv = ((result.get("detail") or {}).get("conversation")) if isinstance(result.get("detail"), dict) else None
    if status_code != 200 or not isinstance(conv, dict):
        raise HTTPException(status_code=502, detail=result.get("message") or "Could not read the chat.")
    last_id = int(conv.get("last_message_id") or 0)
    read_up_to = int(conv.get("last_common_read") or 0)
    mine = str(conv.get("last_message_actor_id") or "").lower() == str(me.get("nextcloud_user") or "").lower()
    if not last_id or not mine or read_up_to >= last_id:
        raise HTTPException(status_code=409, detail="Nothing of yours is waiting to be read.")

    key = f"private-chat:nudge:{token}:{user}"
    try:
        wait = await asyncio.to_thread(_claim_nudge, key)
    except Exception as exc:
        log.warning("[private-chat] nudge limit unavailable: %s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Nudges are unavailable right now; try again.") from exc
    if wait:
        return JSONResponse(
            status_code=429,
            content={"detail": f"You can nudge again in {max(1, round(wait / 60))} min.", "retry_after": wait},
            headers={"Retry-After": str(wait)},
        )

    others = [m for m in chat["members"] if m != user]
    url = f"/family?chat={token}"
    sent = await notify.send_many(
        others,
        lambda person: notify.notice(
            # Its own category: the phones give a nudge its own sound and a
            # heartbeat vibration (Android channel "Nudges"), and an open
            # Jarvis shows it full screen, so it does not pass as one more message.
            person, "nudge", "Private chat", f"{_display(me)} nudged you in your private chat",
            url=url, priority=4, tag=f"private-nudge:{token}", kind="nudge",
            actions=[notify.view_action(url)],
            data={"token": token, "private": True, "nudge": True},
        ),
    )
    if not sent:
        await asyncio.to_thread(_release_nudge, key)
        raise HTTPException(status_code=502, detail="The nudge could not be delivered; try again.")
    log.info("[private-chat] %s nudged %s", user, ", ".join(others))
    return {"nudged": others, "next_in": NUDGE_INTERVAL_S}


def _recent_attempts(chat: dict[str, Any], now: float) -> list[float]:
    stamps = chat.get("password_change_attempts")
    if not isinstance(stamps, list):
        return []
    return [float(t) for t in stamps if isinstance(t, (int, float)) and now - float(t) < PASSWORD_CHANGE_WINDOW_S]


@router.post("/chats/{token}/password")
async def change_password(token: str, request: Request):
    """Re-wrap the chat's data key under a new password (either parent).

    The body is a v2 key record made on the device (``kdf``, ``wrapped_key``,
    ``verifier``), the new ``new_auth`` and, for a v2 chat, the current
    ``auth``. The verifier must be the one already stored: the data key, and so
    every message and picture already sent, stays the same.
    """
    try:
        body = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="A JSON body is required.") from exc
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="A JSON body is required.")
    user, _chat, people = await _member_chat(request, token)
    record = _wrapping(body)
    new_auth = _auth(body.get("new_auth"), "new_auth")
    old_auth = body.get("auth")

    async with _password_lock:
        store = get_store()
        chat = store.get(token)
        if not chat or user not in chat.get("members", []):
            raise HTTPException(status_code=404, detail=NOT_AVAILABLE)
        now = time.time()
        attempts = _recent_attempts(chat, now)
        if len(attempts) >= PASSWORD_CHANGE_LIMIT:
            wait = max(1, int(PASSWORD_CHANGE_WINDOW_S - (now - min(attempts))))
            return JSONResponse(
                status_code=429,
                content={"detail": f"Too many password changes; try again in {max(1, round(wait / 60))} min.",
                         "retry_after": wait},
                headers={"Retry-After": str(wait)},
            )
        chat["password_change_attempts"] = [*attempts, now]

        refusal: HTTPException | None = None
        if _version(chat) == 2:
            stored = str(chat.get("auth_hash") or "")
            given = _auth(old_auth, "auth") if old_auth is not None else None
            if not stored or given is None or not hmac.compare_digest(_auth_hash(given), stored):
                refusal = HTTPException(status_code=403, detail="That is not the current password for this chat.")
        if refusal is None and record["verifier"] != chat.get("verifier"):
            refusal = HTTPException(
                status_code=409,
                detail="That change would lock away the chat's history (its key changed). Nothing was changed.")
        if refusal is not None:
            store.save(chat)
            log.warning("[private-chat] %s: password change by %s refused (%s)", token, user, refusal.status_code)
            raise refusal

        previous = {**_key_fields(chat), "version": _version(chat),
                    "replaced_at": int(now), "replaced_by": user}
        history = chat.get("history") if isinstance(chat.get("history"), list) else []
        for field in _KEY_FIELDS:
            chat.pop(field, None)
        chat.update(record)
        chat["auth_hash"] = _auth_hash(new_auth)
        chat["history"] = [*history, previous][-PASSWORD_HISTORY_KEEP:]
        chat["password_changed_at"] = int(now)
        chat["password_changed_by"] = user
        try:
            store.save(chat)
        except OSError as exc:
            log.error("[private-chat] could not save the new password record for %s: %s", token, exc)
            raise HTTPException(status_code=500, detail="Could not save the new password (PRIVATE_CHAT_DIR not writable).") from exc
    log.info("[private-chat] %s changed the password of %s (was v%s)", user, token, previous["version"])

    me = people.get(user) or {"username": user}
    others = [m for m in chat["members"] if m != user]
    url = f"/family?chat={token}"
    sent = await notify.send_many(
        others,
        lambda person: notify.notice(
            person, "chat", "Private chat", f"{_display(me)} changed the private chat password",
            url=url, priority=4, tag=f"private-password:{token}",
            actions=[notify.view_action(url)],
            data={"token": token, "private": True, "password_changed": True},
        ),
    )
    if not sent:
        log.warning("[private-chat] %s: the password-change notice to %s was not delivered", token, ", ".join(others))
    return {**_public(chat, user, people), "notified": others if sent else []}

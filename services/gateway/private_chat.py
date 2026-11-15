"""Private chat: an end-to-end encrypted conversation between the two parents.

Like the "Between" couples app, but inside the family chat. Only the parents
(family role ``father`` or ``mother``, never a minor) can start one or be in
one, and only they can read it:

* **The key never leaves a parent's device.** The password is stretched with
  PBKDF2-SHA256 (``MIN_ITERATIONS`` or more, a random 16-byte salt per chat)
  into an AES-GCM-256 key in the browser (services/ui/src/lib/privateChatCrypto.ts).
  This service keeps only the salt, the iteration count and a *verifier* (the
  AES-GCM encryption of a fixed constant), so a wrong password is caught on the
  device without the server learning anything about it. A forgotten password
  means the history is gone: there is nothing here to recover it with.
* **Messages ride on Talk as ciphertext.** Each is a ``sealed`` envelope
  (services/common/sealed.py) with a fresh IV. The Talk routes refuse anything
  else in a private room (``guard_talk``): plain text, Talk file shares, polls,
  games, cards, reactions, and adding members.
* **Pictures never touch Nextcloud.** They are encrypted on the device and kept
  here as opaque blobs (``PRIVATE_CHAT_DIR``), served only to the chat's
  members, so Nextcloud never makes a preview and nothing scans them.
* **Notices carry no content**: "New private message from Sam" (chat_notify)
  and "Sam nudged you in your private chat" (``nudge``).

The registry is one JSON file per chat (``chats/<token>.json``); pictures are
``blobs/<token>/<id>``. Both are ciphertext or public parameters only.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
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

_create_lock = asyncio.Lock()


# --- Store --------------------------------------------------------------------


class Store:
    """The registry and the encrypted pictures under one directory."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def _chat_path(self, token: str) -> Path:
        if not _TOKEN.match(token or ""):
            raise HTTPException(status_code=404, detail="No such private chat.")
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

    A chat the caller is not in answers 404, as if it did not exist.
    """
    user, people = await _caller(request)
    chat = get_store().get(token)
    if not chat or user not in chat.get("members", []):
        raise HTTPException(status_code=404, detail="No such private chat.")
    if not is_parent(people.get(user)):
        raise HTTPException(status_code=403, detail="Private chats are only for the parents.")
    return user, chat, people


# --- Crypto parameters (public; checked for shape only) -----------------------


def _b64(value: Any, field: str) -> bytes:
    if not isinstance(value, str) or not value:
        raise HTTPException(status_code=422, detail=f"{field} is required (base64).")
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"{field} is not valid base64.") from exc


def _crypto_params(body: dict[str, Any]) -> dict[str, Any]:
    salt = _b64(body.get("salt"), "salt")
    if len(salt) != SALT_BYTES:
        raise HTTPException(status_code=422, detail=f"salt must be {SALT_BYTES} bytes.")
    iterations = body.get("iterations")
    if not isinstance(iterations, int) or isinstance(iterations, bool) or not MIN_ITERATIONS <= iterations <= MAX_ITERATIONS:
        raise HTTPException(status_code=422, detail=f"iterations must be a whole number from {MIN_ITERATIONS} to {MAX_ITERATIONS}.")
    verifier = body.get("verifier")
    if not isinstance(verifier, dict):
        raise HTTPException(status_code=422, detail="verifier is required: {iv, ct}.")
    iv = _b64(verifier.get("iv"), "verifier.iv")
    ct = _b64(verifier.get("ct"), "verifier.ct")
    if len(iv) != IV_BYTES or not TAG_BYTES < len(ct) <= 256:
        raise HTTPException(status_code=422, detail="verifier is not an AES-GCM encryption of the check value.")
    return {
        "salt": body["salt"],
        "iterations": iterations,
        "verifier": {"iv": verifier["iv"], "ct": verifier["ct"]},
    }


def _public(chat: dict[str, Any], me: str, people: dict[str, dict[str, Any]]) -> dict[str, Any]:
    partner = next((m for m in chat["members"] if m != me), "")
    return {
        "token": chat["token"],
        "members": chat["members"],
        "partner": partner,
        "partner_name": _display(people.get(partner) or {"username": partner}),
        "created_by": chat["created_by"],
        "created_at": chat["created_at"],
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
        raise HTTPException(status_code=403, detail="That is not available in a private chat.")
    if action in ("send", "edit_message") and not is_sealed(message):
        raise HTTPException(status_code=403, detail="A private chat only takes encrypted messages. Unlock it in Jarvis to write.")


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
    """Whether the caller may use private chats, their chats, and who they could start one with."""
    user, people = await _caller(request)
    me = people.get(user)
    eligible = is_parent(me)
    chats = [_public(c, user, people) for c in get_store().all() if user in c.get("members", [])] if eligible else []
    partners = (
        [
            {"username": name, "display_name": _display(p)}
            for name, p in sorted(people.items())
            if name != user and is_parent(p)
        ]
        if eligible
        else []
    )
    reason = None
    if not eligible:
        reason = "Private chats are only for the parents (family role father or mother) with a chat account."
    return {"eligible": eligible, "reason": reason, "chats": chats, "partners": partners,
            "min_iterations": MIN_ITERATIONS}


@router.post("/chats")
async def create_chat(request: Request):
    """Start a private chat with the other parent.

    The body carries the public key-derivation parameters and the verifier the
    creator's device made from the password; the password itself never comes here.
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
        raise HTTPException(status_code=403, detail="Private chats are only for the parents.")
    partner = str(body.get("partner") or "")
    if not _USER.match(partner) or partner == user:
        raise HTTPException(status_code=422, detail="Choose the other parent.")
    other = people.get(partner)
    if not is_parent(other):
        raise HTTPException(status_code=403, detail="A private chat can only be with the other parent.")
    params = _crypto_params(body)
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
            person, "chat", "Private chat", f"{_display(me)} nudged you in your private chat",
            url=url, priority=4, tag=f"private-nudge:{token}",
            actions=[notify.view_action(url)],
            data={"token": token, "private": True, "nudge": True},
        ),
    )
    if not sent:
        await asyncio.to_thread(_release_nudge, key)
        raise HTTPException(status_code=502, detail="The nudge could not be delivered; try again.")
    log.info("[private-chat] %s nudged %s", user, ", ".join(others))
    return {"nudged": others, "next_in": NUDGE_INTERVAL_S}

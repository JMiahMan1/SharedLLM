"""Edits and reactions on private chat messages, end to end encrypted.

The parents' private chat (services/gateway/private_chat.py) keeps its messages
as sealed envelopes (services/gateway/private_chat_messages.py; a chat not yet
moved off Talk still has them in its Talk room). A message can also be **edited** by the
parent who wrote it and **reacted to** by either parent (a heart, a thumbs up,
a laugh...). Both are kept here, beside the gallery entries, as sealed records
made on the device with the chat's data key. This service never sees the new
text or the emoji.

Why edits are an overlay here and not Talk's own edit
------------------------------------------------------

When the messages were on Talk, Talk could edit a message in place
(``PUT /chat/<token>/<id>``), which would also have kept the server blind. It
was not used because of two surprises, and the overlay stayed when the
messages moved into the gateway's own store (marks are keyed by message id,
which the move keeps):

* **Talk refuses an edit after 24 hours** (``{"error": "age"}``), except in a
  note-to-self room. A typo noticed the next morning could not be fixed.
* **Talk lets a moderator edit someone else's message.** The parent who made
  the room is its owner, so Talk alone would not stop one parent rewriting the
  other's words. Here the author is checked against the stored message (or,
  for a chat still on Talk, Talk's own record of who sent it), and nobody else
  may edit it.

An overlay also leaves Talk's thread as it was (no "You edited a message"
system lines). The record is bound to its message inside the encryption (the
sealed JSON names the message id), so a server that moved an edit onto another
message would only produce something the devices refuse to show.

What is stored, per message, in ``marks/<token>/<message id>.json``
--------------------------------------------------------------------

* ``edit``: ``{"by", "at", "sealed"}``, the latest version only (who edited
  is always the author). The sealed record is ``{"t": "edit", "m": <id>,
  "text": ...}``.
* ``reactions``: a list of ``{"id", "by", "at", "sealed", "seen_by"}``. The
  sealed record is ``{"t": "reaction", "m": <id>, "e": <emoji>}``. The server
  learns only that a reaction exists, by whom, on which message and when, and
  who has seen it. ``seen_by`` starts with its author; the other parent is
  added when their device has played its animation (``POST /marks/seen``), so
  a reaction made while they were away waits for them and plays when they next
  open and unlock the chat.

**One reaction per person per emoji is kept by the devices**, because the
server cannot tell two emoji apart: tapping an emoji you already used removes
every copy of yours, and the thread counts each person once per emoji. The
server only caps reactions per person per message (``MAX_REACTIONS_PER_PERSON``)
so a runaway client cannot fill the disk.

Deleting a message (``DELETE /messages/<id>`` in private_chat_messages.py, or
the Talk delete route in main.py for a chat still on Talk, both calling
``forget_message``) deletes its edit and reactions with it, and deleting the
chat removes the whole ``marks/<token>`` folder (``Store.remove``).

The other parent may be told "Sam reacted in your private chat": no emoji, no
message, and at most one such notice per sender per chat per
``REACTION_NOTICE_INTERVAL_S``. An edit sends no notice.
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

from fastapi import APIRouter, HTTPException, Request

from services.common import notify
from services.common.sealed import is_sealed
from services.gateway import private_chat

log = logging.getLogger("gateway.private_chat_marks")

router = APIRouter(prefix="/api/private-chat/chats/{token}")

#: One sealed edit: the longest message a parent would write, with room for the envelope.
MAX_EDIT_BYTES = 16 * 1024
#: One sealed reaction: an emoji and the message id, sealed. Generous, never a message.
MAX_REACTION_BYTES = 1024
#: Reactions one parent may keep on one message (the devices keep one per emoji).
MAX_REACTIONS_PER_PERSON = 12
#: Messages with marks per chat, so a runaway client cannot fill the disk with records.
MAX_MARKED_MESSAGES = 50_000
#: One "reacted" notice per sender per chat in this many seconds; reactions sent sooner are kept, just not announced.
REACTION_NOTICE_INTERVAL_S = 60
#: Seen ids accepted in one call.
MAX_SEEN_IDS = 500

_ID = re.compile(r"^[0-9a-f]{32}$")
_FENCE = "```jarvis-envelope"

_lock = asyncio.Lock()


# --- Store --------------------------------------------------------------------


class MarksStore:
    """Each marked message's edit and reactions under ``<root>/marks/<token>/<message id>.json``."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def _dir(self, token: str) -> Path:
        private_chat.get_store()._chat_path(token)  # validates the token
        return self.root / "marks" / token

    def _path(self, token: str, message_id: int) -> Path:
        return self._dir(token) / f"{int(message_id)}.json"

    def get(self, token: str, message_id: int) -> dict[str, Any]:
        path = self._path(token, message_id)
        if path.is_file():
            try:
                mark = json.loads(path.read_text())
                if isinstance(mark, dict):
                    return mark
            except (OSError, ValueError):
                log.warning("[private-chat] unreadable mark %s", path.name)
        return {"message_id": int(message_id), "reactions": []}

    def put(self, token: str, mark: dict[str, Any]) -> None:
        folder = self._dir(token)
        folder.mkdir(parents=True, exist_ok=True)
        path = self._path(token, mark["message_id"])
        if not mark.get("edit") and not mark.get("reactions"):
            path.unlink(missing_ok=True)
            return
        if not path.exists() and sum(1 for _ in folder.glob("*.json")) >= MAX_MARKED_MESSAGES:
            raise HTTPException(status_code=413, detail="There is no room for more of these.")
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(mark, sort_keys=True))
        os.replace(tmp, path)

    def forget(self, token: str, message_id: int) -> bool:
        path = self._path(token, message_id)
        existed = path.exists()
        path.unlink(missing_ok=True)
        return existed

    def all(self, token: str) -> list[dict[str, Any]]:
        folder = self._dir(token)
        if not folder.is_dir():
            return []
        out = []
        for path in folder.glob("*.json"):
            try:
                mark = json.loads(path.read_text())
            except (OSError, ValueError):
                log.warning("[private-chat] unreadable mark %s", path.name)
                continue
            if isinstance(mark, dict) and isinstance(mark.get("message_id"), int):
                out.append(mark)
        return sorted(out, key=lambda m: m["message_id"])

    def remove_all(self, token: str) -> None:
        shutil.rmtree(self._dir(token), ignore_errors=True)


def get_store() -> MarksStore:
    return MarksStore(private_chat.get_store().root)


def forget_message(token: str | None, message_id: Any) -> bool:
    """Drop a deleted message's edit and reactions (called after Talk deleted it)."""
    if not token or not private_chat.is_private(token):
        return False
    try:
        mid = int(message_id)
    except (TypeError, ValueError):
        return False
    if mid <= 0:
        return False
    removed = get_store().forget(str(token), mid)
    if removed:
        log.info("[private-chat] %s: message %s deleted with its edit and reactions", token, mid)
    return removed


# --- Checks -------------------------------------------------------------------


def _sealed(value: Any, max_bytes: int) -> str:
    """A sealed envelope whose iv and ciphertext are the right shape. Never anything readable."""
    if not isinstance(value, str) or not is_sealed(value):
        raise HTTPException(status_code=422, detail="Only an encrypted record can be kept.")
    if len(value.encode()) > max_bytes:
        raise HTTPException(status_code=413, detail="That is too long.")
    start = value.find(_FENCE)
    end = value.find("```", start + len(_FENCE))
    try:
        envelope = json.loads(value[start + len(_FENCE):end].strip()) if start != -1 and end != -1 else None
        iv = base64.b64decode(str(envelope["iv"]), validate=True)
        ct = base64.b64decode(str(envelope["ct"]), validate=True)
    except (TypeError, KeyError, ValueError, binascii.Error) as exc:
        raise HTTPException(status_code=422, detail="Only an encrypted record can be kept.") from exc
    if not isinstance(envelope, dict) or envelope.get("kind") != "sealed" or len(iv) != private_chat.IV_BYTES \
            or len(ct) <= private_chat.TAG_BYTES:
        raise HTTPException(status_code=422, detail="Only an encrypted record can be kept.")
    # Nothing beside the envelope: a sealed record carries only the standard locked line before it.
    # The app closes the block with the full fence (privateChatCrypto.ts sealRecord/sealMessage), so a
    # closing "```jarvis-envelope" is part of the envelope, not text beside it. Treating its tail as
    # outside text refused every real message, edit and reaction (only bare-``` test fixtures passed).
    close = len(_FENCE) if value.startswith(_FENCE, end) else 3
    outside = (value[:start] + value[end + close:]).strip()
    if outside and outside not in {"Encrypted message", "\U0001f512 Encrypted message"}:
        raise HTTPException(status_code=422, detail="Only an encrypted record can be kept.")
    return value


def _message_id(value: Any) -> int:
    try:
        mid = int(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="No such message.") from exc
    if mid <= 0 or mid > 2**53:
        raise HTTPException(status_code=404, detail="No such message.")
    return mid


async def _body(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Send a JSON body.") from exc
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="Send a JSON body.")
    return body


async def _talk_message(request: Request, token: str, message_id: int) -> dict[str, Any]:
    """Talk's own record of one message in the room (who sent it), or 404 when there is none to mark."""
    status_code, result = await private_chat._talk(request, {"action": "message", "token": token, "message_id": message_id})
    detail = result.get("detail") if isinstance(result.get("detail"), dict) else {}
    record = detail.get("message_record") if isinstance(detail, dict) else None
    if status_code != 200 or result.get("status") != "SUCCESS" or not isinstance(record, dict):
        raise HTTPException(status_code=404, detail="No such message.")
    if record.get("id") != message_id or record.get("deleted") or record.get("system_message"):
        raise HTTPException(status_code=404, detail="No such message.")
    return record


async def _author(request: Request, token: str, chat: dict[str, Any], people: dict[str, dict[str, Any]],
                  message_id: int) -> str:
    """The username who wrote a live message in the chat, or 404 when there is none to mark."""
    if chat.get("storage") == "store":
        from services.gateway import private_chat_messages

        try:
            by = await asyncio.to_thread(private_chat_messages.author, token, message_id)
        except private_chat_messages.StoreUnreadable as exc:
            raise HTTPException(status_code=503, detail="Could not read the chat; try again.") from exc
        if not by:
            raise HTTPException(status_code=404, detail="No such message.")
        return by
    record = await _talk_message(request, token, message_id)
    return next((name for name, person in people.items() if _sent_by(record, person)), "")


def _sent_by(record: dict[str, Any], person: dict[str, Any]) -> bool:
    """Whether Talk says this person (by their chat account) sent the message."""
    account = str(person.get("nextcloud_user") or "").lower()
    return bool(account) and record.get("actor_type") in (None, "users") \
        and str(record.get("actor_id") or "").lower() == account


def _public(mark: dict[str, Any], me: str, people: dict[str, dict[str, Any]]) -> dict[str, Any]:
    def name(user: str) -> str:
        return private_chat._display(people.get(user) or {"username": user})

    edit = mark.get("edit") if isinstance(mark.get("edit"), dict) else None
    reactions = [r for r in mark.get("reactions") or [] if isinstance(r, dict) and isinstance(r.get("sealed"), str)]
    return {
        "message_id": mark["message_id"],
        "edit": {"at": edit.get("at", 0), "by": edit.get("by"), "own": edit.get("by") == me, "sealed": edit["sealed"]}
        if edit and isinstance(edit.get("sealed"), str) else None,
        "reactions": [
            {
                "id": r["id"],
                "by": str(r.get("by") or ""),
                "by_name": name(str(r.get("by") or "")),
                "own": r.get("by") == me,
                "at": r.get("at", 0),
                "seen": me in (r.get("seen_by") or []),
                "sealed": r["sealed"],
            }
            for r in reactions
        ],
    }


def _claim(key: str, interval: int) -> bool:
    from services.gateway.cache import get_redis

    return bool(get_redis().set(key, "1", nx=True, ex=interval))


async def _tell_partner(token: str, chat: dict[str, Any], user: str, people: dict[str, dict[str, Any]]) -> bool:
    """'Sam reacted in your private chat' to the other parent: no emoji, no message, rate limited."""
    try:
        allowed = await asyncio.to_thread(_claim, f"private-chat:reaction:{token}:{user}", REACTION_NOTICE_INTERVAL_S)
    except Exception as exc:
        log.warning("[private-chat] reaction notice limit unavailable: %s", type(exc).__name__)
        return False
    if not allowed:
        return False
    others = [m for m in chat["members"] if m != user]
    me = people.get(user) or {"username": user}
    url = f"/family?chat={token}"
    sent = await notify.send_many(
        others,
        lambda person: notify.notice(
            person, "chat", "Private chat", f"{private_chat._display(me)} reacted in your private chat",
            url=url, priority=3, tag=f"private-reaction:{token}",
            actions=[notify.view_action(url)],
            data={"token": token, "private": True, "reaction": True},
        ),
    )
    return bool(sent)


# --- Routes -------------------------------------------------------------------


@router.get("/marks")
async def list_marks(token: str, request: Request):
    """Every edited or reacted-to message's sealed edit and reactions, with whether the caller has seen each."""
    user, _chat, people = await private_chat._member_chat(request, token)
    marks = await asyncio.to_thread(get_store().all, token)
    return {"marks": [_public(m, user, people) for m in marks]}


@router.put("/messages/{message_id}/edit")
async def edit_message(token: str, message_id: str, request: Request):
    """Replace a message's text with a new sealed version. Only the parent who sent it may."""
    user, chat, people = await private_chat._member_chat(request, token)
    mid = _message_id(message_id)
    sealed = _sealed((await _body(request)).get("sealed"), MAX_EDIT_BYTES)
    if await _author(request, token, chat, people, mid) != user:
        raise HTTPException(status_code=403, detail="Only the one who wrote it can edit it.")
    async with _lock:
        store = get_store()
        mark = store.get(token, mid)
        mark["edit"] = {"by": user, "at": int(time.time()), "sealed": sealed}
        await asyncio.to_thread(store.put, token, mark)
    log.info("[private-chat] %s edited message %s in %s", user, mid, token)
    return _public(mark, user, people)


@router.post("/messages/{message_id}/reactions")
async def add_reaction(token: str, message_id: str, request: Request):
    """Keep one sealed reaction on a message (either parent's); the other parent sees it play once."""
    user, chat, people = await private_chat._member_chat(request, token)
    mid = _message_id(message_id)
    sealed = _sealed((await _body(request)).get("sealed"), MAX_REACTION_BYTES)
    await _author(request, token, chat, people, mid)
    async with _lock:
        store = get_store()
        mark = store.get(token, mid)
        reactions = [r for r in mark.get("reactions") or [] if isinstance(r, dict)]
        if sum(1 for r in reactions if r.get("by") == user) >= MAX_REACTIONS_PER_PERSON:
            raise HTTPException(status_code=409, detail="That message has all the reactions it can take from you.")
        reaction = {"id": secrets.token_hex(16), "by": user, "at": int(time.time()), "sealed": sealed, "seen_by": [user]}
        mark["reactions"] = [*reactions, reaction]
        await asyncio.to_thread(store.put, token, mark)
    notified = await _tell_partner(token, chat, user, people)
    return {**_public(mark, user, people), "reaction_id": reaction["id"], "notified": notified}


@router.delete("/messages/{message_id}/reactions/{reaction_id}")
async def remove_reaction(token: str, message_id: str, reaction_id: str, request: Request):
    """Take back one of your own reactions."""
    user, _chat, people = await private_chat._member_chat(request, token)
    mid = _message_id(message_id)
    if not _ID.match(reaction_id or ""):
        raise HTTPException(status_code=404, detail="No such reaction.")
    async with _lock:
        store = get_store()
        mark = store.get(token, mid)
        reactions = [r for r in mark.get("reactions") or [] if isinstance(r, dict)]
        target = next((r for r in reactions if r.get("id") == reaction_id), None)
        if not target:
            raise HTTPException(status_code=404, detail="No such reaction.")
        if target.get("by") != user:
            raise HTTPException(status_code=403, detail="Only the one who reacted can take it back.")
        mark["reactions"] = [r for r in reactions if r is not target]
        await asyncio.to_thread(store.put, token, mark)
    return _public(mark, user, people)


@router.post("/marks/seen")
async def mark_seen(token: str, request: Request):
    """The caller's device has shown these reactions (played their animation): they stop waiting."""
    user, _chat, _people = await private_chat._member_chat(request, token)
    ids = (await _body(request)).get("ids")
    if not isinstance(ids, list) or len(ids) > MAX_SEEN_IDS or not all(isinstance(i, str) and _ID.match(i) for i in ids):
        raise HTTPException(status_code=422, detail=f"ids must be a list of up to {MAX_SEEN_IDS} reaction ids.")
    wanted = set(ids)
    marked: list[str] = []
    if wanted:
        async with _lock:
            store = get_store()
            for mark in await asyncio.to_thread(store.all, token):
                changed = False
                for reaction in mark.get("reactions") or []:
                    if isinstance(reaction, dict) and reaction.get("id") in wanted:
                        seen_by = [str(s) for s in reaction.get("seen_by") or []]
                        if user not in seen_by:
                            reaction["seen_by"] = [*seen_by, user]
                            changed = True
                        marked.append(str(reaction["id"]))
                if changed:
                    await asyncio.to_thread(store.put, token, mark)
    return {"seen": sorted(marked)}

"""The private chat's messages, kept by the gateway instead of Nextcloud Talk.

The parents' private chat (services/gateway/private_chat.py) used to post every
message to a Talk room as a sealed envelope, so Nextcloud showed a room full of
ciphertext. Its messages now live here, beside the chat's pictures, gallery,
marks and Beloved. Only the private chat moved: every other conversation stays
on Talk exactly as it was.

What is stored (all under ``PRIVATE_CHAT_DIR/messages/<token>/``)
------------------------------------------------------------------

* ``m/<id>.json``: one message. ``{"id", "by", "at", "sealed", "reply_to",
  "deleted"}``: the integer id, the author's username, the Unix time it was
  sent, the sealed envelope exactly as the device made it (the same text a
  Talk message carried), the id it replies to and whether it was deleted. What
  kind of message it is (text, picture, comment on a photo) is inside the
  encryption, as it was on Talk, so the server does not know it. A deleted
  message keeps its id, author and time as a tombstone with no ciphertext, so
  ordering and the ids its marks hang on stay put.
* ``meta.json``: ``{"floor": N}``. New ids start above it. A chat moved off
  Talk sets it to the highest id the Talk room had used, so a new message can
  never take an id a migrated one (or its edit or reaction) already has.
* ``read.json``: ``{username: last read id}``, one marker per member, for the
  unread count, the read receipt and the nudge.

Every file is written whole to a temporary name, flushed to disk, renamed over
the old one and the folder flushed too, so a kill mid-write leaves either the
old file or the new one, never half of one. Leftover temporary files are
ignored and swept. A record that cannot be read anyway (a damaged disk) is a
``StoreUnreadable`` error, never a silently shorter thread.

Only a well-formed sealed envelope is accepted (the same check as an edit,
services/gateway/private_chat_marks.py): readable text never reaches the disk.

Live updates
------------

``GET .../messages/wait`` holds the request until a message newer than
``last_id`` is stored (or ``wait`` seconds pass). A send wakes every waiter on
that chat through an ``asyncio.Event`` per chat, which works because the
gateway runs as one uvicorn process (services/gateway/Dockerfile starts it
without ``--workers``). Running more than one worker would need Redis pub/sub
here instead; a waiter on another process would otherwise only see the new
message when its hold ran out.

Notices
-------

A new message tells the other parent "New private message from Sam" (no
content, no inline reply), a few seconds after it is stored and only while it
is still unread, as the Talk-based notice (services/gateway/chat_notify.py)
did. Nudges, reactions and password changes keep their own notices.

Moving a chat off Talk (``migrate_chat``, run on gateway start)
----------------------------------------------------------------

A chat made before this change has no ``storage`` field in its registry record
and its messages are in its Talk room. On start the gateway moves each such
chat, one at a time, retrying with backoff when a step fails:

1. Read every message in the room as the parent who made it, paging back 200
   at a time (``lastKnownMessageId``) until a short page, without moving that
   parent's read marker or clearing a notification.
2. Write a full export first: Talk's raw records (ciphertext only) and a copy
   of the registry record, to ``migration/<token>/talk-export-<time>.json``
   (mode 600). An earlier export is never overwritten.
3. Copy each comment with its Talk id, author, time and reply-to; a deleted
   one (``comment_deleted``) becomes a tombstone with its id; Talk's system
   lines ("You created the conversation") are skipped and only counted. A
   comment that is not a sealed envelope stops the move: it cannot go into the
   store, and nothing may be lost.
4. Verify: the store's ids are exactly Talk's comment and tombstone ids, and
   every stored envelope is byte for byte Talk's.
5. Set the id floor, then switch the chat to ``storage: "store"`` in its
   registry record (``migration`` there says what was moved and when).

Nothing is deleted. The Talk room is left as it was (writes to it are refused
from then on, ``guard_talk``), so the owner can compare. Deleting it is a
separate step, ``POST /api/private-chat/admin/chats/<token>/delete-talk-room``
(admins; ``?dry_run=1`` only reports), which refuses unless the chat is in
store mode and the room's current comments still match the store, and logs
what it deletes. ``PRIVATE_CHAT_DELETE_TALK_ROOM=1`` makes the migration take
that step by itself; it is off by default.

Until the move, the routes below pass through to Talk, so the app never needs
to know which kind of chat it has. If a moved chat's store is ever unreadable
or missing migrated messages, reading falls back to the Talk room (still there)
for the moved part of the history instead of showing it empty.
"""
from __future__ import annotations

import asyncio
import contextlib
import fcntl
import json
import logging
import os
import re
import secrets
import shutil
import threading
import time
from pathlib import Path
from typing import Any

import aiohttp
from fastapi import APIRouter, HTTPException, Request

from services.common import notify
from services.common.sealed import SEALED_LINE, is_sealed
from services.gateway import private_chat, private_chat_marks

log = logging.getLogger("gateway.private_chat_messages")

router = APIRouter(prefix="/api/private-chat")

#: One sealed message: Talk's own limit was 32,000 characters.
MAX_MESSAGE_BYTES = 32 * 1024
#: Messages per chat, so a runaway client cannot fill the disk.
MAX_MESSAGES = 500_000
#: Most messages one list call returns.
MAX_PAGE = 200
#: Longest a wait may hold.
MAX_WAIT_S = 30
#: How long after a send the other parent is told, if they have not read it by then.
NOTICE_DELAY_S = 4.0
#: Talk's largest page, and how many pages a migration reads at most.
TALK_PAGE = 200
MAX_TALK_PAGES = 5_000
#: Retry a failed migration after this long, doubling up to the cap.
MIGRATION_RETRY_START_S = 60
MIGRATION_RETRY_MAX_S = 6 * 3600
#: Delete a moved chat's Talk room at the end of the migration. Off: the room is kept until an admin deletes it.
DELETE_TALK_ROOM = os.getenv("PRIVATE_CHAT_DELETE_TALK_ROOM", "0").strip().lower() in ("1", "true", "yes", "on")

_RECORD = re.compile(r"^([1-9][0-9]{0,15})\.json$")

#: One writer at a time per process (ids are allocated under it).
_write_lock = threading.Lock()
#: One migration at a time in this process.
_migrate_lock = asyncio.Lock()
#: A send rings the chat's bell; every wait on it then looks again.
_bells: dict[str, asyncio.Event] = {}
#: Notices waiting out NOTICE_DELAY_S (kept so asyncio does not collect them).
_tasks: set[asyncio.Task] = set()


class StoreUnreadable(Exception):
    """The store could not be read (a damaged record or folder). Never content in the message."""


# --- Files --------------------------------------------------------------------


def _fsync_dir(folder: Path) -> None:
    with contextlib.suppress(OSError):
        fd = os.open(folder, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def write_atomic(path: Path, data: bytes, *, mode: int = 0o600, exclusive: bool = False) -> None:
    """Write ``path`` whole or not at all: temp file, fsync, rename (or link, when it must not exist), fsync the folder."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view):]
        os.fsync(fd)
    finally:
        os.close(fd)
    try:
        if exclusive:
            os.link(tmp, path)  # FileExistsError rather than replace an existing file
            os.unlink(tmp)
        else:
            os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    _fsync_dir(path.parent)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise StoreUnreadable(f"{path.name}: {type(exc).__name__}") from exc


class MessageStore:
    """Each private chat's messages, read markers and id floor under ``<root>/messages/<token>/``."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def _dir(self, token: str) -> Path:
        private_chat.get_store()._chat_path(token)  # validates the token
        return self.root / "messages" / token

    def _path(self, token: str, message_id: int) -> Path:
        return self._dir(token) / "m" / f"{int(message_id)}.json"

    def ids(self, token: str) -> list[int]:
        folder = self._dir(token) / "m"
        try:
            names = os.listdir(folder)
        except FileNotFoundError:
            return []
        except OSError as exc:
            raise StoreUnreadable(f"listing: {type(exc).__name__}") from exc
        return sorted(int(m.group(1)) for m in map(_RECORD.match, names) if m)

    def get(self, token: str, message_id: int) -> dict[str, Any] | None:
        record = _read_json(self._path(token, message_id))
        if record is None:
            return None
        if not isinstance(record, dict) or record.get("id") != int(message_id):
            raise StoreUnreadable(f"record {int(message_id)} is malformed")
        return record

    def records(self, token: str, ids: list[int]) -> list[dict[str, Any]]:
        out = []
        for mid in ids:
            record = self.get(token, mid)
            if record is None:
                raise StoreUnreadable(f"record {mid} vanished")
            out.append(record)
        return out

    def page(self, token: str, limit: int, *, before: int | None = None, after: int | None = None) -> list[dict[str, Any]]:
        """Up to ``limit`` records, oldest first: the newest ones (below ``before``), or the first ones above ``after``."""
        ids = self.ids(token)
        if after is not None:
            chosen = [i for i in ids if i > after][:limit]
        else:
            below = [i for i in ids if before is None or i < before]
            chosen = below[-limit:] if limit else []
        return self.records(token, chosen)

    def floor(self, token: str) -> int:
        meta = _read_json(self._dir(token) / "meta.json")
        return int(meta.get("floor") or 0) if isinstance(meta, dict) else 0

    def set_floor(self, token: str, floor: int) -> None:
        with _write_lock:
            current = self.floor(token)
            if floor > current:
                write_atomic(self._dir(token) / "meta.json", json.dumps({"floor": int(floor)}).encode())

    def append(self, token: str, by: str, sealed: str, reply_to: int | None = None) -> dict[str, Any]:
        with _write_lock:
            ids = self.ids(token)
            if len(ids) >= MAX_MESSAGES:
                raise HTTPException(status_code=413, detail="This chat is full.")
            new_id = max(ids[-1] if ids else 0, self.floor(token)) + 1
            record = {"id": new_id, "by": by, "at": int(time.time()), "sealed": sealed,
                      "reply_to": reply_to, "deleted": False}
            write_atomic(self._path(token, new_id), json.dumps(record, sort_keys=True).encode())
            return record

    def put_migrated(self, token: str, record: dict[str, Any]) -> bool:
        """Store one record moved from Talk under its own id. False when an identical one is already there."""
        with _write_lock:
            existing = self.get(token, record["id"])
            if existing is not None:
                if existing == record:
                    return False
                raise ValueError(f"message {record['id']} is already stored differently")
            write_atomic(self._path(token, record["id"]), json.dumps(record, sort_keys=True).encode())
            return True

    def tombstone(self, token: str, message_id: int) -> dict[str, Any]:
        with _write_lock:
            record = self.get(token, message_id)
            if record is None:
                raise KeyError(message_id)
            record.update({"sealed": None, "deleted": True, "deleted_at": int(time.time())})
            write_atomic(self._path(token, message_id), json.dumps(record, sort_keys=True).encode())
            return record

    def read_markers(self, token: str) -> dict[str, int]:
        try:
            markers = _read_json(self._dir(token) / "read.json")
        except StoreUnreadable:
            log.warning("[private-chat] %s: read markers unreadable; treating them as unset", token)
            return {}
        if not isinstance(markers, dict):
            return {}
        return {str(k): int(v) for k, v in markers.items() if isinstance(v, int)}

    def mark_read(self, token: str, user: str, upto: int) -> int:
        with _write_lock:
            markers = self.read_markers(token)
            if upto > markers.get(user, 0):
                markers[user] = int(upto)
                write_atomic(self._dir(token) / "read.json", json.dumps(markers, sort_keys=True).encode())
            return markers.get(user, 0)

    def remove(self, token: str) -> None:
        shutil.rmtree(self._dir(token), ignore_errors=True)


def get_store() -> MessageStore:
    return MessageStore(private_chat.get_store().root)


def is_store_chat(chat: dict[str, Any] | None) -> bool:
    return bool(chat) and chat.get("storage") == "store"


# --- Shapes -------------------------------------------------------------------


def _sealed(value: Any) -> str:
    return private_chat_marks._sealed(value, MAX_MESSAGE_BYTES)


def public(record: dict[str, Any], token: str, people: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """A stored message in the shape the app already reads for a Talk message."""
    by = str(record.get("by") or "")
    deleted = bool(record.get("deleted"))
    reply_to = record.get("reply_to") if isinstance(record.get("reply_to"), int) else None
    return {
        "id": record["id"],
        "token": token,
        "actor_type": "users",
        "actor_id": by,
        "actor_display_name": private_chat._display(people.get(by) or {"username": by}),
        "timestamp": int(record.get("at") or 0),
        "message_type": "comment_deleted" if deleted else "comment",
        "system_message": "",
        "message": "" if deleted else str(record.get("sealed") or ""),
        "deleted": deleted,
        "reply_to": reply_to,
        "parent": {"id": reply_to} if reply_to else None,
        "reactions": {},
        "reactions_self": [],
        "parameters": {},
    }


def _ok(messages: list[dict[str, Any]], source: str, **extra: Any) -> dict[str, Any]:
    return {"status": "SUCCESS", "message": f"Loaded {len(messages)} message(s).", "source": source,
            "detail": {"messages": messages, **extra}}


def _common_read(chat: dict[str, Any], markers: dict[str, int]) -> int:
    return min((markers.get(str(m), 0) for m in chat.get("members", [])), default=0)


def _unread_for(store: MessageStore, token: str, user: str, ids: list[int], markers: dict[str, int]) -> tuple[int, dict | None]:
    """How many live messages from someone else are past ``user``'s marker, and the newest live record."""
    mine = markers.get(user, 0)
    unread = 0
    last: dict[str, Any] | None = None
    for mid in reversed(ids):
        record = store.get(token, mid)
        if not record or record.get("deleted"):
            continue
        if last is None:
            last = record
        if mid <= mine:
            break
        if record.get("by") != user:
            unread += 1
    if last is None:
        return unread, None
    return unread, last


def _summary(chat: dict[str, Any], user: str, names: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    """The inbox's view of one moved chat for ``user``: who wrote last and when, unread, read markers. No content."""
    token = chat["token"]
    store = get_store()
    try:
        ids = store.ids(token)
        markers = store.read_markers(token)
        unread, last = _unread_for(store, token, user, ids, markers)
    except StoreUnreadable as exc:
        log.warning("[private-chat] %s: store unreadable for the inbox (%s)", token, exc)
        return None
    at = int((last or {}).get("at") or chat.get("created_at") or 0)
    by = str((last or {}).get("by") or "")
    return {
        "token": token,
        "unread_messages": unread,
        "unread_mention": False,
        "last_activity": at,
        "last_message_timestamp": at,
        "last_message_id": int((last or {}).get("id") or 0),
        "last_message_actor_id": by,
        "last_message_actor": private_chat._display(names.get(by) or {"username": by}) if by else "",
        "last_message_system": False,
        "last_read": markers.get(user, 0),
        "last_common_read": _common_read(chat, markers),
    }


async def inbox(content: dict[str, Any], user: str) -> dict[str, Any]:
    """Put each moved private chat of ``user``'s into a Talk conversation list, counted from the store.

    A chat whose Talk room still exists keeps its entry, with the store's
    unread count and last activity in place of Talk's (which stopped moving);
    one without a room gets an entry of its own. Neither carries content.
    """
    detail = content.get("detail") if isinstance(content, dict) else None
    conversations = detail.get("conversations") if isinstance(detail, dict) else None
    if not isinstance(conversations, list) or not user:
        return content
    chats = [c for c in private_chat.get_store().all() if is_store_chat(c) and user in c.get("members", [])]
    if not chats:
        return content
    try:
        names = await private_chat._people()
    except HTTPException:
        names = {}
    for chat in chats:
        summary = await asyncio.to_thread(_summary, chat, user, names)
        if summary is None:
            continue
        conv = next((c for c in conversations if isinstance(c, dict) and c.get("token") == chat["token"]), None)
        if conv is None:
            conv = {"token": chat["token"], "display_name": private_chat.ROOM_NAME, "name": "", "type": 2,
                    "can_moderate": True}
            conversations.append(conv)
        conv.update(summary)
        conv.update({"private": True, "storage": "store", "last_message": SEALED_LINE, "description": None})
    return content


# --- Talk (until a chat is moved, and for the move itself) ----------------------


async def _talk_as(username: str, payload: dict[str, Any], *, timeout: float = 60.0) -> tuple[int, dict[str, Any]]:
    """One /execute/talk call as ``username`` with no request (the migration runs on start)."""
    from services.gateway.main import EXECUTION_SVC, IDENTITY_SVC, INTERNAL_SECRET, shared_http_client

    headers = {"X-Internal-Secret": INTERNAL_SECRET}
    async with shared_http_client() as client:
        resp = await client.post(f"{IDENTITY_SVC}/api/resolve", json={"rag_user": username}, headers=headers,
                                 timeout=aiohttp.ClientTimeout(total=15.0))
        creds = await resp.json(content_type=None) if resp.status == 200 else None
        if not isinstance(creds, dict) or not creds.get("nextcloud_user"):
            return 0, {"status": "FAILURE", "message": f"no chat account for {username}"}
        resp = await client.post(f"{EXECUTION_SVC}/execute/talk", json={"user_context": creds, **payload},
                                 headers=headers, timeout=aiohttp.ClientTimeout(total=timeout))
        try:
            body = await resp.json(content_type=None)
        except (ValueError, aiohttp.ContentTypeError):
            body = {}
        return resp.status, body if isinstance(body, dict) else {}


def _talk_ok(status_code: int, result: dict[str, Any]) -> bool:
    return status_code == 200 and result.get("status") == "SUCCESS"


async def read_talk_room(token: str, as_user: str) -> list[dict[str, Any]]:
    """Every raw Talk record in the room, newest first, read as ``as_user`` without touching their read marker."""
    out: list[dict[str, Any]] = []
    lowest: int | None = None
    for _ in range(MAX_TALK_PAGES):
        payload: dict[str, Any] = {"action": "messages", "token": token, "limit": TALK_PAGE, "export": True}
        if lowest is not None:
            payload["last_known_id"] = lowest
        status_code, result = await _talk_as(as_user, payload)
        detail = result.get("detail") if isinstance(result.get("detail"), dict) else {}
        if not _talk_ok(status_code, result) or detail.get("export") is not True or not isinstance(detail.get("raw"), list):
            raise RuntimeError(f"Talk did not give a page of the room (HTTP {status_code}, {result.get('status')})")
        page = [r for r in detail["raw"] if isinstance(r, dict) and isinstance(r.get("id"), int)]
        if lowest is not None and any(r["id"] >= lowest for r in page):
            raise RuntimeError("Talk paging went backwards (an execution service without export paging?)")
        out.extend(page)
        if len(page) < TALK_PAGE:
            return out
        lowest = min(r["id"] for r in page)
    raise RuntimeError(f"the room has more than {MAX_TALK_PAGES} pages")


# --- Migration ----------------------------------------------------------------


def _migration_dir(token: str) -> Path:
    private_chat.get_store()._chat_path(token)
    return private_chat.get_store().root / "migration" / token


def _write_export(token: str, chat: dict[str, Any], raw: list[dict[str, Any]]) -> str:
    folder = _migration_dir(token)
    folder.mkdir(parents=True, exist_ok=True)
    os.chmod(folder, 0o700)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    body = json.dumps({"token": token, "exported_at": int(time.time()), "registry": chat, "messages": raw},
                      sort_keys=True).encode()
    for n in range(100):
        name = f"talk-export-{stamp}{'' if n == 0 else f'-{n}'}.json"
        try:
            write_atomic(folder / name, body, mode=0o600, exclusive=True)
            return name
        except FileExistsError:
            continue
    raise RuntimeError("could not find a free export name")


def _plan(raw: list[dict[str, Any]], people: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Talk's records sorted into what moves: comments, tombstones, skipped system lines. Raises on anything else."""
    by_account = {str(p.get("nextcloud_user") or "").casefold(): name for name, p in people.items() if p.get("nextcloud_user")}
    comments: list[dict[str, Any]] = []
    tombstones: list[dict[str, Any]] = []
    system = 0
    ids = [r["id"] for r in raw]
    if len(ids) != len(set(ids)):
        raise RuntimeError("Talk gave the same message twice")
    for item in sorted(raw, key=lambda r: r["id"]):
        kind = item.get("messageType")
        if kind == "system" or item.get("systemMessage"):
            system += 1
            continue
        if kind not in ("comment", "comment_deleted"):
            raise RuntimeError(f"message {item['id']} is of a kind that cannot move ({kind})")
        if item.get("actorType") not in (None, "users"):
            raise RuntimeError(f"message {item['id']} was not sent by a family member")
        author = by_account.get(str(item.get("actorId") or "").casefold())
        if not author:
            raise RuntimeError(f"message {item['id']} has an author with no family account")
        parent = item.get("parent")
        reply_to = parent.get("id") if isinstance(parent, dict) and isinstance(parent.get("id"), int) else None
        record = {"id": item["id"], "by": author, "at": int(item.get("timestamp") or 0), "reply_to": reply_to,
                  "talk_id": item["id"]}
        if kind == "comment_deleted":
            tombstones.append({**record, "sealed": None, "deleted": True})
            continue
        message = item.get("message")
        if not is_sealed(message):
            raise RuntimeError(f"message {item['id']} is not encrypted; it cannot move and stays on Talk")
        try:
            _sealed(message)
        except HTTPException as exc:
            raise RuntimeError(f"message {item['id']} is not a well-formed sealed envelope") from exc
        comments.append({**record, "sealed": message, "deleted": False})
    return {"comments": comments, "tombstones": tombstones, "system": system,
            "talk_max_id": max(ids, default=0)}


def _verify(token: str, plan: dict[str, Any]) -> None:
    store = get_store()
    expected = {r["id"]: r for r in plan["comments"] + plan["tombstones"]}
    stored = [i for i in store.ids(token) if i <= plan["talk_max_id"]]
    if set(stored) != set(expected):
        raise RuntimeError(f"the store has {len(stored)} moved messages, Talk {len(expected)}")
    for mid, want in expected.items():
        got = store.get(token, mid)
        if got is None or got.get("by") != want["by"] or bool(got.get("deleted")) != bool(want["deleted"]):
            raise RuntimeError(f"message {mid} did not store as it was")
        if not want["deleted"]:
            if got.get("sealed") != want["sealed"]:
                raise RuntimeError(f"message {mid}'s envelope changed on the way")
            _sealed(got.get("sealed"))


async def _update_chat(token: str, change) -> dict[str, Any] | None:
    """Re-read the registry record and apply ``change`` under the lock password changes use."""
    async with private_chat._password_lock:
        store = private_chat.get_store()
        chat = store.get(token)
        if not chat:
            return None
        change(chat)
        store.save(chat)
        return chat


def _flock(token: str):
    folder = _migration_dir(token)
    folder.mkdir(parents=True, exist_ok=True)
    handle = open(folder / ".lock", "a")  # noqa: SIM115 (held for the whole migration)
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise RuntimeError("another migration of this chat is running") from None
    return handle


async def _carry_read_markers(token: str, chat: dict[str, Any], owner: str, plan: dict[str, Any]) -> None:
    """Start the store's read markers where Talk had them, so a moved chat does not light up as unread.

    Talk tells the owner their own marker and everyone's common one; the other
    member gets the common one (never further than they had really read).
    Without an answer every moved message counts as read: no notice is ever
    sent for history, only the inbox badge would be wrong.
    """
    moved = [r["id"] for r in plan["comments"]]
    newest = max(moved, default=0)
    markers = {str(m): newest for m in chat.get("members", [])}
    with contextlib.suppress(Exception):
        status_code, result = await _talk_as(owner, {"action": "open", "token": token})
        conv = (result.get("detail") or {}).get("conversation") if _talk_ok(status_code, result) else None
        if isinstance(conv, dict):
            common = int(conv.get("last_common_read") or 0)
            markers = {str(m): min(common, newest) for m in chat.get("members", [])}
            markers[owner] = min(max(int(conv.get("last_read") or 0), common), newest)
    store = get_store()
    for member, upto in markers.items():
        if upto:
            await asyncio.to_thread(store.mark_read, token, member, upto)


async def migrate_chat(token: str) -> str:
    """Move one chat's messages off Talk. Returns "moved", "already" or raises (the chat stays on Talk)."""
    chat = private_chat.get_store().get(token)
    if not chat:
        return "gone"
    if is_store_chat(chat):
        return "already"
    owner = str(chat.get("created_by") or "")
    readers = [owner, *[m for m in chat.get("members", []) if m != owner]]
    handle = await asyncio.to_thread(_flock, token)
    try:
        people = await private_chat._people()
        raw: list[dict[str, Any]] | None = None
        why = ""
        for reader in readers:
            try:
                raw = await read_talk_room(token, reader)
                break
            except (RuntimeError, aiohttp.ClientError, TimeoutError) as exc:
                why = f"{type(exc).__name__}: {exc}"
        if raw is None:
            raise RuntimeError(f"could not read the Talk room ({why})")
        export = await asyncio.to_thread(_write_export, token, chat, raw)
        plan = _plan(raw, people)
        store = get_store()
        for record in plan["comments"] + plan["tombstones"]:
            await asyncio.to_thread(store.put_migrated, token, record)
        await asyncio.to_thread(_verify, token, plan)
        await asyncio.to_thread(store.set_floor, token, plan["talk_max_id"])
        await _carry_read_markers(token, chat, owner, plan)
        summary = {
            "at": int(time.time()),
            "comments": len(plan["comments"]),
            "tombstones": len(plan["tombstones"]),
            "system_skipped": plan["system"],
            "talk_max_id": plan["talk_max_id"],
            "ids": sorted(r["id"] for r in plan["comments"] + plan["tombstones"]),
            "export": export,
        }

        def switch(record: dict[str, Any]) -> None:
            record["storage"] = "store"
            record["talk_room"] = True
            record["migration"] = summary

        if await _update_chat(token, switch) is None:
            return "gone"
    finally:
        handle.close()
    log.info("[private-chat] %s moved off Talk: %d messages, %d deleted kept as tombstones, %d system lines skipped "
             "(export %s); the Talk room is kept", token, summary["comments"], summary["tombstones"],
             summary["system_skipped"], export)
    _ring(token)
    if DELETE_TALK_ROOM:
        try:
            await delete_talk_room(token, dry_run=False)
        except (RuntimeError, HTTPException) as exc:
            log.error("[private-chat] %s: the Talk room was kept (%s)", token, getattr(exc, "detail", exc))
    return "moved"


async def delete_talk_room(token: str, *, dry_run: bool) -> dict[str, Any]:
    """Delete a moved chat's Talk room, only when its comments still match the store. Logs what it deletes."""
    chat = private_chat.get_store().get(token)
    if not chat:
        raise HTTPException(status_code=404, detail="No such private chat.")
    if not is_store_chat(chat):
        raise HTTPException(status_code=409, detail="This chat has not moved off Talk; its room is its history.")
    if not chat.get("talk_room"):
        return {"deleted": False, "reason": "The chat has no Talk room."}
    owner = str(chat.get("created_by") or "")
    try:
        raw = await read_talk_room(token, owner)
        plan = _plan(raw, await private_chat._people())
        store = get_store()
        talk_ids = {r["id"] for r in plan["comments"] + plan["tombstones"]}
        stored = {i for i in await asyncio.to_thread(store.ids, token) if i <= plan["talk_max_id"]}
        if talk_ids != stored:
            raise RuntimeError(f"Talk has {len(talk_ids)} messages, the store {len(stored)} for the same ids")
        for record in plan["comments"]:
            got = await asyncio.to_thread(store.get, token, record["id"])
            # A message deleted here since the move is a tombstone now; anything else must be byte for byte Talk's.
            if got is None or (not got.get("deleted") and got.get("sealed") != record["sealed"]):
                raise RuntimeError(f"message {record['id']} differs between Talk and the store")
    except (RuntimeError, StoreUnreadable, aiohttp.ClientError, TimeoutError) as exc:
        log.warning("[private-chat] %s: Talk room not deleted: %s", token, exc)
        raise HTTPException(status_code=409, detail=f"The Talk room was not deleted: {exc}") from exc
    report = {"comments": len(plan["comments"]), "tombstones": len(plan["tombstones"]), "system": plan["system"]}
    if dry_run:
        log.info("[private-chat] %s: would delete the Talk room (%d messages, %d deleted, %d system lines); "
                 "all are in the store", token, report["comments"], report["tombstones"], report["system"])
        return {"deleted": False, "dry_run": True, "would_delete": report}
    log.info("[private-chat] %s: deleting the Talk room (%d messages, %d deleted, %d system lines); all are in the store",
             token, report["comments"], report["tombstones"], report["system"])
    status_code, result = await _talk_as(owner, {"action": "delete_conversation", "token": token, "delete_only": True})
    detail = result.get("detail") if isinstance(result.get("detail"), dict) else {}
    if not _talk_ok(status_code, result) or detail.get("deleted") is not True:
        log.warning("[private-chat] %s: Talk refused to delete the room (HTTP %s)", token, status_code)
        raise HTTPException(status_code=502, detail=result.get("message") or "Talk did not delete the room.")

    def gone(record: dict[str, Any]) -> None:
        record["talk_room"] = False
        record["talk_room_deleted_at"] = int(time.time())

    await _update_chat(token, gone)
    log.info("[private-chat] %s: Talk room deleted", token)
    return {"deleted": True, "deleted_messages": report}


async def migrate_all(*, sleep=asyncio.sleep) -> None:
    """Move every chat still on Talk, retrying failures with backoff until each has moved (gateway start)."""
    delay = MIGRATION_RETRY_START_S
    while True:
        pending = [c["token"] for c in private_chat.get_store().all() if not is_store_chat(c)]
        if not pending:
            return
        failed = 0
        async with _migrate_lock:
            for token in pending:
                try:
                    await migrate_chat(token)
                except Exception as exc:  # the chat stays on Talk; nothing readable in the message
                    failed += 1
                    log.error("[private-chat] %s: could not move off Talk, it stays there for now: %s: %s",
                              token, type(exc).__name__, getattr(exc, "detail", exc))
        if not failed:
            return
        log.info("[private-chat] retrying the move off Talk in %d s", delay)
        await sleep(delay)
        delay = min(delay * 2, MIGRATION_RETRY_MAX_S)


# --- Waiting ------------------------------------------------------------------


def _ring(token: str) -> None:
    bell = _bells.pop(token, None)
    if bell is not None:
        bell.set()


async def _newer(token: str, last_id: int, limit: int) -> list[dict[str, Any]]:
    return await asyncio.to_thread(get_store().page, token, limit, after=last_id)


# --- Notices ------------------------------------------------------------------


async def _tell_partner(token: str, chat: dict[str, Any], record: dict[str, Any], people: dict[str, dict[str, Any]]) -> None:
    """"New private message from Sam" to the other parent, if they have not read it a moment later. No content."""
    await asyncio.sleep(NOTICE_DELAY_S)
    user = record["by"]
    me = people.get(user) or {"username": user}
    try:
        markers = await asyncio.to_thread(get_store().read_markers, token)
    except Exception:
        markers = {}
    others = [m for m in chat.get("members", []) if m != user and markers.get(m, 0) < record["id"]]
    if not others:
        return
    url = f"/family?chat={token}"
    try:
        sent = await notify.send_many(
            others,
            lambda person: notify.notice(
                person, "chat", "Private chat", f"New private message from {private_chat._display(me)}",
                url=url, priority=4, tag=f"chat:{token}", actions=[notify.view_action(url)],
                data={"token": token, "message_id": record["id"], "private": True},
                once=f"chat:{token}:{record['id']}",
            ),
        )
    except Exception as exc:
        log.warning("[private-chat] %s: message notice failed: %s", token, type(exc).__name__)
        return
    if not sent:
        log.warning("[private-chat] %s: the message notice to %s was not delivered", token, ", ".join(others))


def _spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


# --- Routes -------------------------------------------------------------------


async def _body(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Send a JSON body.") from exc
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="Send a JSON body.")
    return body


def _int(value: Any, field: str, *, low: int = 0, high: int = 2**53) -> int | None:
    if value is None or value == "":
        return None
    try:
        out = int(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"{field} must be a whole number.") from exc
    if not low <= out <= high:
        raise HTTPException(status_code=422, detail=f"{field} is out of range.")
    return out


def _talk_response(status_code: int, result: dict[str, Any], what: str) -> dict[str, Any]:
    if status_code != 200 or result.get("status") != "SUCCESS":
        raise HTTPException(status_code=502, detail=result.get("message") or f"Could not {what}.")
    return {**result, "source": "talk"}


async def _talk_history(request: Request, token: str, limit: int) -> list[dict[str, Any]]:
    status_code, result = await private_chat._talk(request, {"action": "messages", "token": token, "limit": min(limit, TALK_PAGE)})
    if status_code != 200 or result.get("status") != "SUCCESS":
        raise HTTPException(status_code=502, detail=result.get("message") or "Could not load the messages.")
    detail = result.get("detail") if isinstance(result.get("detail"), dict) else {}
    return [m for m in detail.get("messages") or [] if isinstance(m, dict)]


@router.get("/chats/{token}/messages")
async def list_messages(token: str, request: Request, limit: int = 50, before: str | None = None, after: str | None = None):
    """The thread, oldest first: the newest ``limit`` (below ``before``), or those after ``after``. Members only."""
    user, chat, people = await private_chat._member_chat(request, token)
    limit = max(1, min(int(limit), MAX_PAGE))
    before_id, after_id = _int(before, "before", low=1), _int(after, "after")
    if not is_store_chat(chat):
        return _ok(await _talk_history(request, token, limit), "talk")
    store = get_store()
    migration = chat.get("migration") if isinstance(chat.get("migration"), dict) else {}
    try:
        ids = await asyncio.to_thread(store.ids, token)
        missing = set(migration.get("ids") or []) - set(ids)
        if missing:
            raise StoreUnreadable(f"{len(missing)} moved messages are missing")
        records = await asyncio.to_thread(store.page, token, limit, before=before_id, after=after_id)
        markers = await asyncio.to_thread(store.read_markers, token)
    except StoreUnreadable as exc:
        if not chat.get("talk_room") or not migration:
            log.error("[private-chat] %s: the message store is unreadable (%s)", token, exc)
            raise HTTPException(status_code=503, detail="The messages could not be read; try again.") from exc
        # The Talk room still has everything that was moved: show that rather than an empty or shorter history.
        log.error("[private-chat] %s: the message store is unreadable (%s); reading the moved history from Talk", token, exc)
        floor = int(migration.get("talk_max_id") or 0)
        older = await _talk_history(request, token, limit)
        try:
            newer = [public(r, token, people) for r in await asyncio.to_thread(store.page, token, limit, after=floor)]
        except StoreUnreadable:
            newer = []
        merged = [m for m in older if isinstance(m.get("id"), int) and m["id"] <= floor] + newer
        return _ok(merged[-limit:], "talk")
    return _ok([public(r, token, people) for r in records], "store",
               last_read=markers.get(user, 0), last_common_read=_common_read(chat, markers))


@router.get("/chats/{token}/messages/wait")
async def wait_messages(token: str, request: Request, last_id: int = 0, wait: int = 25):
    """Answer as soon as a message newer than ``last_id`` is stored, or empty after ``wait`` seconds."""
    _user, chat, people = await private_chat._member_chat(request, token)
    wait = max(1, min(int(wait), MAX_WAIT_S))
    if not is_store_chat(chat):
        status_code, result = await private_chat._talk(request, {
            "action": "wait", "token": token, "last_known_id": max(1, int(last_id)), "wait_s": min(wait, 20)})
        return _talk_response(status_code, result, "wait for messages")
    deadline = time.monotonic() + wait
    while True:
        bell = _bells.setdefault(token, asyncio.Event())
        try:
            found = await _newer(token, int(last_id), MAX_PAGE)
        except StoreUnreadable as exc:
            raise HTTPException(status_code=503, detail="The messages could not be read; try again.") from exc
        remaining = deadline - time.monotonic()
        if found or remaining <= 0 or await request.is_disconnected():
            return _ok([public(r, token, people) for r in found], "store")
        with contextlib.suppress(asyncio.TimeoutError, TimeoutError):
            await asyncio.wait_for(bell.wait(), timeout=min(remaining, 5.0))


@router.post("/chats/{token}/messages")
async def send_message(token: str, request: Request):
    """Keep one sealed message (``{"sealed", "reply_to"?}``) and wake everyone waiting on the chat."""
    user, chat, people = await private_chat._member_chat(request, token)
    body = await _body(request)
    sealed = _sealed(body.get("sealed"))
    reply_to = _int(body.get("reply_to"), "reply_to", low=1)
    if not is_store_chat(chat):
        status_code, result = await private_chat._talk(request, {
            "action": "send", "token": token, "message": sealed, **({"reply_to": reply_to} if reply_to else {})})
        return _talk_response(status_code, result, "send the message")
    store = get_store()
    if reply_to is not None and await asyncio.to_thread(store.get, token, reply_to) is None:
        raise HTTPException(status_code=422, detail="That reply is to a message this chat does not have.")
    try:
        record = await asyncio.to_thread(store.append, token, user, sealed, reply_to)
    except OSError as exc:
        log.error("[private-chat] %s: could not store a message: %s", token, type(exc).__name__)
        raise HTTPException(status_code=500, detail="Could not save the message (PRIVATE_CHAT_DIR not writable).") from exc
    # The sender has read what they just sent.
    await asyncio.to_thread(store.mark_read, token, user, record["id"])
    _ring(token)
    _spawn(_tell_partner(token, chat, record, people))
    return {"status": "SUCCESS", "message": "Sent.", "source": "store", "detail": {"message": public(record, token, people)}}


@router.delete("/chats/{token}/messages/{message_id}")
async def delete_message(token: str, message_id: str, request: Request):
    """Delete a message for both parents (its author only, at any age): ciphertext gone, a tombstone kept, its marks gone."""
    user, chat, _people = await private_chat._member_chat(request, token)
    mid = _int(message_id, "message_id", low=1)
    if mid is None:
        raise HTTPException(status_code=404, detail="No such message.")
    if not is_store_chat(chat):
        status_code, result = await private_chat._talk(request, {"action": "delete_message", "token": token, "message_id": mid})
        response = _talk_response(status_code, result, "delete the message")
        await asyncio.to_thread(private_chat_marks.forget_message, token, mid)
        return response
    store = get_store()
    record = await asyncio.to_thread(store.get, token, mid)
    if record is None or record.get("deleted"):
        raise HTTPException(status_code=404, detail="No such message.")
    if record.get("by") != user:
        raise HTTPException(status_code=403, detail="Only the one who wrote it can delete it.")
    await asyncio.to_thread(store.tombstone, token, mid)
    await asyncio.to_thread(private_chat_marks.forget_message, token, mid)
    _ring(token)
    log.info("[private-chat] %s: message %s deleted by its author", token, mid)
    return {"status": "SUCCESS", "message": "Message deleted.", "source": "store", "detail": {"deleted": mid}}


@router.post("/chats/{token}/read")
async def mark_read(token: str, request: Request):
    """Move the caller's read marker up to ``last_id`` (or to the newest message). It never moves back."""
    user, chat, _people = await private_chat._member_chat(request, token)
    try:
        body = await request.json() if await request.body() else {}
    except ValueError:
        body = {}
    upto = _int((body or {}).get("last_id") if isinstance(body, dict) else None, "last_id", low=1)
    if not is_store_chat(chat):
        status_code, result = await private_chat._talk(request, {"action": "mark_read", "token": token})
        return _talk_response(status_code, result, "mark the chat read")
    store = get_store()
    ids = await asyncio.to_thread(store.ids, token)
    newest = ids[-1] if ids else 0
    marker = await asyncio.to_thread(store.mark_read, token, user, min(upto or newest, newest))
    return {"status": "SUCCESS", "message": "Marked as read.", "source": "store",
            "detail": {"token": token, "last_read": marker, "unread_messages": 0}}


@router.post("/admin/chats/{token}/delete-talk-room")
async def admin_delete_talk_room(token: str, request: Request, dry_run: bool = False):
    """Delete a moved chat's leftover Talk room (admins). Refuses unless every message in it is in the store."""
    from services.gateway.main import _require_admin

    admin = await _require_admin(request)
    log.info("[private-chat] %s: %s asked to delete the Talk room%s", token, admin, " (dry run)" if dry_run else "")
    return await delete_talk_room(token, dry_run=dry_run)


# --- What other modules ask ---------------------------------------------------


def latest(token: str) -> dict[str, Any] | None:
    """The newest live message of a moved chat (for the nudge), or None."""
    store = get_store()
    for mid in reversed(store.ids(token)):
        record = store.get(token, mid)
        if record and not record.get("deleted"):
            return record
    return None


def author(token: str, message_id: int) -> str | None:
    """Who wrote a live stored message, or None when there is no such message."""
    record = get_store().get(token, message_id)
    if not record or record.get("deleted"):
        return None
    return str(record.get("by") or "") or None

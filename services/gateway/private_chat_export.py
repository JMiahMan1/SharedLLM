"""Back up, export and restore the parents' private chat, from the app. Never readable without the password.

The nightly backup (scripts/private_chat_backup.py) already encrypts each
private chat to its backup public key. These routes let a member do the same
on demand and put a chat back from such a file:

Export (``GET /api/private-chat/chats/<token>/export``)
    The chat's backup file, made by the nightly encrypter's own code
    (``write_backup``, loaded from scripts/private_chat_backup.py, so the format
    cannot drift): the chat's folder streamed as a tar straight through the
    encryption into the response. No clear tar touches the disk, and the
    response is ciphertext from its first byte. Members only, a few exports
    per chat per hour (``EXPORT_LIMIT`` per ``EXPORT_WINDOW_S``) and one at a
    time. The file name carries a date and nothing else.

Opening and reading a backup happen only on a device (the app's
lib/privateChatBackupCore.js): the password unwraps the chat's data key from
the key records in the file's header, which unseals the backup private key,
which opens the file. This server never sees a password, a key or a word.

Restore (a member, or a parent setting up a new server)
    1. ``POST /api/private-chat/restore`` with the chat's registry record from
       the backup (``chats/<token>.json``: members, key records, the backup
       key: exactly the public fields this server keeps for any chat).
       * A chat with that token here: the caller must be one of its members,
         the members must be the backup's, and its verifier must be the
         backup's (otherwise the backup is of another key and nothing in it
         would open). If it already has data, ``merge: true`` is required
         (409 with the counts otherwise).
       * No such chat: it is made again for the two people the record names,
         if both are parents here and they have no other private chat together;
         otherwise 409 says what is missing.
       Either way a restore session opens for the caller (``RESTORE_SESSION_S``)
       and the other parent is told, with no content, that a backup was restored.
    2. ``GET  .../restore/have``: the blob ids already here, so they are not sent again.
    3. ``PUT  .../restore/blobs/<id>``: one encrypted blob, under its own id.
    4. ``POST .../restore/records``: sealed records in batches: messages
       (with their ids, authors, times, replies, tombstones and blob lists),
       gallery entries, edits and reactions, Beloved records, read markers and
       the id floor.
    5. ``POST .../restore/finish``: closes the session.

Merging is by id and never duplicates: a record already here is kept as it
is (a message is never overwritten, a deletion here stays deleted); a
reaction is added only if its id is new (``seen_by`` is united); an edit is
added only to a message with none; a Beloved record keeps whichever version
was changed last. Every record must be a well-formed sealed envelope (the
same checks as when it was first sent), its author one of the chat's two
members, and every blob an opaque ciphertext of a blob's size: nothing
readable is accepted. The Talk migration exports a backup may carry are not
restored (they are the record of a one-off move, not part of the chat).

What this cannot check: the content of a sealed record (who really wrote it
is inside nothing the server can read). A member could therefore add records
in the other's name during a session; that is why a session is short, only
for the member who opened it, and announced to the other parent.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import importlib.util
import json
import logging
import queue
import re
import sys
import threading
import time
from collections.abc import AsyncIterator
from pathlib import Path
from types import ModuleType
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from services.common import notify
from services.gateway import beloved, private_chat, private_chat_marks, private_chat_messages

log = logging.getLogger("gateway.private_chat_export")

router = APIRouter(prefix="/api/private-chat")

#: Exports per chat in a window (either member), and the window.
EXPORT_LIMIT = 4
EXPORT_WINDOW_S = 3600
#: How long a restore session stays open after it was begun.
RESTORE_SESSION_S = 2 * 3600
#: Records one restore call may carry, of every kind together.
MAX_RESTORE_RECORDS = 2000
#: Ciphertext chunks waiting between the encrypting thread and the response.
_QUEUE_DEPTH = 4

_FORMAT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "private_chat_backup.py"
_format_module: ModuleType | None = None
_exports: dict[str, list[float]] = {}
_exporting: set[str] = set()
_export_lock = threading.Lock()
_restore_lock = asyncio.Lock()
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def backup_format() -> ModuleType:
    """scripts/private_chat_backup.py, the one implementation of the backup format (it is in the image)."""
    global _format_module
    if _format_module is None:
        existing = sys.modules.get("private_chat_backup")
        if existing is not None and Path(getattr(existing, "__file__", "")).resolve() == _FORMAT_PATH:
            _format_module = existing
        else:
            spec = importlib.util.spec_from_file_location("services_gateway_private_chat_backup_format", _FORMAT_PATH)
            if spec is None or spec.loader is None:
                raise RuntimeError(f"cannot load the backup format from {_FORMAT_PATH}")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            _format_module = module
    return _format_module


# --- Export -------------------------------------------------------------------


def _claim_export(token: str, now: float) -> int:
    """0 when an export may start (and it is counted); otherwise seconds to wait. -1 when one is running."""
    with _export_lock:
        if token in _exporting:
            return -1
        recent = [t for t in _exports.get(token, []) if now - t < EXPORT_WINDOW_S]
        if len(recent) >= EXPORT_LIMIT:
            _exports[token] = recent
            return max(1, int(EXPORT_WINDOW_S - (now - min(recent))))
        _exports[token] = [*recent, now]
        _exporting.add(token)
        return 0


def _release_export(token: str) -> None:
    with _export_lock:
        _exporting.discard(token)


class _Cancelled(Exception):
    """The download went away; the encrypting thread stops."""


class _QueueWriter:
    """What the encrypter writes into: ciphertext handed to the response, never a file."""

    def __init__(self, sink: queue.Queue, cancelled: threading.Event):
        self._sink = sink
        self._cancelled = cancelled
        self.written = 0

    def write(self, data: bytes) -> int:
        block = bytes(data)
        while True:
            if self._cancelled.is_set():
                raise _Cancelled
            try:
                self._sink.put(block, timeout=0.5)
                break
            except queue.Full:
                continue
        self.written += len(block)
        return len(block)


def export_filename(now: float | None = None) -> str:
    """``private-chat-<date>.jarvis-backup``: a date and nothing else, never a name."""
    return f"private-chat-{time.strftime('%Y-%m-%d', time.localtime(now))}{backup_format().SUFFIX}"


async def _stream(token: str, chat: dict[str, Any]) -> AsyncIterator[bytes]:
    fmt = backup_format()
    root = private_chat.get_store().root
    sink: queue.Queue = queue.Queue(maxsize=_QUEUE_DEPTH)
    cancelled = threading.Event()
    done = object()

    def produce() -> None:
        writer = _QueueWriter(sink, cancelled)
        try:
            count = fmt.write_backup(writer, chat, fmt._chat_files(root, token))
            log.info("[private-chat] %s exported (%d file(s), %d bytes encrypted)", token, count, writer.written)
            outcome: object = done
        except _Cancelled:
            log.info("[private-chat] %s export cancelled by the client", token)
            return
        except Exception as exc:
            log.error("[private-chat] %s export failed: %s", token, type(exc).__name__)
            outcome = exc
        while not cancelled.is_set():
            try:
                sink.put(outcome, timeout=0.5)
                return
            except queue.Full:
                continue

    worker = threading.Thread(target=produce, name=f"private-chat-export-{token}", daemon=True)
    worker.start()
    try:
        while True:
            item = await asyncio.to_thread(sink.get)
            if item is done:
                return
            if isinstance(item, BaseException):
                # The response has started: all that can be done is to stop. The file is then
                # missing its final chunk, which every reader refuses as truncated.
                return
            yield item
    finally:
        cancelled.set()
        with contextlib.suppress(queue.Empty):
            while True:
                sink.get_nowait()
        _release_export(token)


@router.get("/chats/{token}/export")
async def export_chat(token: str, request: Request):
    """The chat's encrypted backup file, streamed. Only the chat password opens it (docs/PRIVATE_CHAT_BACKUP.md)."""
    user, chat, _people = await private_chat._member_chat(request, token)
    if not isinstance(chat.get("backup_key"), dict):
        raise HTTPException(status_code=409, detail="This chat has no backup key yet. Unlock it once on this device and try again.")
    if chat.get("storage") != "store":
        raise HTTPException(status_code=409, detail="This chat's messages are still being moved into Jarvis; try again later.")
    fmt = backup_format()
    try:
        fmt.make_header(chat, payload="chat")  # checks the key before anything is sent
    except fmt.BackupError as exc:
        raise HTTPException(status_code=409, detail=f"The chat's backup key cannot be used ({exc}).") from exc
    now = time.time()
    wait = _claim_export(token, now)
    if wait == -1:
        raise HTTPException(status_code=409, detail="A backup of this chat is already being made.")
    if wait:
        return JSONResponse(
            status_code=429,
            content={"detail": f"Too many backups of this chat; try again in {max(1, round(wait / 60))} min.", "retry_after": wait},
            headers={"Retry-After": str(wait)},
        )
    log.info("[private-chat] %s: %s is exporting the chat", token, user)
    name = export_filename(now)
    return StreamingResponse(
        _stream(token, chat),
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{name}"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "X-Backup-Filename": name,
        },
    )


# --- Restore: the registry record ----------------------------------------------


def _int(value: Any, field: str, low: int = 0, high: int = 2**53) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
        raise HTTPException(status_code=422, detail=f"{field} must be a whole number.")
    return value


def _key_record(fields: Any, field: str, *, current: bool) -> dict[str, Any]:
    """One key record (the current one, or a history entry) with only its public fields, shape-checked."""
    if not isinstance(fields, dict):
        raise HTTPException(status_code=422, detail=f"{field} is not a key record.")
    if fields.get("version") == 2 or fields.get("wrapped_key"):
        record = private_chat._wrapping(fields)
        auth_hash = fields.get("auth_hash")
        if isinstance(auth_hash, str) and _HEX64.match(auth_hash):
            record["auth_hash"] = auth_hash
        elif current:
            # Without it the password could never be changed again.
            raise HTTPException(status_code=422, detail=f"{field}.auth_hash must be a sha256.")
    else:
        salt = private_chat._b64(fields.get("salt"), f"{field}.salt")
        if len(salt) != private_chat.SALT_BYTES:
            raise HTTPException(status_code=422, detail=f"{field}.salt must be {private_chat.SALT_BYTES} bytes.")
        record = {"version": 1, "salt": fields["salt"],
                  "iterations": private_chat._iterations(fields.get("iterations"), f"{field}.iterations"),
                  "verifier": private_chat._verifier(fields.get("verifier"))}
    if not current:
        for extra in ("replaced_at",):
            if isinstance(fields.get(extra), int):
                record[extra] = fields[extra]
        if isinstance(fields.get("replaced_by"), str) and private_chat._USER.match(fields["replaced_by"]):
            record["replaced_by"] = fields["replaced_by"]
    return record


def _backup_key(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise HTTPException(status_code=422, detail=f"{field} is required.")
    raw = private_chat._backup_public_key(value.get("alg"), value.get("public_key"))
    out = {
        "alg": value["alg"],
        "public_key": value["public_key"],
        "sealed_private_key": private_chat._sealed_backup_key(value.get("sealed_private_key")),
        "key_id": hashlib.sha256(raw).hexdigest()[:32],
        "created_at": _int(value.get("created_at"), f"{field}.created_at"),
    }
    for extra in ("created_by", "retired_by"):
        if isinstance(value.get(extra), str) and private_chat._USER.match(value[extra]):
            out[extra] = value[extra]
    if isinstance(value.get("retired_at"), int):
        out["retired_at"] = value["retired_at"]
    return out


def restored_registry(raw: Any) -> dict[str, Any]:
    """The registry record a backup carries, reduced to the public fields this server keeps for a chat."""
    if not isinstance(raw, dict):
        raise HTTPException(status_code=422, detail="registry is required: the chat's record from the backup.")
    token = str(raw.get("token") or "")
    if not private_chat._TOKEN.match(token):
        raise HTTPException(status_code=422, detail="That backup does not name a chat.")
    members = raw.get("members")
    if (not isinstance(members, list) or len(members) != 2 or len(set(map(str, members))) != 2
            or not all(isinstance(m, str) and private_chat._USER.match(m) for m in members)):
        raise HTTPException(status_code=422, detail="A private chat has two members.")
    created_by = raw.get("created_by") if raw.get("created_by") in members else members[0]
    record: dict[str, Any] = {
        "token": token,
        "members": sorted(members),
        "created_by": created_by,
        "created_at": _int(raw.get("created_at"), "created_at") if raw.get("created_at") is not None else int(time.time()),
        "storage": "store",
        **_key_record(raw, "registry", current=True),
    }
    history = raw.get("history") or []
    if not isinstance(history, list):
        raise HTTPException(status_code=422, detail="history must be a list.")
    if history:
        record["history"] = [_key_record(h, f"history[{i}]", current=False)
                             for i, h in enumerate(history[-private_chat.PASSWORD_HISTORY_KEEP:])]
    record["backup_key"] = _backup_key(raw.get("backup_key"), "backup_key")
    retired = raw.get("backup_keys_retired") or []
    if isinstance(retired, list) and retired:
        record["backup_keys_retired"] = [_backup_key(k, f"backup_keys_retired[{i}]")
                                         for i, k in enumerate(retired[-private_chat.BACKUP_KEYS_RETIRED_KEEP:])]
    return record


def _has_data(token: str) -> dict[str, int]:
    """How much of a chat is here already (counts only)."""
    root = private_chat.get_store().root
    counts = {"messages": len(private_chat_messages.get_store().ids(token))}
    for kind, pattern in (("pictures", "*"), ("gallery", "*.json"), ("marks", "*.json")):
        folder = root / ("blobs" if kind == "pictures" else kind) / token
        counts[kind] = sum(1 for p in folder.glob(pattern) if p.is_file() and not p.name.endswith(".tmp")) if folder.is_dir() else 0
    folder = root / "beloved" / token
    counts["beloved"] = sum(1 for p in folder.glob("*/*.json") if p.is_file()) if folder.is_dir() else 0
    return counts


async def _announce(chat: dict[str, Any], user: str, people: dict[str, dict[str, Any]]) -> None:
    me = people.get(user) or {"username": user}
    others = [m for m in chat["members"] if m != user]
    url = f"/family?chat={chat['token']}"
    try:
        await notify.send_many(
            others,
            lambda person: notify.notice(
                person, "chat", "Private chat", f"{private_chat._display(me)} restored the private chat from a backup",
                url=url, priority=3, tag=f"private-restore:{chat['token']}",
                actions=[notify.view_action(url)], data={"token": chat["token"], "private": True, "restored": True},
            ),
        )
    except Exception as exc:
        log.warning("[private-chat] %s: the restore notice was not delivered (%s)", chat["token"], type(exc).__name__)


@router.post("/restore")
async def begin_restore(request: Request):
    """Open a restore of a chat from its backup (re-creating it if this server has none). See the module docstring."""
    try:
        body = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="A JSON body is required.") from exc
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="A JSON body is required.")
    user, people = await private_chat._caller(request)
    if not private_chat.is_parent(people.get(user)):
        raise HTTPException(status_code=403, detail=private_chat.NOT_AVAILABLE)
    record = restored_registry(body.get("registry"))
    token = record["token"]
    if user not in record["members"]:
        raise HTTPException(status_code=403, detail="This backup is of a private chat you are not in.")
    merge = body.get("merge") is True
    store = private_chat.get_store()
    now = int(time.time())
    async with _restore_lock:
        existing = store.get(token)
        if existing:
            if user not in existing.get("members", []):
                raise HTTPException(status_code=404, detail=private_chat.NOT_AVAILABLE)
            if sorted(existing.get("members", [])) != record["members"]:
                raise HTTPException(status_code=409, detail="This backup is of a chat with someone else; nothing was restored.")
            if existing.get("verifier") != record["verifier"]:
                raise HTTPException(status_code=409, detail="This backup was made with another key than this chat's; nothing was restored.")
            if existing.get("storage") != "store":
                raise HTTPException(status_code=409, detail="This chat's messages are still being moved into Jarvis; try again later.")
            counts = await asyncio.to_thread(_has_data, token)
            if any(counts.values()) and not merge:
                return JSONResponse(status_code=409, content={
                    "detail": "This chat already has messages here. Merge the backup into it? Nothing is duplicated or overwritten.",
                    "needs_merge": True, "counts": counts})
            chat = existing
            created = False
        else:
            missing = [m for m in record["members"] if not private_chat.is_parent(people.get(m))]
            if missing:
                raise HTTPException(status_code=409, detail=(
                    f"This backup is of a private chat between {' and '.join(record['members'])}. "
                    f"{' and '.join(missing)} {'is' if len(missing) == 1 else 'are'} not a parent with a chat account on this "
                    "server yet. Add them (father or mother, with a chat account) and restore again."))
            other = next((c for c in store.all() if sorted(c.get("members", [])) == record["members"]), None)
            if other:
                return JSONResponse(status_code=409, content={
                    "detail": ("You already have a different private chat together here. A backup can only go back into the "
                               "chat it was made from; delete that one first to restore this one."),
                    "token": other["token"]})
            chat = {**record, "restored_at": now, "restored_by": user}
            created = True
        chat["restore"] = {"by": user, "at": now, "until": now + RESTORE_SESSION_S}
        try:
            store.save(chat)
        except OSError as exc:
            log.error("[private-chat] could not save the restored chat %s: %s", token, exc)
            raise HTTPException(status_code=500, detail="Could not save the chat (PRIVATE_CHAT_DIR not writable).") from exc
    log.info("[private-chat] %s: %s began a restore (%s)", token, user, "new chat" if created else "merge")
    await _announce(chat, user, people)
    return {**private_chat._public(chat, user, people), "created": created, "merge": not created,
            "session_until": chat["restore"]["until"]}


async def _session(request: Request, token: str) -> tuple[str, dict[str, Any]]:
    """The member running an open restore of this chat (409 when none is open for them)."""
    user, chat, _people = await private_chat._member_chat(request, token)
    session = chat.get("restore") if isinstance(chat.get("restore"), dict) else None
    if not session or session.get("by") != user or int(session.get("until") or 0) < time.time():
        raise HTTPException(status_code=409, detail="Open the backup again to restore it (the restore was not started or has ended).")
    return user, chat


@router.get("/chats/{token}/restore/have")
async def restore_have(token: str, request: Request):
    """The blob ids this chat already has, so a restore does not send them again."""
    await _session(request, token)
    folder = private_chat.get_store()._blob_dir(token)
    ids = sorted(p.name for p in folder.iterdir() if private_chat._BLOB_ID.match(p.name)) if folder.is_dir() else []
    return {"blobs": ids}


def _put_blob(token: str, blob_id: str, data: bytes) -> str:
    path = private_chat.get_store().blob_path(token, blob_id)
    if path.is_file():
        return "same" if path.read_bytes() == data else "kept"
    try:
        private_chat_messages.write_atomic(path, data, exclusive=True)
    except FileExistsError:
        return "same" if path.read_bytes() == data else "kept"
    return "added"


@router.put("/chats/{token}/restore/blobs/{blob_id}")
async def restore_blob(token: str, blob_id: str, request: Request):
    """One encrypted blob from the backup, under its own id. One already here is never replaced."""
    await _session(request, token)
    if not private_chat._BLOB_ID.match(blob_id or ""):
        raise HTTPException(status_code=422, detail="That is not a blob id.")
    data = await request.body()
    if len(data) <= private_chat.IV_BYTES + private_chat.TAG_BYTES:
        raise HTTPException(status_code=422, detail="That is not an encrypted picture.")
    if len(data) > private_chat.MAX_BLOB_BYTES:
        raise HTTPException(status_code=413, detail="That blob is larger than any picture or piece of a clip.")
    result = await asyncio.to_thread(_put_blob, token, blob_id, data)
    return {"id": blob_id, "result": result}


# --- Restore: sealed records ---------------------------------------------------


def _author(value: Any, members: list[str], field: str) -> str:
    if not isinstance(value, str) or value not in members:
        raise HTTPException(status_code=422, detail=f"{field} must be one of the chat's members.")
    return value


def _message(raw: Any, members: list[str]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise HTTPException(status_code=422, detail="A message must be a record.")
    mid = _int(raw.get("id"), "message id", 1, 10**16 - 1)
    record: dict[str, Any] = {"id": mid, "by": _author(raw.get("by"), members, "by"), "at": _int(raw.get("at"), "at")}
    reply = raw.get("reply_to")
    record["reply_to"] = _int(reply, "reply_to", 1, 10**16 - 1) if reply is not None else None
    if raw.get("deleted") is True:
        record.update({"sealed": None, "deleted": True})
        if isinstance(raw.get("deleted_at"), int):
            record["deleted_at"] = raw["deleted_at"]
        return record
    record.update({"sealed": private_chat_messages._sealed(raw.get("sealed")), "deleted": False})
    blobs = raw.get("blobs")
    if blobs is not None:
        if (not isinstance(blobs, list) or len(blobs) > private_chat_messages.MAX_BLOBS_PER_MESSAGE
                or not all(isinstance(b, str) and private_chat._BLOB_ID.match(b) for b in blobs)):
            raise HTTPException(status_code=422, detail="blobs must be blob ids.")
        if blobs:
            record["blobs"] = list(blobs)
    return record


def _gallery_entry(raw: Any, members: list[str]) -> dict[str, Any]:
    if not isinstance(raw, dict) or not private_chat._BLOB_ID.match(str(raw.get("id") or "")):
        raise HTTPException(status_code=422, detail="A gallery entry needs its id.")
    sealed = private_chat_marks._sealed(raw.get("sealed"), private_chat.MAX_GALLERY_ENTRY_BYTES)
    return {"id": raw["id"], "by": _author(raw.get("by"), members, "by"), "at": _int(raw.get("at"), "at"), "sealed": sealed}


def _mark(raw: Any, members: list[str]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise HTTPException(status_code=422, detail="A mark must be a record.")
    mark: dict[str, Any] = {"message_id": _int(raw.get("message_id"), "message_id", 1, 10**16 - 1), "reactions": []}
    edit = raw.get("edit")
    if edit:
        if not isinstance(edit, dict):
            raise HTTPException(status_code=422, detail="edit must be a record.")
        mark["edit"] = {"by": _author(edit.get("by"), members, "edit.by"), "at": _int(edit.get("at"), "edit.at"),
                        "sealed": private_chat_marks._sealed(edit.get("sealed"), private_chat_marks.MAX_EDIT_BYTES)}
    reactions = raw.get("reactions") or []
    if not isinstance(reactions, list) or len(reactions) > 2 * private_chat_marks.MAX_REACTIONS_PER_PERSON:
        raise HTTPException(status_code=422, detail="reactions must be a short list.")
    for r in reactions:
        if not isinstance(r, dict) or not private_chat_marks._ID.match(str(r.get("id") or "")):
            raise HTTPException(status_code=422, detail="A reaction needs its id.")
        seen = [s for s in (r.get("seen_by") or []) if s in members] if isinstance(r.get("seen_by"), list) else []
        by = _author(r.get("by"), members, "reaction.by")
        mark["reactions"].append({
            "id": r["id"], "by": by, "at": _int(r.get("at"), "reaction.at"),
            "sealed": private_chat_marks._sealed(r.get("sealed"), private_chat_marks.MAX_REACTION_BYTES),
            "seen_by": sorted(set([by, *seen]), key=[by, *seen].index),
        })
    return mark


def _beloved_record(raw: Any, members: list[str]) -> dict[str, Any]:
    if not isinstance(raw, dict) or raw.get("kind") not in beloved.KINDS or not beloved._ID.match(str(raw.get("id") or "")):
        raise HTTPException(status_code=422, detail="A Beloved record needs its kind and id.")
    by = _author(raw.get("by"), members, "by")
    updated_by = raw.get("updated_by") if raw.get("updated_by") in members else by
    at = _int(raw.get("at"), "at")
    return {"id": raw["id"], "kind": raw["kind"], "by": by, "at": at,
            "updated_at": _int(raw.get("updated_at", at), "updated_at"), "updated_by": updated_by,
            "sealed": private_chat_marks._sealed(raw.get("sealed"), beloved.MAX_RECORD_BYTES)}


def _apply(token: str, chat: dict[str, Any], body: dict[str, Any]) -> dict[str, dict[str, int]]:
    """Write a checked batch, merging by id. Returns per kind how many were added, already the same, or kept as they were."""
    members = [str(m) for m in chat["members"]]
    batch = {kind: body.get(kind) or [] for kind in ("messages", "gallery", "marks", "beloved")}
    if not all(isinstance(v, list) for v in batch.values()):
        raise HTTPException(status_code=422, detail="Each kind of record is a list.")
    if sum(len(v) for v in batch.values()) > MAX_RESTORE_RECORDS:
        raise HTTPException(status_code=413, detail=f"Send at most {MAX_RESTORE_RECORDS} records at a time.")
    # Check everything first: one bad record refuses the whole batch, so nothing is half written.
    messages = [_message(m, members) for m in batch["messages"]]
    gallery = [_gallery_entry(g, members) for g in batch["gallery"]]
    marks = [_mark(m, members) for m in batch["marks"]]
    records = [_beloved_record(b, members) for b in batch["beloved"]]
    read = body.get("read") or {}
    if not isinstance(read, dict):
        raise HTTPException(status_code=422, detail="read must map a member to a message id.")
    markers = {m: _int(v, "read", 0, 10**16) for m, v in read.items() if m in members}
    floor = _int(body.get("floor") or 0, "floor", 0, 10**16)

    out = {kind: {"added": 0, "same": 0, "kept": 0} for kind in ("messages", "gallery", "marks", "beloved")}
    root = private_chat.get_store().root
    msg_store = private_chat_messages.get_store()
    with private_chat_messages._write_lock:
        for record in messages:
            path = msg_store._path(token, record["id"])
            existing = msg_store.get(token, record["id"])
            if existing is None:
                private_chat_messages.write_atomic(path, json.dumps(record, sort_keys=True).encode(), exclusive=True)
                out["messages"]["added"] += 1
            else:
                out["messages"]["same" if existing == record else "kept"] += 1
        if markers:
            current = msg_store.read_markers(token)
            merged = {**current, **{m: max(v, current.get(m, 0)) for m, v in markers.items()}}
            if merged != current:
                private_chat_messages.write_atomic(msg_store._dir(token) / "read.json",
                                                   json.dumps(merged, sort_keys=True).encode())
        if floor > msg_store.floor(token):
            private_chat_messages.write_atomic(msg_store._dir(token) / "meta.json", json.dumps({"floor": floor}).encode())

    for entry in gallery:
        path = root / "gallery" / token / f"{entry['id']}.json"
        if path.is_file():
            out["gallery"]["same" if json.loads(path.read_text()) == entry else "kept"] += 1
            continue
        private_chat_messages.write_atomic(path, json.dumps(entry).encode())
        out["gallery"]["added"] += 1

    marks_store = private_chat_marks.get_store()
    for mark in marks:
        current = marks_store.get(token, mark["message_id"])
        changed = False
        if mark.get("edit") and not current.get("edit"):
            current["edit"] = mark["edit"]
            changed = True
        have = {r.get("id"): r for r in current.get("reactions") or [] if isinstance(r, dict)}
        for reaction in mark["reactions"]:
            mine = have.get(reaction["id"])
            if mine is None:
                current.setdefault("reactions", []).append(reaction)
                changed = True
            else:
                seen = [str(s) for s in mine.get("seen_by") or []]
                more = [s for s in reaction["seen_by"] if s not in seen]
                if more:
                    mine["seen_by"] = [*seen, *more]
                    changed = True
        if changed:
            current["message_id"] = mark["message_id"]
            marks_store.put(token, current)
            out["marks"]["added"] += 1
        else:
            out["marks"]["same"] += 1

    store = beloved.BelovedStore(root)
    for record in records:
        existing = store.get(token, record["kind"], record["id"])
        if existing == record:
            out["beloved"]["same"] += 1
        elif existing is None or int(existing.get("updated_at") or 0) < record["updated_at"]:
            folder = store._dir(token, record["kind"])
            folder.mkdir(parents=True, exist_ok=True)
            store._write(folder / f"{record['id']}.json", record)
            out["beloved"]["added"] += 1
        else:
            out["beloved"]["kept"] += 1
    return out


@router.post("/chats/{token}/restore/records")
async def restore_records(token: str, request: Request):
    """A batch of sealed records from the backup (see the module docstring for what each may hold)."""
    try:
        body = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="A JSON body is required.") from exc
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="A JSON body is required.")
    _user, chat = await _session(request, token)
    async with _restore_lock:
        result = await asyncio.to_thread(_apply, token, chat, body)
    return {"token": token, "result": result}


@router.post("/chats/{token}/restore/finish")
async def finish_restore(token: str, request: Request):
    """Close the restore session. What is here now is the chat."""
    user, _chat = await _session(request, token)
    async with _restore_lock:
        store = private_chat.get_store()
        chat = store.get(token)
        if chat:
            chat.pop("restore", None)
            chat["last_restored_at"] = int(time.time())
            chat["last_restored_by"] = user
            store.save(chat)
    counts = await asyncio.to_thread(_has_data, token)
    log.info("[private-chat] %s: %s finished a restore (%s)", token, user, counts)
    return {"token": token, "counts": counts}

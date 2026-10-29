"""Device management (MDM): a parent's view and control of the family's phones.

Phase 12 (HANDOVER.md). This module adds the pieces that did not exist:

* ``GET  /api/mdm/devices``            - every family device in one list.
* ``POST /api/mdm/devices/update``     - push an app-update notice to selected devices.
* ``POST /api/mdm/devices/notices/ack``- a phone reports the notices it has shown.
* ``POST /api/mdm/devices/{key}/wipe`` - queue a remote wipe on a device-owner phone.
* ``GET/POST/DELETE /api/mdm/files*``  - the shared School and Library folders.
* ``GET  /api/mdm/library/books``      - the read-only family Library set (Calibre).
* ``POST /api/mdm/library/books/file`` - one Library book's bytes, read-only.
* ``GET  /api/mdm/audit``              - who did what, and when.

The Library set is sourced from the read-only Calibre shelf
(docs/CALIBRE_LIBRARY.md). The gateway does not talk WebDAV itself: it browses
the configured ``calibre_library_path`` through the storage service's existing
provider, which owns the Nextcloud credentials, so the shelf is read the same
way the RAG index and the Bible importer read it. Nothing here writes to the
library. A blank ``calibre_library_path`` is reported as "not configured"
rather than guessed at, and the shelf root is re-checked on every fetch so a
crafted path cannot leave it.

Every parent action is appended to an audit log (``audit.json``) beside the
shared folders: who pushed an update or a folder, uploaded or removed a file,
or locked or wiped a device, and when. It is append-only and capped, like the
telemetry activity feed, and the Devices page reads it back. A failed write is
logged and the action still completes, so the log is best-effort rather than a
way to make an MDM action fail.

An update notice goes to a *device*, not to a person. An ordinary push reaches
every device that person is signed in on (telemetry publishes it to the
person's one ntfy topic), so "push to this phone" cannot be said with it. A
device-scoped notice is queued here under the device key instead
(``notice_requests.json``) and handed to that phone on its own check-in, the
screen-time status its guard already polls - the same way a queued wipe is. The
phone acks what it has shown, and an unshown notice is forgotten after
``NOTICE_TTL_S``, so a phone that was off for a week does not suddenly announce
a long-gone build.

Two MDM actions are deliberately *not* reimplemented here, because the mechanism
already exists and a second copy is how the two drift apart:

* **Remote lock** is the existing screen-time pause (``POST /api/screen-time/{u}/pause``).
* **App install/uninstall control** is the existing screen rules
  (``install_approval`` and ``blocked_apps`` on ``PUT /api/screen-time/{u}/rules``).

The UI exposes both as MDM actions on a device; the server adds nothing.

Nothing here invents a value. A member whose devices identity will not hand over
is reported as unreadable, not as having none, and an update push with no
published build is refused rather than announced (no fake sends).

The shared folders are plain files under ``MDM_FILES_DIR`` (default
``/app/data/mdm_files``), each with a manifest, plus a ``pushes.json`` naming who
each folder is pushed to. The read-only Calibre library (docs/CALIBRE_LIBRARY.md)
is the *source* for the Library set, browsed through the storage service; this
module never writes to it.
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import logging
import mimetypes
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import aiohttp
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response

from services.common import notify as _notify
from services.common.http import get_client
from services.gateway.config import EXECUTION_SVC, IDENTITY_SVC, INTERNAL_SECRET, STORAGE_SVC

log = logging.getLogger("gateway.mdm")

router = APIRouter()

_TIMEOUT = aiohttp.ClientTimeout(total=10)
_HEADERS = {"X-Internal-Secret": INTERNAL_SECRET}

#: The shared sets a parent pushes: one School folder and one Library folder.
SHARED_FOLDERS = ("school", "library")
#: Where the shared folders, their manifests and pushes.json live.
MDM_FILES_DIR = Path(os.getenv("MDM_FILES_DIR", "/app/data/mdm_files"))
#: A single uploaded file may not exceed this.
MAX_UPLOAD_BYTES = 200 * 1024 * 1024
#: An ESPHome OTA firmware binary may not exceed 16 MB (largest ESP32 flash).
MAX_ESPHOME_OTA_BYTES = 16 * 1024 * 1024
#: How long a queued wipe is offered to the phone before it is ignored.
WIPE_TTL_S = 24 * 3600
#: The most recent MDM actions kept in the audit log.
AUDIT_MAX = 500
#: One Library book may not exceed this when it is opened in the app.
MAX_LIBRARY_BOOK_BYTES = 200 * 1024 * 1024
#: The most books one Library listing returns. A shelf can hold thousands; the
#: response is bounded and says when it was cut, rather than shipping all of them.
LIBRARY_MAX_BOOKS = 500
#: How long the storage service may take to list or fetch from the shelf.
LIBRARY_LIST_TIMEOUT = 60.0
LIBRARY_FETCH_TIMEOUT = 180.0

#: File extensions the Library set shows as books. The Calibre shelf also holds
#: ``metadata.db``, cover images and ``.opf`` sidecars; those are not books and
#: are left out rather than listed as unreadable rows.
LIBRARY_BOOK_SUFFIXES = frozenset({
    ".azw", ".azw3", ".cbz", ".cbr", ".djvu", ".docx", ".epub", ".fb2",
    ".htmlz", ".lit", ".mobi", ".pdb", ".pdf", ".rtf", ".txt",
})

#: Content types for those formats. The base image is a slim Debian with no
#: ``/etc/mime.types`` and Python 3.11's built-in table does not know the book
#: formats, so ``mimetypes.guess_type`` alone offered an EPUB as
#: ``application/octet-stream`` - a reader downloads that instead of opening it.
#: Name the formats the Library serves here and leave the host table as fallback.
LIBRARY_BOOK_TYPES = {
    ".azw": "application/vnd.amazon.ebook",
    ".azw3": "application/vnd.amazon.ebook",
    ".cbz": "application/vnd.comicbook+zip",
    ".cbr": "application/vnd.comicbook-rar",
    ".djvu": "image/vnd.djvu",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".epub": "application/epub+zip",
    ".fb2": "application/x-fictionbook+xml",
    ".htmlz": "application/zip",
    ".lit": "application/x-ms-reader",
    ".mobi": "application/x-mobipocket-ebook",
    ".pdb": "application/vnd.palm",
    ".pdf": "application/pdf",
    ".rtf": "application/rtf",
    ".txt": "text/plain; charset=utf-8",
}


def library_content_type(name: str) -> str:
    """The content type to serve a book file with.

    The formats above are named rather than left to ``mimetypes``, which reads
    the host's mime table: the base image ships none and its Python does not know
    the book formats, so the platform table is only the fallback.
    """
    suffix = Path(str(name or "")).suffix.lower()
    return LIBRARY_BOOK_TYPES.get(suffix) or mimetypes.guess_type(str(name or ""))[0] or "application/octet-stream"

#: A file name: no path separator, no control characters, bounded length.
_NAME = re.compile(r"^[^/\\\x00-\x1f]{1,120}$")


# ---------------------------------------------------------------------------
# Pure file store (no FastAPI, so the routes and the tests share one copy).
# ---------------------------------------------------------------------------


def _root(root: Path | None = None) -> Path:
    return Path(root) if root is not None else MDM_FILES_DIR


def _check_folder(folder: str) -> str:
    if folder not in SHARED_FOLDERS:
        raise ValueError(f"folder must be one of {', '.join(SHARED_FOLDERS)}")
    return folder


def _check_name(name: str) -> str:
    name = (name or "").strip()
    if not _NAME.match(name) or name in (".", ".."):
        raise ValueError("a file name may not contain a path separator")
    return name


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        log.warning("[mdm] %s is unreadable; treating it as empty", path.name)
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
    tmp.replace(path)


def _manifest_path(folder: str, root: Path | None = None) -> Path:
    return _root(root) / folder / "manifest.json"


def list_folder(folder: str, root: Path | None = None) -> list[dict]:
    """Every file in a shared folder, oldest upload first."""
    _check_folder(folder)
    files = _read_json(_manifest_path(folder, root)).get("files")
    out = []
    for name, meta in (files or {}).items():
        if not isinstance(meta, dict):
            continue
        out.append({"name": name, **{k: meta.get(k) for k in
                                     ("size", "sha256", "content_type", "uploaded_by", "uploaded_at")}})
    return sorted(out, key=lambda e: (e.get("uploaded_at") or "", e["name"]))


def store_file(folder: str, name: str, content: bytes, content_type: str | None,
               uploaded_by: str, root: Path | None = None) -> dict:
    """Write ``content`` into a shared folder and record it in the manifest."""
    _check_folder(folder)
    name = _check_name(name)
    folder_dir = _root(root) / folder
    folder_dir.mkdir(parents=True, exist_ok=True)
    (folder_dir / name).write_bytes(content)
    entry = {
        "size": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        "content_type": content_type or "application/octet-stream",
        "uploaded_by": uploaded_by,
        "uploaded_at": datetime.now(UTC).isoformat(),
    }
    data = _read_json(_manifest_path(folder, root))
    files = data.get("files")
    if not isinstance(files, dict):
        files = {}
    files[name] = entry
    data["files"] = files
    _write_json(_manifest_path(folder, root), data)
    return {"name": name, **entry}


def delete_file(folder: str, name: str, root: Path | None = None) -> bool:
    """Remove a file and its manifest row; False when it was not there."""
    _check_folder(folder)
    name = _check_name(name)
    data = _read_json(_manifest_path(folder, root))
    files = data.get("files")
    existed = isinstance(files, dict) and name in files
    if isinstance(files, dict):
        files.pop(name, None)
        data["files"] = files
        _write_json(_manifest_path(folder, root), data)
    with contextlib.suppress(FileNotFoundError):
        (_root(root) / folder / name).unlink()
    return existed


def file_path(folder: str, name: str, root: Path | None = None) -> Path:
    _check_folder(folder)
    return _root(root) / folder / _check_name(name)


def _pushes_path(root: Path | None = None) -> Path:
    return _root(root) / "pushes.json"


def read_pushes(root: Path | None = None) -> dict[str, list[str]]:
    """Who each shared folder is pushed to (lowercased usernames)."""
    data = _read_json(_pushes_path(root))
    out: dict[str, list[str]] = {}
    for folder, users in data.items():
        if folder in SHARED_FOLDERS and isinstance(users, list):
            out[folder] = sorted({str(u).strip().lower() for u in users if str(u).strip()})
    return out


def set_push(folder: str, usernames: list, root: Path | None = None) -> list[str]:
    """Replace who a folder is pushed to; returns the stored, cleaned list."""
    _check_folder(folder)
    clean = sorted({str(u).strip().lower() for u in usernames if str(u).strip()})
    data = read_pushes(root)
    data[folder] = clean
    _write_json(_pushes_path(root), data)
    return clean


def pushed_folders(username: str, root: Path | None = None) -> list[str]:
    who = (username or "").strip().lower()
    data = read_pushes(root)
    return [f for f in SHARED_FOLDERS if who and who in data.get(f, [])]


def _wipes_path(root: Path | None = None) -> Path:
    return _root(root) / "wipe_requests.json"


def read_wipes(root: Path | None = None) -> dict:
    return _read_json(_wipes_path(root))


def queue_wipe(device_key: str, owner: str, root: Path | None = None) -> dict:
    """Record a wipe for a device so the phone erases itself on its next check-in."""
    data = read_wipes(root)
    data[device_key] = {"owner": (owner or "").strip().lower(), "at": datetime.now(UTC).isoformat()}
    _write_json(_wipes_path(root), data)
    return data[device_key]


def wipe_requested(username: str, root: Path | None = None) -> bool:
    """Whether any device owned by ``username`` has a live queued wipe.

    A wipe is offered for ``WIPE_TTL_S`` and then ignored, so a phone that was
    offline for longer than that does not erase itself unexpectedly later; the
    parent can queue it again. Without this bound a queued wipe would fire on
    every check-in, including on the freshly reset phone.
    """
    who = (username or "").strip().lower()
    now = datetime.now(UTC)
    for record in read_wipes(root).values():
        if not isinstance(record, dict) or record.get("owner") != who:
            continue
        try:
            at = datetime.fromisoformat(str(record.get("at")))
        except ValueError:
            continue
        if at.tzinfo is None:
            at = at.replace(tzinfo=UTC)
        if (now - at).total_seconds() <= WIPE_TTL_S:
            return True
    return False


def clear_wipe(device_key: str, root: Path | None = None) -> None:
    data = read_wipes(root)
    if device_key in data:
        data.pop(device_key)
        _write_json(_wipes_path(root), data)


# ---------------------------------------------------------------------------
# Per-device notices (queue, hand over on check-in, ack)
# ---------------------------------------------------------------------------

#: An unshown notice is offered for a week and then ignored, so a phone that
#: was off longer than that does not announce a build that has been replaced.
NOTICE_TTL_S = 7 * 24 * 3600
#: How many undelivered notices one device holds; the oldest is dropped.
NOTICE_MAX = 20


def _notices_path(root: Path | None = None) -> Path:
    return _root(root) / "notice_requests.json"


def read_notices(root: Path | None = None) -> dict:
    return _read_json(_notices_path(root))


def _notice_is_live(record: dict, now: datetime) -> bool:
    try:
        at = datetime.fromisoformat(str(record.get("at")))
    except (TypeError, ValueError):
        return False
    if at.tzinfo is None:
        at = at.replace(tzinfo=UTC)
    return (now - at).total_seconds() <= NOTICE_TTL_S


def queue_notice(device_key: str, owner: str, title: str, body: str, *,
                 url: str | None = None, tag: str | None = None,
                 actions: list | None = None, root: Path | None = None) -> dict:
    """Queue one notice for one device; its phone shows it on its next check-in."""
    data = read_notices(root)
    queued = [n for n in (data.get(device_key) or []) if isinstance(n, dict)]
    record = {
        "id": uuid4().hex[:12],
        "owner": (owner or "").strip().lower(),
        "at": datetime.now(UTC).isoformat(),
        "title": str(title)[:120],
        "body": str(body)[:500],
    }
    if url:
        record["url"] = str(url)[:300]
    if tag:
        record["tag"] = str(tag)[:80]
    if actions:
        record["actions"] = actions[:3]
    queued.append(record)
    data[device_key] = queued[-NOTICE_MAX:]
    _write_json(_notices_path(root), data)
    return record


def notices_for(device_key: str, root: Path | None = None) -> list[dict]:
    """This device's undelivered notices, oldest first; expired ones are dropped."""
    now = datetime.now(UTC)
    return [
        n for n in (read_notices(root).get(device_key) or [])
        if isinstance(n, dict) and _notice_is_live(n, now)
    ]


def clear_notices(device_key: str, ids: list | None = None, root: Path | None = None) -> int:
    """Forget the notices a phone has shown; every notice when ``ids`` is None.

    An empty list clears nothing rather than everything: a caller that meant to
    name notices must not empty the queue by passing none.
    """
    data = read_notices(root)
    if device_key not in data:
        return 0
    before = [n for n in (data.get(device_key) or []) if isinstance(n, dict)]
    if ids is None:
        after: list = []
    else:
        wanted = {str(i) for i in ids if str(i).strip()}
        after = [n for n in before if str(n.get("id")) not in wanted]
    removed = len(before) - len(after)
    if after:
        data[device_key] = after
    else:
        data.pop(device_key, None)
    if removed:
        _write_json(_notices_path(root), data)
    return removed


# ---------------------------------------------------------------------------
# Audit log (append-only, capped, best-effort)
# ---------------------------------------------------------------------------


def _audit_path(root: Path | None = None) -> Path:
    return _root(root) / "audit.json"


def record_audit(action: str, actor: str, *, target: str | None = None,
                 detail: dict | None = None, root: Path | None = None) -> dict | None:
    """Append one MDM action to the audit log; ``None`` if it could not be written.

    The entry is small and scalar: the action, who did it, what it touched and a
    short detail object (owners, a file name, a size). No secret is ever put
    here. A write failure is logged and returns ``None`` rather than raising, so
    a full disk cannot turn a parent's action into an error they did not cause.
    """
    entry = {
        "at": datetime.now(UTC).isoformat(),
        "action": str(action or "").strip()[:60],
        "actor": (actor or "").strip().lower(),
        "target": (str(target).strip()[:200] if target else None),
        "detail": detail if isinstance(detail, dict) else {},
    }
    try:
        data = _read_json(_audit_path(root))
        events = data.get("events")
        if not isinstance(events, list):
            events = []
        events.append(entry)
        data["events"] = events[-AUDIT_MAX:]
        _write_json(_audit_path(root), data)
    except OSError as exc:
        log.warning("[mdm] audit write failed for %s: %s", entry["action"], exc)
        return None
    return entry


def read_audit(limit: int = 100, root: Path | None = None) -> list[dict]:
    """The most recent MDM actions, newest first."""
    events = _read_json(_audit_path(root)).get("events")
    if not isinstance(events, list):
        return []
    rows = [e for e in events if isinstance(e, dict)]
    rows.reverse()
    return rows[: max(0, int(limit))]


# ---------------------------------------------------------------------------
# The Library set, read-only from the Calibre shelf
# ---------------------------------------------------------------------------


def normalise_path(path: str) -> str:
    """A rooted, slash-only path with no ``.`` or ``..`` segments.

    The shelf is a namespace of strings, and a traversal segment would let a
    request leave the library; the segments are resolved here rather than
    trusted, the same way the Bible importer's library reader does it.
    """
    text = str(path or "").strip().replace("\\", "/")
    parts: list[str] = []
    for segment in text.split("/"):
        if segment in ("", "."):
            continue
        if segment == "..":
            if parts:
                parts.pop()
            continue
        parts.append(segment)
    return "/" + "/".join(parts)


def _within(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip("/") + "/")


def _book_format(name: str) -> str:
    suffix = Path(str(name or "")).suffix.lower()
    return suffix.lstrip(".") if suffix in LIBRARY_BOOK_SUFFIXES else ""


def describe_library(entries: list, root: str) -> list[dict]:
    """The book files in a shelf listing, sorted by name.

    Directories and non-book files are dropped rather than shown as unreadable
    rows: the Library set is a shelf of books, and a cover image or Calibre's
    ``metadata.db`` is not one. Entries outside the configured root are dropped
    too, so a listing that came back wrong cannot widen the shelf.
    """
    base = normalise_path(root)
    books: list[dict] = []
    for item in entries or []:
        if not isinstance(item, dict) or item.get("is_dir"):
            continue
        path = normalise_path(str(item.get("path") or ""))
        name = str(item.get("name") or path.rsplit("/", 1)[-1])
        fmt = _book_format(name)
        if not fmt or not path or not _within(path, base) or path == base:
            continue
        size = item.get("size")
        books.append({
            "name": name,
            "path": path,
            "format": fmt,
            "size": int(size) if isinstance(size, (int, float)) and not isinstance(size, bool) else None,
        })
    books.sort(key=lambda b: b["name"].lower())
    return books


async def _library_root() -> str:
    """The configured Calibre shelf root, or ``""`` when it is not set."""
    from services.gateway.main import fetch_global_setting

    return (await fetch_global_setting("calibre_library_path")).strip()


def _unconfigured() -> dict:
    return {
        "configured": False,
        "books": [],
        "reason": (
            "No Calibre library path is configured. Set 'calibre_library_path' "
            "(Admin > LLM & Settings) to the Nextcloud folder that holds the books."
        ),
    }


async def list_library_books() -> dict:
    """List the family Library set from the read-only Calibre shelf.

    The shelf is browsed through the storage service's existing Nextcloud
    provider (the same path the RAG index and Bible importer use), so the
    credentials stay in one place. A blank setting, an unreachable service or a
    refusal are each reported as their own state; none of them is an empty
    shelf, and none of them is invented.
    """
    root = await _library_root()
    if not root:
        return _unconfigured()
    base = normalise_path(root)
    payload = {"provider": {"kind": "nextcloud", "settings": {}}, "path": base, "recursive": True}
    try:
        async with get_client().post(
            f"{STORAGE_SVC}/providers/list",
            json=payload,
            headers=_HEADERS,
            timeout=aiohttp.ClientTimeout(total=LIBRARY_LIST_TIMEOUT),
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                log.warning("[mdm] library list failed: HTTP %s %s", resp.status, body[:200])
                return {"configured": True, "root": base, "books": [],
                        "error": "The family library could not be read from storage."}
            data = await resp.json(content_type=None)
    except Exception as e:  # an unreachable shelf is not an empty one
        log.warning("[mdm] library list failed: %s", e)
        return {"configured": True, "root": base, "books": [],
                "error": "The family library could not be reached."}
    entries = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return {"configured": True, "root": base, "books": [],
                "error": "The family library listing was not in the expected shape."}
    books = describe_library(entries, base)
    return {"configured": True, "root": base, "books": books[:LIBRARY_MAX_BOOKS],
            "count": len(books), "truncated": len(books) > LIBRARY_MAX_BOOKS}


async def fetch_library_book(path: str) -> tuple[bytes, str, str]:
    """One Library book's bytes, its name and its content type.

    Raises ``ValueError`` for a path outside the shelf, ``LookupError`` when the
    shelf is not configured, and ``ConnectionError`` when storage refuses or is
    unreachable. Each maps to a different HTTP answer at the route, because each
    needs a different fix.
    """
    root = await _library_root()
    if not root:
        raise LookupError("No Calibre library path is configured.")
    base = normalise_path(root)
    wanted = normalise_path(path)
    if not wanted or wanted == base or not _within(wanted, base):
        raise ValueError("That path is not inside the family library.")
    payload = {"provider": {"kind": "nextcloud", "settings": {}}, "path": wanted,
               "max_bytes": MAX_LIBRARY_BOOK_BYTES}
    try:
        async with get_client().post(
            f"{STORAGE_SVC}/providers/fetch",
            json=payload,
            headers=_HEADERS,
            timeout=aiohttp.ClientTimeout(total=LIBRARY_FETCH_TIMEOUT),
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                log.warning("[mdm] library fetch failed for %s: HTTP %s %s", wanted, resp.status, body[:200])
                raise ConnectionError("The family library could not read that book.")
            data = await resp.json(content_type=None)
    except (ValueError, LookupError, ConnectionError):
        raise
    except Exception as e:
        log.warning("[mdm] library fetch failed for %s: %s", wanted, e)
        raise ConnectionError("The family library could not be reached.") from e
    encoded = data.get("content_b64") if isinstance(data, dict) else None
    if not isinstance(encoded, str) or not encoded:
        raise ConnectionError("The family library returned no content for that book.")
    try:
        content = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as exc:
        raise ConnectionError("The family library returned content that could not be read.") from exc
    name = wanted.rsplit("/", 1)[-1]
    content_type = library_content_type(name)
    return content, name, content_type


# ---------------------------------------------------------------------------
# Identity lookups & device platform helpers
# ---------------------------------------------------------------------------

ESPHOME_DEVICE_KINDS = frozenset({"watch", "assistant", "speaker", "screen", "light"})


def is_esphome_device(device: dict) -> bool:
    """True when a device is an ESPHome / companion node rather than an Android phone or tablet."""
    if not isinstance(device, dict):
        return False
    kind = str(device.get("kind") or "").strip().lower()
    key = str(device.get("device_key") or "").strip().lower()
    return bool(
        kind in ESPHOME_DEVICE_KINDS
        or device.get("esphome_version")
        or key.startswith("esphome:")
        or str(device.get("registered_by") or "").strip().lower() in ("paired", "adopted", "admin")
    )


def device_platform(device: dict) -> str:
    """'esphome' for ESPHome companion nodes; 'android' for mobile phones and tablets."""
    return "esphome" if is_esphome_device(device) else "android"


def pending_notices(device: dict) -> int:
    """How many device-scoped notices are waiting for this phone to check in."""
    key = str(device.get("device_key") or "").strip()
    return len(notices_for(key)) if key else 0


async def device_owned_by(device_key: str, username: str) -> bool:
    """Whether that device key belongs to that member, per identity's own answer."""
    who = (username or "").strip().lower()
    key = (device_key or "").strip()
    if not who or not key:
        return False
    return (await _owner_of(key)) == who


async def _owner_of(device_key: str) -> str | None:
    """The username a device key belongs to, from identity's internal lookup."""
    try:
        async with get_client().get(
            f"{IDENTITY_SVC}/api/internal/device-registered/{device_key}", headers=_HEADERS, timeout=_TIMEOUT
        ) as resp:
            if resp.status != 200:
                return None
            data = await resp.json(content_type=None)
    except Exception as e:  # a lookup failure is not a device that exists
        log.warning("[mdm] device lookup failed: %s", e)
        return None
    owner = (data or {}).get("owner") if isinstance(data, dict) else None
    return str(owner).lower() if owner else None


async def _guard_caps(request: Request, username: str) -> list[dict]:
    """The ``capabilities.guard`` block of each of the named member's devices.

    A lookup that fails, or a member with no such device, answers empty - a lie
    is never logged. The caller's own devices come from the user-panel list,
    since identity's per-member endpoint refuses the subject themselves.
    """
    from services.gateway.main import _identity_as_caller

    name = (username or "").strip().lower()
    if not name:
        return []
    try:
        status, info = await _identity_as_caller(request, "GET", f"/api/family/devices/{name}")
        if status == 200 and isinstance(info, dict):
            rows = info.get("devices") or []
        else:
            status, info = await _identity_as_caller(request, "GET", "/api/user-panel/devices")
            rows = ([d for d in info if isinstance(d, dict) and (d.get("owner_username") or "").lower() == name]
                    if status == 200 and isinstance(info, list) else [])
    except Exception as exc:  # a lookup failure is not a device that exists
        log.warning("[mdm] guard capability lookup for %s failed: %s", name, exc)
        return []
    out: list[dict] = []
    for device in rows:
        guard = (device.get("capabilities") or {}).get("guard") or {}
        if isinstance(guard, dict):
            out.append(guard)
    return out


async def device_owner_reported(request: Request, username: str) -> bool:
    """Whether the named member has a device that reported ``guard.deviceOwner``."""
    return any(g.get("deviceOwner") for g in await _guard_caps(request, username))


async def real_lock_reported(request: Request, username: str) -> bool:
    """Whether a screen-time lock really locks the named member's phone.

    A device-owner phone gets Android's own lock screen plus lock-task mode; any
    other phone gets the full-screen Accessibility overlay (``GuardOverlay``),
    which covers the launcher and every app while the Accessibility guard is on.
    So the lock is real when either is reported: with the guard off it is only a
    notification, and logging "locked a device" would be a false success.
    """
    return any(g.get("deviceOwner") or g.get("guardEnabled")
               for g in await _guard_caps(request, username))


def _folder_or_404(folder: str) -> str:
    if folder not in SHARED_FOLDERS:
        raise HTTPException(status_code=404, detail=f"No shared folder named {folder!r}")
    return folder


async def _body(request: Request) -> dict:
    try:
        body = await request.json()
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/api/mdm/devices")
async def mdm_device_inventory(request: Request):
    """Every family device in one list, for the father or an admin.

    Aggregated from identity's existing per-member endpoint
    (``/api/family/devices/{username}``) rather than a second source of truth.
    A member whose devices identity will not hand over is reported as
    ``readable: false`` with the reason, so the page shows an explicit
    "not readable" state instead of an invented empty list.
    """
    from services.gateway.main import _identity_as_caller, _require_authenticated

    caller = (await _require_authenticated(request) or "").lower()
    try:
        async with get_client().get(f"{IDENTITY_SVC}/api/users", headers=_HEADERS, timeout=_TIMEOUT) as resp:
            if resp.status != 200:
                raise HTTPException(status_code=502, detail="The family list could not be read")
            users = await resp.json(content_type=None)
    except HTTPException:
        raise
    except Exception as e:
        log.warning("[mdm] family list failed: %s", e)
        raise HTTPException(status_code=502, detail="The family list could not be read") from e
    if not isinstance(users, list):
        raise HTTPException(status_code=502, detail="The family list could not be read")

    devices: list[dict] = []
    members: list[dict] = []
    for u in users:
        if not isinstance(u, dict) or not u.get("username") or u.get("is_system_default"):
            continue
        username = str(u["username"])
        display = u.get("display_name") or username
        if username.lower() == caller:
            # Identity's per-member endpoint refuses the subject themselves, so
            # the caller's own devices come from their user-panel list instead
            # of being reported as unreadable.
            status, info = await _identity_as_caller(request, "GET", "/api/user-panel/devices")
            rows = ([d for d in info if isinstance(d, dict) and (d.get("owner_username") or "").lower() == caller]
                    if status == 200 and isinstance(info, list) else [])
        else:
            status, info = await _identity_as_caller(request, "GET", f"/api/family/devices/{username}")
            rows = info.get("devices") or [] if status == 200 and isinstance(info, dict) else []
        if status == 200:
            for d in rows:
                devices.append({
                    **d,
                    "platform": device_platform(d),
                    "owner_username": d.get("owner_username") or username,
                    "owner_display_name": display,
                    "pending_notices": pending_notices(d),
                })
            members.append({"username": username, "display_name": display,
                            "device_count": len(rows), "readable": True, "reason": None})
        else:
            detail = (info or {}).get("detail") if isinstance(info, dict) else None
            members.append({"username": username, "display_name": display, "device_count": None,
                            "readable": False,
                            "reason": detail or f"Could not read this member's devices (HTTP {status})"})

    # For administrators, ensure unassigned / default ESPHome companion devices are also visible
    from services.gateway.main import _caller_is_admin

    if await _caller_is_admin(request):
        seen_keys = {str(d.get("device_key")).lower() for d in devices if d.get("device_key")}
        try:
            status, all_registered = await _identity_as_caller(request, "GET", "/api/user-panel/devices")
            if status == 200 and isinstance(all_registered, list):
                for dev in all_registered:
                    if not isinstance(dev, dict):
                        continue
                    key = str(dev.get("device_key") or "").lower()
                    owner_u = (dev.get("owner_username") or "").strip().lower()
                    if key and key not in seen_keys:
                        # Include unassigned devices, default-owned devices, or ESPHome companion nodes
                        if not owner_u or owner_u == "default" or is_esphome_device(dev):
                            if not is_esphome_device(dev) and owner_u and owner_u != "default":
                                continue
                            seen_keys.add(key)
                            devices.append({
                                **dev,
                                "platform": device_platform(dev),
                                "owner_username": dev.get("owner_username") or None,
                                "owner_display_name": (
                                    "Default Admin" if owner_u == "default" else (dev.get("owner_username") or "(Unassigned)")
                                ),
                                "pending_notices": pending_notices(dev),
                            })
        except Exception as e:
            log.warning("[mdm] unassigned device inventory supplement failed: %s", e)

    return {"devices": devices, "members": members, "generated_at": datetime.now(UTC).isoformat()}


@router.post("/api/mdm/devices/update")
async def mdm_push_app_update(request: Request):
    """Send an app-update notice to the selected devices or people.

    A named *person* gets an ordinary push, which reaches every device they are
    signed in on, because that is what the push channel can say. A named
    *device* gets a queued notice for that phone alone (see the module
    docstring): selecting one of a person's two phones no longer notifies both.
    The notice goes to the device owner (a minor included): ``audience`` stays
    the default ``all``, unlike the whole-family publish notice in
    ``notices.py`` which is ``parents``-only. When no build is published the
    request is refused with 409 rather than announcing an update nobody can
    install.
    """
    from services.gateway.main import _require_admin, get_app_update_version

    actor = await _require_admin(request)
    body = await _body(request)
    raw_keys = [str(k).strip() for k in (body.get("device_keys") or []) if isinstance(k, str) and str(k).strip()]
    if any(k.lower().startswith("esphome:") for k in raw_keys):
        raise HTTPException(
            status_code=422,
            detail="ESPHome devices do not run the Android app; use ESPHome management instead.",
        )
    people = [str(u).strip().lower() for u in (body.get("usernames") or []) if isinstance(u, str) and str(u).strip()]
    if not raw_keys and not people:
        raise HTTPException(status_code=422, detail="Select at least one device or person")
    # Every named device must be a real one: a queued notice for a key nothing
    # owns would sit there forever and report a send that never happened.
    devices: list[dict] = []
    for key in raw_keys:
        owner = await _owner_of(key)
        if not owner:
            raise HTTPException(status_code=422, detail=f"{key} is not a registered device")
        devices.append({"device_key": key, "owner": owner})

    info = await get_app_update_version(request)
    bundle = bool(info.get("bundle_available"))
    apk = bool(info.get("apk_available"))
    if not bundle and not apk:
        raise HTTPException(
            status_code=409,
            detail="No app build is published yet (no OTA bundle and no APK on the server), so there is nothing to push.",
        )
    version = str(info.get("version") or "unknown")
    code = info.get("apk_version_code")
    label = f"{version} (build {code})" if code else version
    available = []
    if bundle:
        available.append("the web bundle over the air")
    if apk:
        available.append("a new app build to install")
    text = "Update now: " + " and ".join(available) + "."
    title = f"Jarvis app {label} is available"
    actions = [_notify.view_action("/settings", "Update now")]
    sent = 0
    for owner in people:
        payload = _notify.notice(
            owner, "app_updates", title, text,
            url="/settings", tag="mdm_app_update", actions=actions,
        )
        sent += int(await _notify.send(payload))
    for device in devices:
        queue_notice(
            device["device_key"], device["owner"], title, text,
            url="/settings", tag="mdm_app_update", actions=actions,
        )
    queued = len(devices)
    record_audit("app_update_push", actor, detail={
        "owners": people, "devices": [d["device_key"] for d in devices],
        "version": version, "apk_version_code": code,
        "bundle_available": bundle, "apk_available": apk, "sent": sent, "queued": queued,
    })
    return {"status": "SUCCESS", "sent": sent, "queued": queued, "owners": people,
            "devices": [d["device_key"] for d in devices], "version": version,
            "apk_version_code": code, "bundle_available": bundle, "apk_available": apk}


@router.post("/api/mdm/devices/notices/ack")
async def mdm_ack_device_notices(request: Request):
    """A phone reports the device-scoped notices it has shown, so they stop.

    The caller must own the device (the phone acts as the signed-in child) or
    be an admin; anything else is refused rather than silently clearing
    another phone's notices. ``ids`` names what was shown - a phone that has
    not shown anything has nothing to ack, and an empty list is refused so a
    bug cannot quietly drop every queued notice.
    """
    from services.gateway.main import _caller_is_admin, _require_authenticated

    caller = (await _require_authenticated(request) or "").strip().lower()
    body = await _body(request)
    device_key = str(body.get("device_key") or "").strip()
    if not device_key:
        raise HTTPException(status_code=422, detail="device_key is required")
    ids = body.get("ids")
    if not isinstance(ids, list) or not [i for i in ids if str(i).strip()]:
        raise HTTPException(status_code=422, detail="ids must name the notices that were shown")
    owner = await _owner_of(device_key)
    if not owner:
        raise HTTPException(status_code=422, detail=f"{device_key} is not a registered device")
    if owner != caller and not await _caller_is_admin(request):
        raise HTTPException(status_code=403, detail="Only the device's owner can acknowledge its notices")
    cleared = clear_notices(device_key, ids)
    return {"status": "SUCCESS", "cleared": cleared}


@router.get("/api/mdm/files")
async def mdm_list_shared_files(request: Request):
    """The two shared folders, their files and who each is pushed to (admin)."""
    from services.gateway.main import _require_admin

    await _require_admin(request)
    pushes = read_pushes()
    return {"folders": [{"folder": f, "files": list_folder(f), "pushed_to": pushes.get(f, [])}
                        for f in SHARED_FOLDERS]}


@router.post("/api/mdm/files/push")
async def mdm_push_shared_files(request: Request):
    """Push a shared folder to selected people's devices (admin).

    Declared before ``/api/mdm/files/{folder}`` so the literal path is not read
    as a folder name.
    """
    from services.gateway.main import _require_admin

    actor = await _require_admin(request)
    body = await _body(request)
    folder = _folder_or_404(str(body.get("folder") or ""))
    usernames = body.get("usernames")
    if not isinstance(usernames, list) or not usernames:
        raise HTTPException(status_code=422, detail="Select at least one person to push the folder to")
    people = set_push(folder, usernames)
    files = list_folder(folder)
    label = "School" if folder == "school" else "Library"
    sent = 0
    for user in people:
        payload = _notify.notice(
            user, "family_safety", f"{label} folder updated",
            f"{len(files)} file(s) are ready in your shared {label} folder.",
            url="/devices", tag=f"mdm_files:{folder}",
            actions=[_notify.view_action("/devices", f"Open {label}")],
        )
        sent += int(await _notify.send(payload))
    record_audit("folder_push", actor, target=folder,
                 detail={"pushed_to": people, "files": len(files), "sent": sent})
    return {"status": "SUCCESS", "folder": folder, "pushed_to": people, "files": len(files), "sent": sent}


@router.get("/api/mdm/files/for-me")
async def mdm_files_for_me(request: Request):
    """The shared folders pushed to the caller, for the device-facing list."""
    from services.gateway.main import _require_authenticated

    user = await _require_authenticated(request)
    return {"folders": [{"folder": f, "files": list_folder(f)} for f in pushed_folders(user)]}


@router.post("/api/mdm/files/{folder}")
async def mdm_upload_shared_file(request: Request, folder: str, file: UploadFile = File(...)):
    """Upload one file into a shared folder (admin)."""
    from services.gateway.main import _require_admin

    caller = await _require_admin(request)
    _folder_or_404(folder)
    name = (file.filename or "").strip()
    try:
        _check_name(name)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    content = await file.read()
    if not content:
        raise HTTPException(status_code=422, detail="The uploaded file is empty")
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"The file is larger than the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit")
    entry = store_file(folder, name, content, file.content_type, caller)
    record_audit("file_upload", caller, target=f"{folder}/{name}",
                 detail={"folder": folder, "name": name, "size": len(content)})
    return {"status": "SUCCESS", "file": entry}


@router.delete("/api/mdm/files/{folder}/{name}")
async def mdm_delete_shared_file(request: Request, folder: str, name: str):
    """Remove one file from a shared folder (admin)."""
    from services.gateway.main import _require_admin

    actor = await _require_admin(request)
    _folder_or_404(folder)
    try:
        removed = delete_file(folder, name)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not removed:
        raise HTTPException(status_code=404, detail="No such file")
    record_audit("file_delete", actor, target=f"{folder}/{name}",
                 detail={"folder": folder, "name": name})
    return {"status": "SUCCESS"}


@router.get("/api/mdm/files/{folder}/{name}")
async def mdm_download_shared_file(request: Request, folder: str, name: str):
    """Download one shared file (any signed-in member; the device-facing read)."""
    from services.gateway.main import _require_authenticated

    await _require_authenticated(request)
    _folder_or_404(folder)
    try:
        path = file_path(folder, name)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not path.exists():
        raise HTTPException(status_code=404, detail="No such file")
    return FileResponse(str(path), filename=name)


@router.post("/api/mdm/devices/{device_key}/wipe")
async def mdm_wipe_device(request: Request, device_key: str):
    """Queue a remote wipe, only for a device-owner phone, and only with confirm.

    A wipe is destructive and irreversible, so it is gated on an explicit
    ``confirm`` and on the device having reported ``guard.deviceOwner``: without
    device owner the phone's own Android will not honour it, and pretending
    otherwise would be a lie. The queue is read by the screen-time status the
    phone already polls, so the phone erases itself on its next check-in.
    """
    from services.gateway.main import _identity_as_caller, _require_authenticated

    actor = await _require_authenticated(request)
    body = await _body(request)
    if not body.get("confirm"):
        raise HTTPException(
            status_code=422,
            detail="A remote wipe erases everything on the phone and cannot be undone; send confirm=true to proceed.",
        )
    key = device_key.strip()
    if key.lower().startswith("esphome:"):
        raise HTTPException(
            status_code=409,
            detail="ESPHome devices do not support Android factory wipe.",
        )
    owner = await _owner_of(key)
    if not owner:
        raise HTTPException(status_code=404, detail="No registered device with that key")
    status, info = await _identity_as_caller(request, "GET", f"/api/family/devices/{owner}")
    if status != 200 or not isinstance(info, dict):
        detail = (info or {}).get("detail") if isinstance(info, dict) else None
        raise HTTPException(status_code=status if status in (401, 403, 404) else 502,
                            detail=detail or "You may not manage that device")
    device = next((d for d in (info.get("devices") or []) if d.get("device_key") == key), None)
    if device is None:
        raise HTTPException(status_code=404, detail="That device does not belong to a member you can manage")
    if is_esphome_device(device):
        raise HTTPException(
            status_code=409,
            detail="ESPHome devices do not support Android factory wipe.",
        )
    noun = "tablet" if str(device.get("kind") or "").lower() == "tablet" else "phone"
    guard = (device.get("capabilities") or {}).get("guard") or {}
    if not guard.get("deviceOwner"):
        raise HTTPException(
            status_code=409,
            detail=f"Remote wipe needs a device-owner {noun}; this device has not reported device owner.",
        )
    record = queue_wipe(key, owner)
    record_audit("device_wipe", actor, target=key, detail={"owner": owner, "kind": device.get("kind")})
    return {"status": "queued", "device_key": key, "owner_username": owner, "queued_at": record["at"],
            "delivery": f"the {noun} erases itself on its next check-in"}


@router.post("/api/mdm/esphome/{device}/ota")
async def mdm_esphome_ota(
    request: Request,
    device: str,
    file: UploadFile = File(...),
    password: str | None = Form(default=None),
    ota_port: int = Form(default=3232),
):
    """Push a compiled `.bin` firmware image over the air to an ESPHome device (admin).

    Forwards the firmware payload to the execution service's ESPHome OTA client,
    which speaks ESPHome's native TCP OTA v1/v2 protocol (port 3232) and falls
    back to `web_server` `POST /update` (port 80) when applicable. Every OTA
    flash is recorded in the MDM audit log.
    """
    from services.gateway.main import _require_admin

    actor = await _require_admin(request)
    target = (device or "").strip()
    if not target:
        raise HTTPException(status_code=422, detail="An ESPHome device name or key is required")
    name = (file.filename or "firmware.bin").strip()
    try:
        _check_name(name)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not name.lower().endswith((".bin", ".bin.gz", ".ota")):
        raise HTTPException(
            status_code=422,
            detail="ESPHome OTA requires a compiled firmware binary (.bin, .bin.gz, or .ota).",
        )
    content = await file.read()
    if not content:
        raise HTTPException(status_code=422, detail="The uploaded firmware file is empty")
    if len(content) > MAX_ESPHOME_OTA_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"The firmware file exceeds the {MAX_ESPHOME_OTA_BYTES // (1024 * 1024)} MB ESP32 flash limit",
        )
    payload = {
        "user_context": {"user": actor, "is_admin": True},
        "action": "ota",
        "device": target,
        "params": {
            "firmware_b64": base64.b64encode(content).decode("ascii"),
            "filename": name,
            "password": (password or "").strip() or None,
            "ota_port": int(ota_port or 3232),
        },
    }
    try:
        async with get_client().post(
            f"{EXECUTION_SVC}/execute/esphome",
            json=payload,
            headers=_HEADERS,
            timeout=aiohttp.ClientTimeout(total=120.0),
        ) as resp:
            data = await resp.json(content_type=None)
    except Exception as exc:
        log.warning("[mdm] esphome OTA request to execution failed for %s: %s", target, exc)
        raise HTTPException(status_code=502, detail="The execution service could not be reached for ESPHome OTA.") from exc

    if not isinstance(data, dict) or data.get("status") != "SUCCESS":
        msg = (data.get("message") or data.get("detail") if isinstance(data, dict) else None) or "ESPHome OTA update failed"
        raise HTTPException(status_code=502, detail=str(msg))

    detail = data.get("detail") if isinstance(data.get("detail"), dict) else {}
    sha256 = hashlib.sha256(content).hexdigest()
    record_audit(
        "esphome_ota",
        actor,
        target=target,
        detail={
            "filename": name,
            "size": len(content),
            "sha256": sha256,
            "method": detail.get("method") or "esphome_ota",
        },
    )
    return {
        "status": "SUCCESS",
        "message": data.get("message") or f"Uploaded {name} to {target}",
        "device": target,
        "filename": name,
        "bytes": len(content),
        "sha256": sha256,
        **detail,
    }


@router.post("/api/workspaces/{workspace_id}/esphome/flash")
async def workspace_esphome_flash(
    request: Request,
    workspace_id: str,
):
    """Flash a firmware binary from the workspace to an ESPHome device (Admin only)."""
    from services.gateway.main import _require_admin

    actor = await _require_admin(request)
    body = await _body(request)
    device = (body.get("device") or "").strip()
    if not device:
        raise HTTPException(status_code=422, detail="An ESPHome device name or key is required")
    path = (body.get("path") or body.get("firmware_path") or "").strip()
    if not path:
        raise HTTPException(status_code=422, detail="A workspace firmware path (.bin) is required")

    # Read the firmware binary from the workspace
    try:
        async with get_client().post(
            f"{EXECUTION_SVC}/execute/workspace_file_read",
            json={
                "user_context": {"user": actor, "is_admin": True},
                "workspace_id": workspace_id,
                "path": path,
                "encoding": "base64",
            },
            headers=_HEADERS,
            timeout=_TIMEOUT,
        ) as resp:
            data = await resp.json(content_type=None)
    except Exception as exc:
        log.warning("[mdm] reading workspace file %s failed: %s", path, exc)
        raise HTTPException(status_code=502, detail="Could not read firmware from workspace") from exc

    if not isinstance(data, dict) or data.get("status") != "SUCCESS":
        msg = (data.get("message") if isinstance(data, dict) else None) or f"Could not read {path}"
        raise HTTPException(status_code=404, detail=str(msg))

    detail = data.get("detail") if isinstance(data.get("detail"), dict) else {}
    fw_b64 = detail.get("content") or detail.get("base64")
    if not fw_b64:
        raise HTTPException(status_code=422, detail=f"No binary content in workspace file {path}")

    filename = path.rsplit("/", 1)[-1]
    password = (body.get("password") or "").strip() or None
    ota_port = int(body.get("ota_port") or 3232)

    payload = {
        "user_context": {"user": actor, "is_admin": True},
        "action": "ota",
        "device": device,
        "params": {
            "firmware_b64": fw_b64,
            "filename": filename,
            "password": password,
            "ota_port": ota_port,
        },
    }
    try:
        async with get_client().post(
            f"{EXECUTION_SVC}/execute/esphome",
            json=payload,
            headers=_HEADERS,
            timeout=aiohttp.ClientTimeout(total=120.0),
        ) as resp:
            res_data = await resp.json(content_type=None)
    except Exception as exc:
        log.warning("[mdm] workspace esphome OTA failed for %s: %s", device, exc)
        raise HTTPException(status_code=502, detail="The execution service could not be reached for ESPHome OTA.") from exc

    if not isinstance(res_data, dict) or res_data.get("status") != "SUCCESS":
        msg = (res_data.get("message") if isinstance(res_data, dict) else None) or "ESPHome OTA flash failed"
        raise HTTPException(status_code=502, detail=str(msg))

    ota_detail = res_data.get("detail") if isinstance(res_data.get("detail"), dict) else {}
    record_audit(
        "esphome_ota",
        actor,
        target=device,
        detail={
            "filename": filename,
            "source": f"workspace:{workspace_id}:{path}",
            "method": ota_detail.get("method") or "esphome_ota",
            "bytes": ota_detail.get("bytes"),
        },
    )
    return {
        "status": "SUCCESS",
        "message": res_data.get("message") or f"Flashed {filename} to {device}",
        "device": device,
        "filename": filename,
        "detail": ota_detail,
    }


@router.post("/api/workspaces/{workspace_id}/esphome/validate")
async def workspace_esphome_validate(
    request: Request,
    workspace_id: str,
):
    """Validate an ESPHome YAML configuration file in the workspace."""
    from services.gateway.main import _require_authenticated

    actor = await _require_authenticated(request)
    body = await _body(request)
    path = (body.get("path") or body.get("yaml_path") or "").strip()
    if not path:
        raise HTTPException(status_code=422, detail="A YAML file path is required")

    lint_req = {
        "user_context": {"user": actor, "is_admin": True},
        "workspace_id": workspace_id,
        "path": path,
        "linter": "esphome",
    }
    try:
        async with get_client().post(
            f"{EXECUTION_SVC}/execute/workspace_lint",
            json=lint_req,
            headers=_HEADERS,
            timeout=aiohttp.ClientTimeout(total=45.0),
        ) as resp:
            data = await resp.json(content_type=None)
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Execution service unreachable for lint") from exc

    return data


@router.get("/api/mdm/library/books")
async def mdm_library_books(request: Request):
    """The family Library set, read-only from the Calibre shelf (any member).

    A blank ``calibre_library_path``, an unreachable storage service and an
    unreadable listing are each reported as their own state; none is shown as an
    empty shelf.
    """
    from services.gateway.main import _require_authenticated

    await _require_authenticated(request)
    return await list_library_books()


@router.post("/api/mdm/library/books/file")
async def mdm_library_book_file(request: Request):
    """One Library book's bytes, read-only (any member).

    A POST rather than a GET because ``path`` is a shelf path with slashes in
    it, and the caller has to be authenticated (the app sends the key in a
    header), so a plain link cannot open it either way.
    """
    from services.gateway.main import _require_authenticated

    await _require_authenticated(request)
    body = await _body(request)
    try:
        content, name, content_type = await fetch_library_book(str(body.get("path") or ""))
    except LookupError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ConnectionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(content=content, media_type=content_type,
                    headers={"Content-Disposition": f'inline; filename="{name}"'})


@router.get("/api/mdm/audit")
async def mdm_audit_log(request: Request, limit: int = 100):
    """Who did what to the family's devices and shared files, newest first (admin)."""
    from services.gateway.main import _require_admin

    await _require_admin(request)
    capped = max(1, min(int(limit), AUDIT_MAX))
    return {"events": read_audit(capped)}


__all__ = [
    "AUDIT_MAX", "LIBRARY_BOOK_SUFFIXES", "LIBRARY_MAX_BOOKS", "MAX_LIBRARY_BOOK_BYTES", "MAX_UPLOAD_BYTES",
    "MDM_FILES_DIR",
    "SHARED_FOLDERS", "WIPE_TTL_S", "clear_wipe", "delete_file", "describe_library", "device_owner_reported",
    "fetch_library_book",
    "file_path", "library_content_type", "list_folder", "list_library_books", "normalise_path", "pushed_folders",
    "queue_wipe",
    "read_audit", "read_pushes", "read_wipes", "real_lock_reported", "record_audit", "router", "set_push", "store_file",
    "wipe_requested",
]

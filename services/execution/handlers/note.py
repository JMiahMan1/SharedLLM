# services/execution/handlers/note.py
import asyncio
import logging
import os
import re
import xml.etree.ElementTree as ET
from collections import OrderedDict
from pathlib import Path

from services.config import LOCAL_NOTES_ROOT as _LOCAL_NOTES_ROOT
from services.execution.schemas import ExecutionResult, NoteRequest

from ..personal_data import missing_account_message, resolve_personal_data_provider

log = logging.getLogger("execution.note")

DEFAULT_NOTES_DIRS = ["Notes"]
LOCAL_NOTES_ROOT = _LOCAL_NOTES_ROOT or "/app/data/notes"

#: The shared notes index. Pin, colour, reminders and the archive/trash
#: original path live here so they follow the family across devices and
#: clients while every note stays a plain markdown file. The name starts with
#: a dot so Nextcloud's Files view hides it, and it is not a ``.md`` file, so
#: it is never listed as a note.
META_PATH = "Notes/.jarvis-notes.json"
META_VERSION = 1
META_MAX_BYTES = 512 * 1024
ARCHIVE_DIR = "Notes/Archive"
TRASH_DIR = "Notes/Trash"

#: How many of the newest notes a ``list`` with ``previews`` fills in, and how
#: many of those reads run at once. The Notes page used to fetch each preview
#: as its own request: 24 parallel reads filled the execution service's 10
#: connections to Nextcloud, the rest timed out waiting for one, and Talk and
#: the editor's own read starved behind them for minutes.
PREVIEW_LIMIT = 24
PREVIEW_CONCURRENCY = 4
#: Note text already read, by file URL (which names the account), with the
#: modified time and size it was read at. A list only re-reads notes that
#: changed; every write through this handler forgets the note it touched.
_PREVIEW_CACHE_MAX = 512
_SPAN_TAG = re.compile(r'</?span(?: style="[^"]*")?>')


def _item_matches(item_text: str, target: str) -> bool:
    """A checklist row matches its raw text or the plain words shown to people.

    The notes editor stores colour/highlight/size as an inline ``<span>``;
    widgets and chat show only the words, so both forms must find the row.
    """
    raw = item_text.strip()
    return raw == target or _SPAN_TAG.sub("", raw).strip() == target


_preview_cache: "OrderedDict[str, tuple[str, str]]" = OrderedDict()

#: Ask only for what the list shows. Nextcloud's default property set includes
#: quota and checksum properties that are slow to compute on a big folder.
_LIST_PROPS = (
    '<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:prop>'
    "<d:resourcetype/><d:getlastmodified/><d:getcontentlength/>"
    "</d:prop></d:propfind>"
)

#: The header create/write put on a note: "# <title>", "Category: <dir>", blank.
_STORED_HEADER = re.compile(r"\A[ \t]*\n*# [^\n]*\nCategory: [^\n]*\n(?:[ \t]*\n)?")


def note_body(content: str | None) -> str:
    """A note's text without the stored header(s).

    ``read`` returns the whole file, header included, so an editor that saved
    what it was given wrote the header again on top of itself every time.
    Stripping it here (repeatedly, which also heals notes already stacked
    with copies) makes ``write`` idempotent whatever the caller sends.
    """
    text = content or ""
    while (m := _STORED_HEADER.match(text)):
        text = text[m.end():]
    return text


def _etag(resp: dict) -> str | None:
    """The file version Nextcloud reported (``ETag``, or its ``OC-ETag`` twin).

    Header names arrive in whatever case the server used, so look them up
    without case. A save sends this back as ``If-Match`` so an edit made
    elsewhere since the note was opened is refused instead of overwritten.
    """
    headers = resp.get("headers") or {}
    lowered = {str(k).lower(): v for k, v in headers.items()}
    value = lowered.get("etag") or lowered.get("oc-etag")
    return str(value) if value else None


def _with_header(title: str | None, category: str | None, content: str | None) -> str:
    return f"# {title}\nCategory: {category}\n\n{note_body(content)}"


def _forget_preview(url: str) -> None:
    _preview_cache.pop(url, None)


def _remember_preview(url: str, version: str, text: str) -> None:
    _preview_cache[url] = (version, text)
    _preview_cache.move_to_end(url)
    while len(_preview_cache) > _PREVIEW_CACHE_MAX:
        _preview_cache.popitem(last=False)


async def _attach_previews(provider, notes: list[dict]) -> None:
    """Put each of the newest notes' text in ``content``, a few reads at a time.

    A note that cannot be read keeps no ``content``; the page shows its size
    instead, and opening it reads it again.
    """
    from .. import http_client

    gate = asyncio.Semaphore(PREVIEW_CONCURRENCY)

    async def fill(note: dict) -> None:
        url = provider.file_url(note["path"].lstrip("/"))
        version = f"{note.get('modified', '')}|{note.get('size', 0)}"
        cached = _preview_cache.get(url)
        if cached and cached[0] == version:
            _preview_cache.move_to_end(url)
            note["content"] = cached[1]
            return
        async with gate:
            try:
                resp = await http_client.request(
                    "GET", url, auth=(provider.username, provider.password), verify=False, timeout=20
                )
            except Exception as e:  # one unreadable note must not fail the list
                log.warning("Note preview read failed for %s: %s", note["path"], str(e) or type(e).__name__)
                return
        if resp["status_code"] == 200:
            note["content"] = resp["text"]
            _remember_preview(url, version, resp["text"])

    await asyncio.gather(*(fill(note) for note in notes[:PREVIEW_LIMIT]))

def _default_meta() -> dict:
    return {"version": META_VERSION, "notes": {}}


def _meta_url(provider) -> str:
    return provider.file_url(META_PATH)


async def _read_meta(provider) -> dict | None:
    """The shared index, or ``None`` when it exists but cannot be read.

    A missing file is a normal first run and reads as an empty index; a
    corrupt or unreachable one is reported rather than overwritten, because
    silently replacing it would lose every pin and archived path.
    """
    import json

    from ..http_client import request as http_request

    # Nextcloud connects stall in bursts; one quick retry keeps a single
    # stalled connect from blanking the whole index (pins, colours, archive).
    resp: dict | None = None
    for attempt in range(2):
        try:
            resp = await http_request(
                "GET", _meta_url(provider),
                auth=(provider.username, provider.password), verify=False,
            )
            break
        except Exception as e:  # retry once, then report
            if attempt == 0:
                log.info("Notes index read failed; retrying: %s", str(e) or type(e).__name__)
                await asyncio.sleep(0.4)
                continue
            raise
    assert resp is not None
    if resp["status_code"] == 404:
        return _default_meta()
    if resp["status_code"] != 200:
        return None
    try:
        meta = json.loads(resp["text"] or "{}")
    except ValueError:
        log.warning("Notes index at %s is not valid JSON", META_PATH)
        return None
    if not isinstance(meta, dict) or not isinstance(meta.get("notes", {}), dict):
        log.warning("Notes index at %s has an unexpected shape", META_PATH)
        return None
    meta.setdefault("version", META_VERSION)
    meta.setdefault("notes", {})
    return meta


async def _write_meta(provider, meta: dict) -> tuple[bool, str]:
    import json

    from ..http_client import request as http_request

    payload = json.dumps(meta, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(payload) > META_MAX_BYTES:
        return False, "The notes index is too large to store."
    await provider.ensure_directory("Notes")
    resp = await http_request(
        "PUT", _meta_url(provider),
        data=payload,
        auth=(provider.username, provider.password),
        headers={"Content-Type": "application/json; charset=utf-8"},
        verify=False,
    )
    if resp["status_code"] in (200, 201, 204):
        return True, ""
    return False, f"Could not write the notes index (HTTP {resp['status_code']})."


def _in_archive_or_trash(path: str) -> bool:
    return path.startswith(ARCHIVE_DIR + "/") or path.startswith(TRASH_DIR + "/")


#: One lock per account while a note mutation reads and writes the shared
#: index. Bulk archive/trash/delete taps arrive as parallel requests; without
#: this each one read the same index and the last writer won, so entries for
#: the other moves vanished. Nextcloud writes stay the slow part; the lock
#: covers only the read-modify-write of the small dotfile.
_note_index_locks: dict[str, asyncio.Lock] = {}


def _note_index_lock(provider) -> asyncio.Lock:
    key = getattr(provider, "username", "") or "unknown"
    lock = _note_index_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _note_index_locks[key] = lock
    return lock


async def _index_after_move(provider, source: str, destination: str) -> None:
    """Keep pin/colour/reminder state attached to a moved note, best effort.

    Moving into Archive or Trash records where the note came from so it can be
    restored; moving back out clears that pointer. The note itself has already
    moved, so a failed index write only costs the extra metadata.
    """
    async with _note_index_lock(provider):
        meta = await _read_meta(provider)
        if meta is None:
            return
        notes = meta["notes"]
        entry = notes.pop(source, None)
        if entry is None and not _in_archive_or_trash(destination):
            return
        entry = dict(entry or {})
        if _in_archive_or_trash(destination):
            entry["previousPath"] = source
        else:
            entry.pop("previousPath", None)
        notes[destination] = entry
        ok, reason = await _write_meta(provider, meta)
        if not ok:
            log.warning("Could not update the notes index after moving %s: %s", source, reason)


async def _index_forget(provider, *paths: str) -> None:
    async with _note_index_lock(provider):
        meta = await _read_meta(provider)
        if meta is None:
            return
        notes = meta["notes"]
        if not any(path in notes for path in paths):
            return
        for path in paths:
            notes.pop(path, None)
        ok, reason = await _write_meta(provider, meta)
        if not ok:
            log.warning("Could not update the notes index after deleting a note: %s", reason)


async def _path_exists(provider, path: str) -> bool:
    status, _ = await _propfind(provider, path, "0")
    return status == 207


async def _unique_note_path(provider, directory: str, file_title: str) -> tuple[str, str]:
    """``directory/<file_title>.md``, numbered until it does not overwrite a note.

    PUT replaces a file with no warning, so a duplicate has to prove the name
    is free first. Returns the path and the title (file name without ``.md``)
    so a caller can use the same name in the new note's header.
    """
    candidate = f"{directory}/{file_title}.md"
    if not await _path_exists(provider, candidate):
        return candidate, file_title
    for suffix in [" (copy)"] + [f" (copy {n})" for n in range(2, 100)]:
        candidate = f"{directory}/{file_title}{suffix}.md"
        if not await _path_exists(provider, candidate):
            return candidate, f"{file_title}{suffix}"
    return f"{directory}/{file_title} (copy {len(_preview_cache) + 2}).md", f"{file_title} (copy {len(_preview_cache) + 2})"


async def _move_note(provider, source: str, destination: str) -> tuple[bool, str]:
    from ..http_client import request as http_request

    await provider.ensure_directory(destination.rsplit("/", 1)[0] or "Notes")
    resp = await http_request(
        "MOVE", provider.file_url(source),
        auth=(provider.username, provider.password),
        headers={
            "Destination": provider.file_url(destination),
            "Overwrite": "T",
        },
        verify=False,
        timeout=60,
    )
    if resp["status_code"] in (200, 201, 204):
        return True, ""
    return False, f"Could not move the note (HTTP {resp['status_code']})."


#: Attachments, as Nextcloud Notes keeps them: files in a hidden folder
#: beside the note named after the note's Nextcloud file id
#: (``Notes/.attachments.4242/photo.jpg``), linked from the markdown with a
#: relative path (``![](.attachments.4242/photo.jpg)``). The note stays plain
#: markdown and both apps show the same pictures.
ATTACHMENT_MAX_BYTES = 10 * 1024 * 1024
#: A link Jarvis will follow: one file directly inside an attachments folder.
_ATTACHMENT_LINK = re.compile(r"^(\.attachments\.[0-9]{1,20})/([^/\\]{1,255})$")
#: Attachment folders a note's markdown points at.
_ATTACHMENT_REF = re.compile(r"\]\(<?(\.attachments\.[0-9]{1,20})/[^)\s>]+>?\)")


def _note_dir(path: str) -> str:
    path = path.strip("/")
    return path.rsplit("/", 1)[0] if "/" in path else ""


def _in_dir(directory: str, name: str) -> str:
    return f"{directory}/{name}" if directory else name


def attachment_folders(text: str | None) -> list[str]:
    """The ``.attachments.<id>`` folders a note's markdown links to, in order."""
    return list(dict.fromkeys(_ATTACHMENT_REF.findall(text or "")))


def _attachment_path(note_path: str, link: str | None) -> str | None:
    """The file a relative attachment link names, or None for anything else
    (another folder, ``..``, an absolute path or a URL never reach WebDAV)."""
    from urllib.parse import unquote

    match = _ATTACHMENT_LINK.match(unquote((link or "").strip()))
    if not match or match.group(2) in (".", ".."):
        return None
    return _in_dir(_note_dir(note_path), f"{match.group(1)}/{match.group(2)}")


def _attachment_name(filename: str | None) -> str:
    """A file name safe in a markdown link and a URL: letters, digits, dot,
    dash and underscore, keeping the extension."""
    base = (filename or "").strip().rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    stem, dot, ext = base.rpartition(".")
    if not dot:
        stem, ext = base, ""
    clean_stem = re.sub(r"[^A-Za-z0-9_-]+", "-", stem).strip("-")[:80] or "attachment"
    clean_ext = re.sub(r"[^A-Za-z0-9]+", "", ext)[:10].lower()
    return f"{clean_stem}.{clean_ext}" if clean_ext else clean_stem


async def _carry_attachments(provider, text: str | None, source: str, destination: str) -> None:
    """Move the attachment folders a note links to beside it in its new
    folder, so its relative links still resolve. Best effort: the note has
    already moved, and a missing folder simply has nothing to carry."""
    from ..http_client import request as http_request

    old_dir, new_dir = _note_dir(source), _note_dir(destination)
    if old_dir == new_dir:
        return
    for folder in attachment_folders(text):
        resp = await http_request(
            "MOVE", provider.file_url(_in_dir(old_dir, folder)),
            auth=(provider.username, provider.password),
            headers={"Destination": provider.file_url(_in_dir(new_dir, folder)), "Overwrite": "F"},
            verify=False, timeout=60,
        )
        if resp["status_code"] not in (201, 204, 404):
            log.warning("Attachments %s did not follow %s (HTTP %s)", folder, destination, resp["status_code"])


async def _drop_attachments(provider, text: str | None, note_path: str) -> None:
    """Delete the attachment folders a note deleted for good linked to."""
    from ..http_client import request as http_request

    for folder in attachment_folders(text):
        resp = await http_request(
            "DELETE", provider.file_url(_in_dir(_note_dir(note_path), folder)),
            auth=(provider.username, provider.password), verify=False,
        )
        if resp["status_code"] not in (200, 204, 404):
            log.warning("Attachments %s of %s were not deleted (HTTP %s)", folder, note_path, resp["status_code"])


async def _copy_attachments(provider, content: str, new_path: str, new_title: str, directory: str) -> None:
    """Give a duplicated note its own copy of the one attachment folder it
    links to, under its own file id, so deleting either note never takes the
    other's pictures. Best effort: on any failure the copy keeps linking to
    the original's files. A note linking to several folders (made elsewhere)
    keeps sharing them."""
    from ..http_client import request as http_request

    folders = attachment_folders(content)
    if len(folders) != 1:
        return
    file_id, _ = await _file_id(provider, new_path)
    if not file_id:
        return
    own = f".attachments.{file_id}"
    resp = await http_request(
        "COPY", provider.file_url(_in_dir(directory, folders[0])),
        auth=(provider.username, provider.password),
        headers={"Destination": provider.file_url(_in_dir(directory, own)), "Overwrite": "F", "Depth": "infinity"},
        verify=False, timeout=120,
    )
    if resp["status_code"] not in (201, 204):
        log.warning("Attachments of %s were not copied (HTTP %s)", new_path, resp["status_code"])
        return
    relinked = content.replace(f"]({folders[0]}/", f"]({own}/").replace(f"](<{folders[0]}/", f"](<{own}/")
    await http_request(
        "PUT", provider.file_url(new_path), data=relinked.encode("utf-8"),
        auth=(provider.username, provider.password),
        headers={"Content-Type": "text/markdown; charset=utf-8"}, verify=False,
    )


async def _note_text(provider, path: str) -> str | None:
    from ..http_client import request as http_request

    resp = await http_request("GET", provider.file_url(path), auth=(provider.username, provider.password), verify=False)
    return resp["text"] if resp["status_code"] == 200 else None


async def _note_attachment(provider, req: NoteRequest) -> ExecutionResult:
    """Upload, read or delete one attachment of a saved note."""
    import base64
    import mimetypes

    from ..http_client import request as http_request

    service = f"note_{req.action}"
    note_path = (req.path or "").strip("/")
    if not note_path.endswith(".md"):
        return ExecutionResult(status="FAILURE", message="Attachments need a saved note (path).", service=service)
    auth = (provider.username, provider.password)

    if req.action == "attachment_upload":
        try:
            data = base64.b64decode(req.data or "", validate=True)
        except (ValueError, TypeError):
            return ExecutionResult(status="FAILURE", message="The attachment was not readable.", service=service)
        if not data:
            return ExecutionResult(status="FAILURE", message="The attachment is empty.", service=service)
        if len(data) > ATTACHMENT_MAX_BYTES:
            limit = ATTACHMENT_MAX_BYTES // (1024 * 1024)
            return ExecutionResult(status="FAILURE", message=f"Attachments can be up to {limit} MB.", service=service)
        # Nextcloud Notes files a note's attachments under its file id.
        file_id, reason = await _file_id(provider, note_path)
        if not file_id:
            return ExecutionResult(status="FAILURE", message=reason, service=service)
        folder = f".attachments.{file_id}"
        folder_path = _in_dir(_note_dir(note_path), folder)
        await provider.ensure_directory(folder_path)
        name = _attachment_name(req.filename)
        stem, dot, ext = name.rpartition(".")
        if not dot:
            stem, ext = name, ""
        for n in range(1, 100):
            if n > 1:
                name = f"{stem}-{n}.{ext}" if ext else f"{stem}-{n}"
            if not await _path_exists(provider, f"{folder_path}/{name}"):
                break
        else:
            return ExecutionResult(status="FAILURE", message="Too many attachments with that name.", service=service)
        mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
        if not await provider.upload_file(f"{folder_path}/{name}", data, mime):
            return ExecutionResult(status="FAILURE", message="Could not store the attachment in Nextcloud.", service=service)
        return ExecutionResult(
            status="SUCCESS", message=f"Attached {name}.", service=service,
            detail={"link": f"{folder}/{name}", "name": name, "mime": mime, "size": len(data)},
        )

    target = _attachment_path(note_path, req.attachment)
    if not target:
        return ExecutionResult(status="FAILURE", message="That is not one of this note's attachments.", service=service)

    if req.action == "attachment_read":
        status, content_type, body = await provider.download_file(target)
        if status == 404:
            return ExecutionResult(status="FAILURE", message="That attachment is not there any more.", service=service)
        if status != 200:
            return ExecutionResult(status="FAILURE", message=f"Could not read the attachment (HTTP {status}).", service=service)
        if len(body) > ATTACHMENT_MAX_BYTES * 2:
            return ExecutionResult(status="FAILURE", message="That attachment is too large to show here.", service=service)
        mime = (content_type or "").split(";", 1)[0].strip()
        if not mime or mime == "application/octet-stream":
            mime = mimetypes.guess_type(target)[0] or "application/octet-stream"
        return ExecutionResult(
            status="SUCCESS", message="Attachment read.", service=service,
            detail={"data": base64.b64encode(body).decode("ascii"), "mime": mime, "size": len(body)},
        )

    # attachment_delete: the file only; the link is the editor's to remove.
    resp = await http_request("DELETE", provider.file_url(target), auth=auth, verify=False)
    if resp["status_code"] not in (200, 204, 404):
        return ExecutionResult(
            status="FAILURE", message=f"Could not delete the attachment (HTTP {resp['status_code']}).", service=service,
        )
    return ExecutionResult(status="SUCCESS", message="Attachment deleted.", service=service, detail={"attachment": req.attachment})


#: Nextcloud keeps earlier copies of every file (the files_versions app) and
#: serves them over WebDAV at /remote.php/dav/versions/<user>/versions/<fileid>.
#: A version id is the time it was made; anything else is refused before it
#: can reach a URL.
_VERSION_ID = re.compile(r"^[0-9]{1,20}$")
_FILE_ID_PROPS = (
    '<?xml version="1.0"?><d:propfind xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns">'
    "<d:prop><oc:fileid/></d:prop></d:propfind>"
)
_VERSION_PROPS = (
    '<?xml version="1.0"?><d:propfind xmlns:d="DAV:" xmlns:nc="http://nextcloud.org/ns">'
    "<d:prop><d:getlastmodified/><d:getcontentlength/>"
    "<nc:version-label/><nc:version-author/></d:prop></d:propfind>"
)


def _versions_root(provider) -> str:
    from urllib.parse import quote

    return f"{provider.base_url.rstrip('/')}/remote.php/dav/versions/{quote(provider.username)}"


async def _file_id(provider, path: str) -> tuple[str | None, str]:
    """Nextcloud's numeric id of a file (versions are filed under it), or why not."""
    from .. import http_client

    resp = await http_client.request(
        "PROPFIND", provider.file_url(path),
        auth=(provider.username, provider.password),
        headers={"Depth": "0", "Content-Type": "application/xml; charset=utf-8"},
        data=_FILE_ID_PROPS, verify=False, timeout=30,
    )
    if resp["status_code"] == 404:
        return None, "That note is not there any more."
    if resp["status_code"] != 207:
        return None, f"Could not look up the note (HTTP {resp['status_code']})."
    found = ET.fromstring(resp.get("text") or "").find(".//{http://owncloud.org/ns}fileid")
    if found is None or not (found.text or "").strip().isdigit():
        return None, "Nextcloud did not report an id for this note."
    return found.text.strip(), ""


def _parse_versions(xml_text: str) -> list[dict]:
    """The versions in a PROPFIND reply, newest first (the collection itself skipped)."""
    from email.utils import parsedate_to_datetime

    ns = {"d": "DAV:", "nc": "http://nextcloud.org/ns"}
    versions: list[dict] = []
    for response in ET.fromstring(xml_text).findall(".//d:response", ns):
        href = (response.findtext("d:href", "", ns) or "").rstrip("/")
        version_id = href.rsplit("/", 1)[-1]
        if not _VERSION_ID.match(version_id) or "/versions/" not in href or href.endswith(f"/versions/{version_id}"):
            # The folder named by the file id answers too; it is not a version.
            continue
        modified = response.findtext(".//d:getlastmodified", "", ns) or ""
        try:
            stamp = parsedate_to_datetime(modified).isoformat()
        except (TypeError, ValueError):
            stamp = ""
        size = response.findtext(".//d:getcontentlength", "", ns) or "0"
        versions.append({
            "id": version_id,
            "modified": stamp,
            "size": int(size) if size.isdigit() else 0,
            "label": (response.findtext(".//nc:version-label", "", ns) or "").strip(),
            "author": (response.findtext(".//nc:version-author", "", ns) or "").strip(),
        })
    versions.sort(key=lambda v: int(v["id"]), reverse=True)
    return versions


async def _note_versions(provider, req: NoteRequest) -> ExecutionResult:
    """List, read or restore earlier versions of one note."""
    from .. import http_client

    path = (req.path or "").lstrip("/")
    service = f"note_{req.action}"
    if not path:
        return ExecutionResult(status="FAILURE", message=f"{req.action} requires path.", service=service)
    if req.action != "versions" and not _VERSION_ID.match(req.version or ""):
        return ExecutionResult(status="FAILURE", message="That is not a version of this note.", service=service)
    file_id, reason = await _file_id(provider, path)
    if not file_id:
        return ExecutionResult(status="FAILURE", message=reason, service=service)
    folder = f"{_versions_root(provider)}/versions/{file_id}"
    auth = (provider.username, provider.password)

    if req.action == "versions":
        resp = await http_client.request(
            "PROPFIND", folder, auth=auth,
            headers={"Depth": "1", "Content-Type": "application/xml; charset=utf-8"},
            data=_VERSION_PROPS, verify=False, timeout=30,
        )
        if resp["status_code"] == 404:
            # The versions app is off, or the file has never changed.
            return ExecutionResult(status="SUCCESS", message="No earlier versions.", service=service, detail={"versions": []})
        if resp["status_code"] != 207:
            return ExecutionResult(
                status="FAILURE",
                message=f"Nextcloud would not list this note's versions (HTTP {resp['status_code']}).",
                service=service,
            )
        versions = _parse_versions(resp.get("text") or "")
        return ExecutionResult(
            status="SUCCESS", message=f"{len(versions)} earlier versions.", service=service,
            detail={"versions": versions, "path": path},
        )

    version_url = f"{folder}/{req.version}"
    if req.action == "version_read":
        resp = await http_client.request("GET", version_url, auth=auth, verify=False, timeout=30)
        if resp["status_code"] != 200:
            return ExecutionResult(
                status="FAILURE", message=f"Could not read that version (HTTP {resp['status_code']}).", service=service,
            )
        return ExecutionResult(status="SUCCESS", message=resp["text"], service=service, detail={"version": req.version})

    # Restore: Nextcloud keeps the current text as a version of its own first,
    # so a restore can itself be undone from the same list.
    resp = await http_client.request(
        "MOVE", version_url, auth=auth,
        headers={"Destination": f"{_versions_root(provider)}/restore/target"},
        verify=False, timeout=60,
    )
    if resp["status_code"] not in (200, 201, 204):
        return ExecutionResult(
            status="FAILURE", message=f"Could not restore that version (HTTP {resp['status_code']}).", service=service,
        )
    _forget_preview(provider.file_url(path))
    return ExecutionResult(
        status="SUCCESS", message="Earlier version restored.", service=service,
        detail={"path": path, "version": req.version},
    )


#: Nextcloud's sharing API (OCS). A note is shared like any file: with a
#: Nextcloud user (shareType 0), read only (1) or read and edit (1 + 2).
_SHARES = "/ocs/v2.php/apps/files_sharing/api/v1/shares"
_SHAREES = "/ocs/v2.php/apps/files_sharing/api/v1/sharees"
_SHARE_USER = 0
_PERM_READ = 1
_PERM_UPDATE = 2
_SHARE_ID = re.compile(r"^[0-9]{1,20}$")


def _user_share(share: dict) -> dict | None:
    """One person a note is shared with, as the page shows it."""
    if int(share.get("share_type", -1)) != _SHARE_USER:
        return None
    permissions = int(share.get("permissions") or 0)
    return {
        "id": str(share.get("id")),
        "with": str(share.get("share_with") or ""),
        "name": str(share.get("share_with_displayname") or share.get("share_with") or ""),
        "can_edit": bool(permissions & _PERM_UPDATE),
    }


async def _note_sharing(provider, req: NoteRequest) -> ExecutionResult:
    """Who a note is shared with, who it could be, and sharing or unsharing it."""
    service = f"note_{req.action}"
    path = (req.path or "").lstrip("/")

    if req.action == "sharees":
        ok, data, message = await provider.request(
            "GET", _SHAREES,
            params={"search": (req.search or "").strip(), "itemType": "file", "perPage": "50", "lookup": "false"},
        )
        if not ok:
            return ExecutionResult(status="FAILURE", message=message or "Could not list the people to share with.", service=service)
        people: dict[str, dict] = {}
        data = data if isinstance(data, dict) else {}
        for bucket in ((data.get("exact") or {}).get("users") or [], data.get("users") or []):
            for entry in bucket:
                value = entry.get("value") or {}
                user_id = str(value.get("shareWith") or "")
                if user_id and user_id != provider.username and int(value.get("shareType", _SHARE_USER)) == _SHARE_USER:
                    people.setdefault(user_id, {"id": user_id, "name": str(entry.get("label") or user_id)})
        return ExecutionResult(
            status="SUCCESS", message=f"{len(people)} people.", service=service,
            detail={"people": sorted(people.values(), key=lambda p: p["name"].lower())},
        )

    if req.action == "share_remove":
        if not _SHARE_ID.match(req.share_id or ""):
            return ExecutionResult(status="FAILURE", message="That is not a share of this note.", service=service)
        ok, _data, message = await provider.request("DELETE", f"{_SHARES}/{req.share_id}")
        if not ok:
            return ExecutionResult(status="FAILURE", message=message or "Could not stop sharing.", service=service)
        return ExecutionResult(status="SUCCESS", message="No longer shared.", service=service, detail={"share_id": req.share_id})

    if not path:
        return ExecutionResult(status="FAILURE", message=f"{req.action} requires path.", service=service)

    if req.action == "share_add":
        share_with = (req.share_with or "").strip()
        if not share_with or share_with == provider.username:
            return ExecutionResult(status="FAILURE", message="Pick someone to share this note with.", service=service)
        permissions = _PERM_READ | (_PERM_UPDATE if req.can_edit else 0)
        ok, data, message = await provider.request(
            "POST", _SHARES,
            data={"path": f"/{path}", "shareType": str(_SHARE_USER), "shareWith": share_with, "permissions": str(permissions)},
        )
        if not ok:
            return ExecutionResult(status="FAILURE", message=message or "Could not share the note.", service=service)
        share = _user_share(data) if isinstance(data, dict) else None
        return ExecutionResult(status="SUCCESS", message="Shared.", service=service, detail={"share": share})

    ok, data, message = await provider.request(
        "GET", _SHARES, params={"path": f"/{path}", "reshares": "false", "subfiles": "false"},
    )
    if not ok:
        if "not exist" in (message or "").lower():
            return ExecutionResult(status="FAILURE", message="That note is not there any more.", service=service)
        return ExecutionResult(status="FAILURE", message=message or "Could not list who this note is shared with.", service=service)
    shares = [s for s in (_user_share(item) for item in (data or []) if isinstance(item, dict)) if s]
    return ExecutionResult(
        status="SUCCESS", message=f"Shared with {len(shares)}.", service=service,
        detail={"shares": shares, "path": path},
    )


async def handle_note(req: NoteRequest) -> ExecutionResult:
    storage_mode = getattr(req, "storage", "nextcloud")

    if storage_mode == "local":
        return await _handle_local_note(req)
    else:
        return await _handle_nextcloud_note(req)

def _local_note_path(raw_path: str) -> Path:
    """Resolve a client-supplied local-note path inside ``LOCAL_NOTES_ROOT``.

    ``path`` arrives in the request body, so it must never be able to point
    outside the notes directory: without this, one POST could read
    ``/proc/self/environ`` (which carries the internal secret) or overwrite a
    file in the container.
    """
    root = Path(LOCAL_NOTES_ROOT).resolve()
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError("That path is outside the notes folder.")
    return resolved


async def _handle_local_note(req: NoteRequest) -> ExecutionResult:
    os.makedirs(LOCAL_NOTES_ROOT, exist_ok=True)

    action = req.action

    try:
        if action == "create":
            safe_title = "".join([c for c in (req.title or "untitled") if c.isalnum() or c in (" ", "-", "_")]).strip()
            filename = f"{safe_title}.md"
            file_path = Path(LOCAL_NOTES_ROOT) / filename
            content = f"# {req.title}\nCategory: {req.category}\n\n{req.content or ''}"
            file_path.write_text(content, encoding='utf-8')
            return ExecutionResult(status="SUCCESS", message=f"Local note '{req.title}' created.", service="note_local")

        elif action == "read":
            if req.path:
                file_path = _local_note_path(req.path)
            else:
                safe_title = "".join([c for c in (req.title or "") if c.isalnum() or c in (" ", "-", "_")]).strip()
                file_path = Path(LOCAL_NOTES_ROOT) / f"{safe_title}.md"
            if file_path.exists():
                return ExecutionResult(status="SUCCESS", message=file_path.read_text(encoding='utf-8'), service="note_local")
            return ExecutionResult(status="FAILURE", message=f"Local note '{req.title}' not found.", service="note_local")

        elif action == "append":
            if req.path:
                file_path = _local_note_path(req.path)
            else:
                safe_title = "".join([c for c in (req.title or "") if c.isalnum() or c in (" ", "-", "_")]).strip()
                file_path = Path(LOCAL_NOTES_ROOT) / f"{safe_title}.md"
            existing = file_path.read_text(encoding='utf-8') if file_path.exists() else ""
            new_content = f"{existing}\n\n- [ ] {req.content}"
            file_path.write_text(new_content, encoding='utf-8')
            return ExecutionResult(status="SUCCESS", message=f"Appended to local note '{req.title}'.", service="note_local")

        elif action == "delete":
            if req.path:
                file_path = _local_note_path(req.path)
            else:
                safe_title = "".join([c for c in (req.title or "") if c.isalnum() or c in (" ", "-", "_")]).strip()
                file_path = Path(LOCAL_NOTES_ROOT) / f"{safe_title}.md"
            if file_path.exists():
                file_path.unlink()
                return ExecutionResult(status="SUCCESS", message=f"Local note '{req.title}' deleted.", service="note_local")
            return ExecutionResult(status="FAILURE", message=f"Local note '{req.title}' not found.", service="note_local")

        elif action == "list":
            notes = []
            for f in Path(LOCAL_NOTES_ROOT).rglob("*.md"):
                notes.append({
                    "title": f.stem,
                    "path": str(f),
                    "size": f.stat().st_size,
                    "modified": f.stat().st_mtime,
                })
            notes.sort(key=lambda x: x["modified"], reverse=True)
            return ExecutionResult(
                status="SUCCESS",
                message="Notes listed",
                service="note_list",
                detail={"notes": notes}
            )

        return ExecutionResult(status="FAILURE", message=f"Action {action} failed or not implemented.", service="note_local")

    except ValueError as e:
        # A path outside the notes folder is a refusal, not a server fault.
        return ExecutionResult(status="FAILURE", message=str(e), service="note_local")

    except Exception as e:
        log.error(f"Local Note error: {e}")
        return ExecutionResult(status="FAILURE", message=f"Local Note error: {e!s}", service="note_local")

async def _handle_nextcloud_note(req: NoteRequest) -> ExecutionResult:
    from ..http_client import request as http_request
    provider = resolve_personal_data_provider(req.user_context)
    if not provider:
        return ExecutionResult(status="FAILURE", message=missing_account_message(req.user_context), service="note")

    action = req.action

    try:
        if action == "create":
            await provider.ensure_directory(req.category or "Notes")
            file_title = provider.sanitize_filename(req.title or "untitled", "note")
            filename = f"{file_title}.md"
            url = provider.file_url(f"{req.category or 'Notes'}/{filename}")
            _forget_preview(url)
            content = _with_header(req.title, req.category, req.content)
            resp = await http_request("PUT", url, data=content.encode('utf-8'), auth=(provider.username, provider.password), verify=False)
            if resp["status_code"] in [200, 201, 204]:
                # The path comes back so a caller that wants to point at this
                # note later records the name that was actually written rather
                # than re-deriving our filename rules and drifting from them.
                return ExecutionResult(
                    status="SUCCESS",
                    message=f"Note '{req.title}' created.",
                    service="note_create",
                    detail={"path": f"{req.category or 'Notes'}/{filename}"},
                )

        elif action == "read":
            if req.path:
                url = provider.file_url(req.path.lstrip("/"))
            else:
                file_title = provider.sanitize_filename(req.title or "", "note")
                url = provider.file_url(f"Notes/{file_title}.md")
            resp = await http_request("GET", url, auth=(provider.username, provider.password), verify=False)
            if resp["status_code"] == 200:
                # The version travels with the text so the editor's save can
                # prove nothing changed in between (If-Match on write).
                return ExecutionResult(
                    status="SUCCESS", message=resp["text"], service="note_read",
                    detail={"etag": _etag(resp)},
                )
            elif resp["status_code"] == 404:
                return ExecutionResult(status="FAILURE", message=f"Note '{req.title}' not found.", service="note_read")

        elif action == "append":
            if req.path:
                url = provider.file_url(req.path.lstrip("/"))
            else:
                file_title = provider.sanitize_filename(req.title or "", "note")
                url = provider.file_url(f"Notes/{file_title}.md")
            r_resp = await http_request("GET", url, auth=(provider.username, provider.password), verify=False)
            existing = r_resp["text"] if r_resp["status_code"] == 200 else ""
            new_content = f"{existing}\n\n- [ ] {req.content}"
            resp = await http_request("PUT", url, data=new_content.encode('utf-8'), auth=(provider.username, provider.password), verify=False)
            if resp["status_code"] in [200, 201, 204]:
                return ExecutionResult(status="SUCCESS", message=f"Appended to '{req.title}'.", service="note_append")

        elif action == "write":
            # Full replace: this is what the editor's Save uses. "append" is a
            # deliberate quick-capture action and must never back a save, or
            # every save duplicates the body into a checklist line.
            category = req.category or "Notes"
            await provider.ensure_directory(category)
            if req.path:
                url = provider.file_url(req.path.lstrip("/"))
            else:
                file_title = provider.sanitize_filename(req.title or "untitled", "note")
                url = provider.file_url(f"{category}/{file_title}.md")
            _forget_preview(url)
            content = _with_header(req.title, category, req.content)
            headers = {"Content-Type": "text/markdown; charset=utf-8"}
            # Only an in-place save can be checked: the version names the
            # file at ``path``. Nextcloud answers 412 when it has changed.
            if req.if_match and req.path:
                headers["If-Match"] = req.if_match
            resp = await http_request(
                "PUT", url, data=content.encode('utf-8'),
                auth=(provider.username, provider.password), verify=False,
                headers=headers,
            )
            if resp["status_code"] == 412:
                return ExecutionResult(
                    status="FAILURE",
                    message="This note changed somewhere else since it was opened, so it was not overwritten.",
                    service="note_write",
                    detail={"conflict": True, "path": req.path.lstrip("/") if req.path else None},
                )
            if resp["status_code"] in [200, 201, 204]:
                written = f"{category}/{file_title}.md" if not req.path else req.path.lstrip("/")
                return ExecutionResult(
                    status="SUCCESS",
                    message=f"Note '{req.title}' saved.",
                    service="note_write",
                    detail={"path": written, "etag": _etag(resp)},
                )

        elif action == "check_off":
            # Toggle one checklist item ("- [ ] text" <-> "- [x] text").
            if not req.item:
                return ExecutionResult(status="FAILURE", message="check_off requires item text.", service="note_check_off")
            if req.path:
                url = provider.file_url(req.path.lstrip("/"))
            else:
                file_title = provider.sanitize_filename(req.title or "", "note")
                url = provider.file_url(f"Notes/{file_title}.md")
            r_resp = await http_request("GET", url, auth=(provider.username, provider.password), verify=False)
            if r_resp["status_code"] != 200:
                return ExecutionResult(status="FAILURE", message=f"Note '{req.title}' not found.", service="note_check_off")
            target = req.item.strip()
            lines = r_resp["text"].splitlines()
            changed = False
            for idx, line in enumerate(lines):
                if line.startswith("- [ ] ") and _item_matches(line[6:], target):
                    lines[idx] = "- [x] " + line[6:]
                    changed = True
                    break
                if line.lower().startswith("- [x] ") and _item_matches(line[6:], target):
                    lines[idx] = "- [ ] " + line[6:]
                    changed = True
                    break
            if not changed:
                return ExecutionResult(status="FAILURE", message=f"Checklist item '{target}' not found.", service="note_check_off")
            _forget_preview(url)
            new_content = "\n".join(lines)
            if r_resp["text"].endswith("\n"):
                new_content += "\n"
            resp = await http_request("PUT", url, data=new_content.encode('utf-8'), auth=(provider.username, provider.password), verify=False)
            if resp["status_code"] in [200, 201, 204]:
                return ExecutionResult(status="SUCCESS", message=f"Toggled '{target}'.", service="note_check_off")

        elif action == "delete":
            if req.path:
                url = provider.file_url(req.path.lstrip("/"))
                deleted_path = req.path.lstrip("/")
            else:
                file_title = provider.sanitize_filename(req.title or "", "note")
                deleted_path = f"Notes/{file_title}.md"
                url = provider.file_url(deleted_path)
            _forget_preview(url)
            # Its attachments go with it (as in Nextcloud Notes), unless this
            # delete is the second half of a rename: then they follow the
            # new copy, which links to the same files.
            text = await _note_text(provider, deleted_path)
            resp = await http_request("DELETE", url, auth=(provider.username, provider.password), verify=False)
            if resp["status_code"] in [200, 204]:
                if req.attachments_to:
                    await _carry_attachments(provider, text, deleted_path, req.attachments_to.lstrip("/"))
                else:
                    await _drop_attachments(provider, text, deleted_path)
                await _index_forget(provider, deleted_path)
                return ExecutionResult(status="SUCCESS", message=f"Note '{req.title}' deleted.", service="note_delete")

        elif action == "list":
            directories = req.directories or DEFAULT_NOTES_DIRS
            all_notes = []
            # Listing never creates the folder: a missing one has no notes, and
            # the existence check cost Nextcloud another seconds-long request.
            for base_dir in directories:
                all_notes.extend(await _walk_webdav_dir(provider, base_dir))
            # Newest first by time, not by the text of an HTTP date.
            all_notes.sort(key=_modified_ts, reverse=True)
            if req.previews:
                await _attach_previews(provider, all_notes)
            return ExecutionResult(
                status="SUCCESS",
                message="Notes listed",
                service="note_list",
                detail={"notes": all_notes, "directories": directories}
            )

        elif action == "sync_rag":
            directories = req.directories or DEFAULT_NOTES_DIRS
            synced = []
            for base_dir in directories:
                await provider.ensure_directory(base_dir)
                notes = await _walk_webdav_dir(provider, base_dir)
                for note in notes:
                    note_path = note["path"]
                    url = provider.file_url(note_path.lstrip("/"))
                    resp = await http_request("GET", url, auth=(provider.username, provider.password), verify=False)
                    if resp["status_code"] == 200:
                        synced.append({
                            "path": note_path,
                            "content": resp["text"],
                            "size": len(resp["text"]),
                        })
            return ExecutionResult(
                status="SUCCESS",
                message=f"Synced {len(synced)} notes for RAG indexing",
                service="note_sync_rag",
                detail={"synced": synced}
            )

        elif action == "meta_read":
            meta = await _read_meta(provider)
            if meta is None:
                return ExecutionResult(
                    status="FAILURE",
                    message=f"Notes index at {META_PATH} is unreadable; pins, colours and archive paths could not be loaded.",
                    service="note_meta_read",
                )
            return ExecutionResult(
                status="SUCCESS",
                message="Notes index loaded",
                service="note_meta_read",
                detail={"meta": meta, "path": META_PATH},
            )

        elif action == "meta_write":
            meta = req.meta if isinstance(req.meta, dict) else None
            if meta is None:
                return ExecutionResult(status="FAILURE", message="meta_write requires the index as 'meta'.", service="note_meta_write")
            if not isinstance(meta.get("notes", {}), dict):
                return ExecutionResult(status="FAILURE", message="meta_write: 'notes' must be an object.", service="note_meta_write")
            meta = dict(meta)
            meta.setdefault("version", META_VERSION)
            meta.setdefault("notes", {})
            async with _note_index_lock(provider):
                ok, reason = await _write_meta(provider, meta)
            if not ok:
                return ExecutionResult(status="FAILURE", message=reason, service="note_meta_write")
            return ExecutionResult(
                status="SUCCESS",
                message="Notes index saved",
                service="note_meta_write",
                detail={"meta": meta, "path": META_PATH},
            )

        elif action == "move":
            source = (req.path or "").lstrip("/")
            destination = (req.destination or "").lstrip("/")
            if not source or not destination:
                return ExecutionResult(status="FAILURE", message="move requires path and destination.", service="note_move")
            if source == destination:
                return ExecutionResult(status="SUCCESS", message="The note is already there.", service="note_move", detail={"path": destination})
            # A move never overwrites an existing note. Flattening used to let
            # "Notes/Work/Plan.md" replace "Notes/Archive/Plan.md", and a
            # restore could replace whatever took the old name. A collision
            # gets a numbered name instead.
            if await _path_exists(provider, destination):
                directory = destination.rsplit("/", 1)[0] if "/" in destination else "Notes"
                stem = destination.rsplit("/", 1)[-1].removesuffix(".md")
                destination, _ = await _unique_note_path(provider, directory, stem)
            # Into another folder (archive, trash, restore), its attachment
            # folders move beside it so the relative links keep working.
            text = await _note_text(provider, source) if _note_dir(source) != _note_dir(destination) else None
            ok, reason = await _move_note(provider, source, destination)
            if not ok:
                return ExecutionResult(status="FAILURE", message=reason, service="note_move")
            await _carry_attachments(provider, text, source, destination)
            _forget_preview(provider.file_url(source))
            _forget_preview(provider.file_url(destination))
            await _index_after_move(provider, source, destination)
            return ExecutionResult(
                status="SUCCESS",
                message="Note moved.",
                service="note_move",
                detail={"path": destination, "from": source},
            )

        elif action == "duplicate":
            source = (req.path or "").lstrip("/")
            if not source:
                return ExecutionResult(status="FAILURE", message="duplicate requires path.", service="note_duplicate")
            url = provider.file_url(source)
            resp = await http_request("GET", url, auth=(provider.username, provider.password), verify=False)
            if resp["status_code"] != 200:
                return ExecutionResult(status="FAILURE", message=f"Note '{req.title or source}' not found.", service="note_duplicate")
            directory = source.rsplit("/", 1)[0] if "/" in source else "Notes"
            file_title = provider.sanitize_filename(
                (req.title or source.rsplit("/", 1)[-1].removesuffix(".md")), "note"
            )
            new_path, new_title = await _unique_note_path(provider, directory, file_title)
            content = _with_header(new_title, directory, note_body(resp["text"]))
            put = await http_request(
                "PUT", provider.file_url(new_path),
                data=content.encode("utf-8"),
                auth=(provider.username, provider.password),
                headers={"Content-Type": "text/markdown; charset=utf-8"},
                verify=False,
            )
            if put["status_code"] not in (200, 201, 204):
                return ExecutionResult(status="FAILURE", message=f"Could not write the copy (HTTP {put['status_code']}).", service="note_duplicate")
            await _copy_attachments(provider, content, new_path, new_title, directory)
            _forget_preview(provider.file_url(new_path))
            return ExecutionResult(
                status="SUCCESS",
                message=f"Copied to '{new_title}'.",
                service="note_duplicate",
                detail={"path": new_path, "title": new_title},
            )

        elif action == "empty_trash":
            trashed = await _walk_webdav_dir(provider, TRASH_DIR)
            deleted = []
            for note in trashed:
                note_path = note["path"]
                url = provider.file_url(note_path.lstrip("/"))
                resp = await http_request("DELETE", url, auth=(provider.username, provider.password), verify=False)
                if resp["status_code"] in (200, 204):
                    deleted.append(note_path)
                    _forget_preview(url)
            # Trashed notes' attachment folders moved into Trash with them;
            # with the notes gone, they go too.
            status, text = await _propfind(provider, TRASH_DIR, "1")
            if status == 207:
                for folder in _md_entries(text, TRASH_DIR)[1]:
                    if folder.rsplit("/", 1)[-1].startswith(".attachments."):
                        await http_request("DELETE", provider.file_url(folder), auth=(provider.username, provider.password), verify=False)
            await _index_forget(provider, *[note["path"] for note in trashed])
            return ExecutionResult(
                status="SUCCESS",
                message=f"Emptied Trash ({len(deleted)} notes deleted).",
                service="note_empty_trash",
                detail={"deleted": deleted},
            )

        elif action in ("versions", "version_read", "version_restore"):
            return await _note_versions(provider, req)

        elif action in ("shares", "sharees", "share_add", "share_remove"):
            return await _note_sharing(provider, req)

        elif action in ("attachment_upload", "attachment_read", "attachment_delete"):
            return await _note_attachment(provider, req)

        return ExecutionResult(status="FAILURE", message=f"Action {action} failed or not implemented.", service="note")

    except Exception as e:
        # A timeout's text is empty; name the error so the log and the
        # person both see what happened.
        reason = str(e) or type(e).__name__
        log.error(f"Note error ({action}): {reason}")
        return ExecutionResult(status="FAILURE", message=f"Note error: {reason}", service="note")


def _modified_ts(note: dict) -> float:
    from email.utils import parsedate_to_datetime

    try:
        return parsedate_to_datetime(note.get("modified") or "").timestamp()
    except (TypeError, ValueError):
        return 0.0

#: Folder listings run at most this many at once when Nextcloud refuses a
#: whole-tree listing (each one costs Nextcloud a second or more).
WALK_CONCURRENCY = 4


async def _propfind(provider, dir_path: str, depth: str) -> tuple[int, str]:
    from .. import http_client

    resp = await http_client.request(
        "PROPFIND",
        provider.file_url(dir_path),
        auth=(provider.username, provider.password),
        headers={"Depth": depth, "Content-Type": "application/xml; charset=utf-8"},
        data=_LIST_PROPS,
        verify=False,
        timeout=60,
    )
    return resp["status_code"], resp.get("text") or ""


def _md_entries(xml_text: str, base_dir: str) -> tuple[list[dict], list[str]]:
    """Notes (.md files) and sub-folder paths in a PROPFIND reply, with paths
    relative to the user's files and starting at ``base_dir``."""
    root = ET.fromstring(xml_text)
    ns = {"DAV": "DAV:"}
    anchor = f"/{base_dir.strip('/')}/"
    notes: list[dict] = []
    folders: list[str] = []
    for response in root.findall(".//DAV:response", ns):
        href = response.findtext("DAV:href", "", ns)
        decoded = _decode_webdav_href(href)
        if anchor not in decoded + "/":
            continue
        inside = (decoded + "/").split(anchor, 1)[-1].strip("/")
        if not inside:
            continue  # the folder itself
        full_path = f"{base_dir.strip('/')}/{inside}"
        if "/.attachments." in f"/{inside}" and not inside.rsplit("/", 1)[-1].startswith(".attachments."):
            continue  # a file inside a note's attachments, never a note itself
        is_collection = href.endswith("/") or response.find(".//DAV:resourcetype/DAV:collection", ns) is not None
        if is_collection:
            folders.append(full_path)
        elif inside.lower().endswith(".md"):
            size_elem = response.find(".//DAV:getcontentlength", ns)
            modified_elem = response.find(".//DAV:getlastmodified", ns)
            notes.append({
                "title": inside.rsplit("/", 1)[-1][:-3],
                "path": full_path,
                "size": int(size_elem.text) if size_elem is not None and size_elem.text else 0,
                "modified": modified_elem.text if modified_elem is not None else "",
            })
    return notes, folders


async def _walk_webdav_dir(provider, base_dir: str) -> list[dict]:
    """Every .md file under ``base_dir``, at any depth.

    One whole-tree listing (Depth: infinity) is a single request; Nextcloud
    spends a second or more on each listing, so walking folder by folder
    (nine sequential requests for a typical Notes tree) took over 20 s. If the
    server refuses infinite depth, folders are listed a few at a time. A
    missing folder simply has no notes.
    """
    try:
        status, text = await _propfind(provider, base_dir, "infinity")
        if status == 207:
            return _md_entries(text, base_dir)[0]
        if status == 404:
            return []
        log.info("Whole-tree listing of %s refused (HTTP %s); listing folders instead", base_dir, status)

        gate = asyncio.Semaphore(WALK_CONCURRENCY)
        notes: list[dict] = []

        async def walk(dir_path: str) -> None:
            async with gate:
                status, text = await _propfind(provider, dir_path, "1")
            if status != 207:
                return
            found, folders = _md_entries(text, base_dir)
            notes.extend(n for n in found if n["path"].rsplit("/", 1)[0] == dir_path.strip("/"))
            await asyncio.gather(*(
                walk(f) for f in folders
                if f.strip("/") != dir_path.strip("/") and not f.rsplit("/", 1)[-1].startswith(".attachments.")
            ))

        await walk(base_dir)
        return notes
    except Exception as e:  # a broken listing shows as no notes, with the reason logged
        log.error("WebDAV listing error at %s: %s", base_dir, str(e) or type(e).__name__)
        return []

def _decode_webdav_href(href: str) -> str:
    from urllib.parse import unquote
    parts = href.split("/")
    return "/".join(unquote(p) for p in parts)

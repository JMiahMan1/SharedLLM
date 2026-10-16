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
            except Exception as e:  # noqa: BLE001 - one unreadable note must not fail the list
                log.warning("Note preview read failed for %s: %s", note["path"], str(e) or type(e).__name__)
                return
        if resp["status_code"] == 200:
            note["content"] = resp["text"]
            _remember_preview(url, version, resp["text"])

    await asyncio.gather(*(fill(note) for note in notes[:PREVIEW_LIMIT]))

async def handle_note(req: NoteRequest) -> ExecutionResult:
    storage_mode = getattr(req, "storage", "nextcloud")

    if storage_mode == "local":
        return await _handle_local_note(req)
    else:
        return await _handle_nextcloud_note(req)

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
                file_path = Path(req.path)
            else:
                safe_title = "".join([c for c in (req.title or "") if c.isalnum() or c in (" ", "-", "_")]).strip()
                file_path = Path(LOCAL_NOTES_ROOT) / f"{safe_title}.md"
            if file_path.exists():
                return ExecutionResult(status="SUCCESS", message=file_path.read_text(encoding='utf-8'), service="note_local")
            return ExecutionResult(status="FAILURE", message=f"Local note '{req.title}' not found.", service="note_local")

        elif action == "append":
            if req.path:
                file_path = Path(req.path)
            else:
                safe_title = "".join([c for c in (req.title or "") if c.isalnum() or c in (" ", "-", "_")]).strip()
                file_path = Path(LOCAL_NOTES_ROOT) / f"{safe_title}.md"
            existing = file_path.read_text(encoding='utf-8') if file_path.exists() else ""
            new_content = f"{existing}\n\n- [ ] {req.content}"
            file_path.write_text(new_content, encoding='utf-8')
            return ExecutionResult(status="SUCCESS", message=f"Appended to local note '{req.title}'.", service="note_local")

        elif action == "delete":
            if req.path:
                file_path = Path(req.path)
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
                return ExecutionResult(status="SUCCESS", message=resp["text"], service="note_read")
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
            resp = await http_request(
                "PUT", url, data=content.encode('utf-8'),
                auth=(provider.username, provider.password), verify=False,
                headers={"Content-Type": "text/markdown; charset=utf-8"},
            )
            if resp["status_code"] in [200, 201, 204]:
                written = f"{category}/{file_title}.md" if not req.path else req.path.lstrip("/")
                return ExecutionResult(
                    status="SUCCESS",
                    message=f"Note '{req.title}' saved.",
                    service="note_write",
                    detail={"path": written},
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
                if line.startswith("- [ ] ") and line[6:].strip() == target:
                    lines[idx] = "- [x] " + line[6:]
                    changed = True
                    break
                if line.lower().startswith("- [x] ") and line[6:].strip() == target:
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
            else:
                file_title = provider.sanitize_filename(req.title or "", "note")
                url = provider.file_url(f"Notes/{file_title}.md")
            _forget_preview(url)
            resp = await http_request("DELETE", url, auth=(provider.username, provider.password), verify=False)
            if resp["status_code"] in [200, 204]:
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
            await asyncio.gather(*(walk(f) for f in folders if f.strip("/") != dir_path.strip("/")))

        await walk(base_dir)
        return notes
    except Exception as e:  # noqa: BLE001 - a broken listing shows as no notes, with the reason logged
        log.error("WebDAV listing error at %s: %s", base_dir, str(e) or type(e).__name__)
        return []

def _decode_webdav_href(href: str) -> str:
    from urllib.parse import unquote
    parts = href.split("/")
    return "/".join(unquote(p) for p in parts)

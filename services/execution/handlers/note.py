# services/execution/handlers/note.py
import logging
import sys
import os
import requests
import xml.etree.ElementTree as ET
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import LOCAL_NOTES_ROOT as _LOCAL_NOTES_ROOT
from pathlib import Path
from typing import Optional

try:
    from schemas import NoteRequest, ExecutionResult
    from personal_data import resolve_personal_data_provider
except ImportError:
    from schemas import NoteRequest, ExecutionResult
    from personal_data import resolve_personal_data_provider

log = logging.getLogger("execution.note")

DEFAULT_NOTES_DIRS = ["Notes"]
LOCAL_NOTES_ROOT = _LOCAL_NOTES_ROOT or "/app/data/notes"

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
        return ExecutionResult(status="FAILURE", message=f"Local Note error: {str(e)}", service="note_local")

async def _handle_nextcloud_note(req: NoteRequest) -> ExecutionResult:
    provider = resolve_personal_data_provider(req.user_context)
    if not provider:
        return ExecutionResult(status="FAILURE", message="Nextcloud credentials missing.", service="note")

    action = req.action
    
    try:
        if action == "create":
            provider.ensure_directory(req.category or "Notes")
            file_title = provider.sanitize_filename(req.title or "untitled", "note")
            filename = f"{file_title}.md"
            url = provider.file_url(f"{req.category or 'Notes'}/{filename}")
            content = f"# {req.title}\nCategory: {req.category}\n\n{req.content or ''}"
            resp = requests.put(url, data=content.encode('utf-8'), auth=(provider.username, provider.password), verify=False)
            if resp.status_code in [200, 201, 204]:
                return ExecutionResult(status="SUCCESS", message=f"Note '{req.title}' created.", service="note_create")
            
        elif action == "read":
            if req.path:
                url = provider.file_url(req.path.lstrip("/"))
            else:
                file_title = provider.sanitize_filename(req.title or "", "note")
                url = provider.file_url(f"Notes/{file_title}.md")
            resp = requests.get(url, auth=(provider.username, provider.password), verify=False)
            if resp.status_code == 200:
                return ExecutionResult(status="SUCCESS", message=resp.text, service="note_read")
            elif resp.status_code == 404:
                return ExecutionResult(status="FAILURE", message=f"Note '{req.title}' not found.", service="note_read")
        
        elif action == "append":
            if req.path:
                url = provider.file_url(req.path.lstrip("/"))
            else:
                file_title = provider.sanitize_filename(req.title or "", "note")
                url = provider.file_url(f"Notes/{file_title}.md")
            r_resp = requests.get(url, auth=(provider.username, provider.password), verify=False)
            existing = r_resp.text if r_resp.status_code == 200 else ""
            new_content = f"{existing}\n\n- [ ] {req.content}"
            resp = requests.put(url, data=new_content.encode('utf-8'), auth=(provider.username, provider.password), verify=False)
            if resp.status_code in [200, 201, 204]:
                return ExecutionResult(status="SUCCESS", message=f"Appended to '{req.title}'.", service="note_append")

        elif action == "delete":
            if req.path:
                url = provider.file_url(req.path.lstrip("/"))
            else:
                file_title = provider.sanitize_filename(req.title or "", "note")
                url = provider.file_url(f"Notes/{file_title}.md")
            resp = requests.delete(url, auth=(provider.username, provider.password), verify=False)
            if resp.status_code in [200, 204]:
                return ExecutionResult(status="SUCCESS", message=f"Note '{req.title}' deleted.", service="note_delete")

        elif action == "list":
            directories = req.directories or DEFAULT_NOTES_DIRS
            all_notes = []
            for base_dir in directories:
                provider.ensure_directory(base_dir)
                notes = await _walk_webdav_dir(provider, base_dir)
                all_notes.extend(notes)
            all_notes.sort(key=lambda x: x.get("modified", ""), reverse=True)
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
                provider.ensure_directory(base_dir)
                notes = await _walk_webdav_dir(provider, base_dir)
                for note in notes:
                    note_path = note["path"]
                    url = provider.file_url(note_path.lstrip("/"))
                    resp = requests.get(url, auth=(provider.username, provider.password), verify=False)
                    if resp.status_code == 200:
                        synced.append({
                            "path": note_path,
                            "content": resp.text,
                            "size": len(resp.text),
                        })
            return ExecutionResult(
                status="SUCCESS",
                message=f"Synced {len(synced)} notes for RAG indexing",
                service="note_sync_rag",
                detail={"synced": synced}
            )

        return ExecutionResult(status="FAILURE", message=f"Action {action} failed or not implemented.", service="note")

    except Exception as e:
        log.error(f"Note error: {e}")
        return ExecutionResult(status="FAILURE", message=f"Note error: {str(e)}", service="note")

async def _walk_webdav_dir(provider, base_dir: str, current_path: str = "") -> list[dict]:
    """Recursively walk a WebDAV directory and return all .md files."""
    dir_path = f"{base_dir}/{current_path}".rstrip("/")
    dir_url = provider.file_url(dir_path)
    
    try:
        resp = requests.request(
            "PROPFIND",
            dir_url,
            auth=(provider.username, provider.password),
            headers={"Depth": "1"},
            verify=False,
            timeout=30,
        )
        
        if resp.status_code != 207:
            return []
        
        notes = []
        root = ET.fromstring(resp.text)
        ns = {"DAV": "DAV:"}
        
        for response in root.findall(".//DAV:response", ns):
            href = response.findtext("DAV:href", "", ns)
            decoded_href = _decode_webdav_href(href)
            relative = decoded_href.split(dir_path, 1)[-1].lstrip("/") if dir_path in decoded_href else decoded_href
            
            is_collection = href.endswith("/") or response.find(".//DAV:resourcetype/DAV:collection", ns) is not None
            
            if is_collection:
                if relative and relative != base_dir.split("/")[-1]:
                    subdir = f"{current_path}/{relative}".lstrip("/") if current_path else relative
                    sub_notes = await _walk_webdav_dir(provider, base_dir, subdir)
                    notes.extend(sub_notes)
            elif relative.lower().endswith(".md"):
                size_elem = response.find(".//DAV:getcontentlength", ns)
                modified_elem = response.find(".//DAV:getlastmodified", ns)
                full_path = f"{base_dir}/{current_path}/{relative}".replace("//", "/")
                
                notes.append({
                    "title": relative.replace(".md", ""),
                    "path": full_path,
                    "size": int(size_elem.text) if size_elem is not None and size_elem.text else 0,
                    "modified": modified_elem.text if modified_elem is not None else "",
                })
        
        return notes
        
    except Exception as e:
        log.error(f"WebDAV walk error at {dir_path}: {e}")
        return []

def _decode_webdav_href(href: str) -> str:
    from urllib.parse import unquote
    parts = href.split("/")
    return "/".join(unquote(p) for p in parts)

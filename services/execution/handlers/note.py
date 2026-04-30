# services/execution/handlers/note.py
import os
import logging
import requests
import urllib.parse
from datetime import datetime
from typing import Optional

try:
    from ..schemas import NoteRequest, ExecutionResult
except ImportError:
    from schemas import NoteRequest, ExecutionResult

log = logging.getLogger("execution.note")

# Settings from env
NEXTCLOUD_URL = os.getenv("NEXTCLOUD_URL")
NEXTCLOUD_USER = os.getenv("NEXTCLOUD_USER")
NEXTCLOUD_PASS = os.getenv("NEXTCLOUD_PASS")
NOTES_DIR = "Notes"

def _get_webdav_url(filename: str = "") -> str:
    base = f"{NEXTCLOUD_URL.rstrip('/')}/remote.php/dav/files/{NEXTCLOUD_USER}/{NOTES_DIR}"
    if filename:
        encoded_name = urllib.parse.quote(filename)
        return f"{base}/{encoded_name}"
    return base

def _ensure_notes_dir():
    url = _get_webdav_url()
    try:
        resp = requests.request("PROPFIND", url, auth=(NEXTCLOUD_USER, NEXTCLOUD_PASS), verify=False)
        if resp.status_code == 404:
            requests.request("MKCOL", url, auth=(NEXTCLOUD_USER, NEXTCLOUD_PASS), verify=False)
    except Exception as e:
        log.error(f"Failed to ensure Notes dir: {e}")

async def handle_note(req: NoteRequest) -> ExecutionResult:
    if not (NEXTCLOUD_URL and NEXTCLOUD_USER and NEXTCLOUD_PASS):
        return ExecutionResult(status="FAILURE", message="Nextcloud credentials missing.", service="note")

    _ensure_notes_dir()
    
    action = req.action
    safe_title = "".join([c for c in req.title if c.isalnum() or c in " -_"]).strip()
    filename = f"{safe_title}.md"
    url = _get_webdav_url(filename)
    
    try:
        if action == "create":
            content = f"# {req.title}\nCategory: {req.category}\n\n{req.content or ''}"
            resp = requests.put(url, data=content.encode('utf-8'), auth=(NEXTCLOUD_USER, NEXTCLOUD_PASS), verify=False)
            if resp.status_code in [200, 201, 204]:
                return ExecutionResult(status="SUCCESS", message=f"Note '{req.title}' created.", service="note_create")
            
        elif action == "read":
            resp = requests.get(url, auth=(NEXTCLOUD_USER, NEXTCLOUD_PASS), verify=False)
            if resp.status_code == 200:
                return ExecutionResult(status="SUCCESS", message=resp.text, service="note_read")
            elif resp.status_code == 404:
                return ExecutionResult(status="FAILURE", message=f"Note '{req.title}' not found.", service="note_read")
        
        elif action == "append":
            # Read first
            r_resp = requests.get(url, auth=(NEXTCLOUD_USER, NEXTCLOUD_PASS), verify=False)
            existing = r_resp.text if r_resp.status_code == 200 else ""
            new_content = f"{existing}\n\n- [ ] {req.content}"
            resp = requests.put(url, data=new_content.encode('utf-8'), auth=(NEXTCLOUD_USER, NEXTCLOUD_PASS), verify=False)
            if resp.status_code in [200, 201, 204]:
                return ExecutionResult(status="SUCCESS", message=f"Appended to '{req.title}'.", service="note_append")

        elif action == "delete":
            resp = requests.delete(url, auth=(NEXTCLOUD_USER, NEXTCLOUD_PASS), verify=False)
            if resp.status_code in [200, 204]:
                return ExecutionResult(status="SUCCESS", message=f"Note '{req.title}' deleted.", service="note_delete")

        return ExecutionResult(status="FAILURE", message=f"Action {action} failed or not implemented.", service="note")

    except Exception as e:
        log.error(f"Note error: {e}")
        return ExecutionResult(status="FAILURE", message=f"Note error: {str(e)}", service="note")

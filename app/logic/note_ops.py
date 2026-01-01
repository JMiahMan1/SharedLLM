# app/logic/note_ops.py
import os
import requests
import urllib.parse
from datetime import datetime
from app.settings import NEXTCLOUD_URL, NEXTCLOUD_USER, NEXTCLOUD_PASS, log, run_blocking

NOTES_DIR = "Notes"  # Standard Nextcloud Notes directory

def _get_webdav_url(filename: str = "") -> str:
    """Constructs the WebDAV URL for the Notes directory or a specific file."""
    base = f"{NEXTCLOUD_URL.rstrip('/')}/remote.php/dav/files/{NEXTCLOUD_USER}/{NOTES_DIR}"
    if filename:
        encoded_name = urllib.parse.quote(filename)
        return f"{base}/{encoded_name}"
    return base

def _ensure_notes_dir():
    """Ensures the Notes directory exists."""
    url = _get_webdav_url()
    try:
        resp = requests.request("PROPFIND", url, auth=(NEXTCLOUD_USER, NEXTCLOUD_PASS), verify=False)
        if resp.status_code == 404:
            log.info(f"Creating Notes directory at {url}")
            requests.request("MKCOL", url, auth=(NEXTCLOUD_USER, NEXTCLOUD_PASS), verify=False)
    except Exception as e:
        log.error(f"Failed to check/create Notes dir: {e}")

async def create_note(title: str, content: str, category: str = "General") -> dict:
    """Creates a new markdown note."""
    if not (NEXTCLOUD_URL and NEXTCLOUD_USER and NEXTCLOUD_PASS):
        return {"status": "error", "msg": "Nextcloud credentials missing"}

    await run_blocking(_ensure_notes_dir)

    # Sanitize title
    safe_title = "".join([c for c in title if c.isalnum() or c in " -_"]).strip()
    if not safe_title: safe_title = f"Note_{int(datetime.now().timestamp())}"
    
    filename = f"{safe_title}.md"
    url = _get_webdav_url(filename)
    
    # Add category header
    full_content = f"# {title}\nCategory: {category}\nCreated: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n{content}"

    def _write():
        return requests.put(url, data=full_content.encode('utf-8'), auth=(NEXTCLOUD_USER, NEXTCLOUD_PASS), verify=False)

    try:
        resp = await run_blocking(_write)
        if resp.status_code in [200, 201, 204]:
            return {"status": "success", "msg": f"Note '{safe_title}' created.", "filename": filename}
        return {"status": "error", "msg": f"WebDAV Error: {resp.status_code}"}
    except Exception as e:
        return {"status": "error", "msg": str(e)}

async def append_note(title: str, content: str) -> dict:
    """Appends content to an existing note (or creates if missing)."""
    # 1. Try to read existing
    current_content = await read_note(title)
    
    if "not found" in current_content.get("msg", "").lower():
        # Create new
        return await create_note(title, content)
    
    if current_content.get("status") == "error":
        return current_content

    # 2. Append
    existing_text = current_content.get("content", "")
    new_text = f"{existing_text}\n\n- {content}" # Append as list item by default for shopping lists etc

    # 3. Write back
    safe_title = "".join([c for c in title if c.isalnum() or c in " -_"]).strip()
    filename = f"{safe_title}.md"
    url = _get_webdav_url(filename)

    def _write():
        return requests.put(url, data=new_text.encode('utf-8'), auth=(NEXTCLOUD_USER, NEXTCLOUD_PASS), verify=False)

    try:
        resp = await run_blocking(_write)
        if resp.status_code in [200, 201, 204]:
            return {"status": "success", "msg": f"Appended to '{safe_title}'."}
        return {"status": "error", "msg": f"WebDAV Error: {resp.status_code}"}
    except Exception as e:
        return {"status": "error", "msg": str(e)}

async def check_off_item(title: str, item_name: str) -> dict:
    """Marks an item as completed in a note list (e.g., [ ] -> [x])."""
    # 1. Read Note
    res = await read_note(title)
    if res.get("status") != "success":
        return res
    
    content = res.get("content", "")
    lines = content.split('\n')
    updated_lines = []
    found = False
    
    # 2. Find and Modify
    for line in lines:
        if item_name.lower() in line.lower() and not line.strip().startswith("[x]"):
            # Supports standard bullet points or checkboxes
            if line.strip().startswith("- [ ]"):
                updated_lines.append(line.replace("- [ ]", "- [x]", 1))
            elif line.strip().startswith("- "):
                updated_lines.append(line.replace("- ", "- [x] ", 1))
            elif line.strip().startswith("* "):
                updated_lines.append(line.replace("* ", "* [x] ", 1))
            else:
                # Just prepend [x]
                updated_lines.append(f"[x] {line}")
            found = True
        else:
            updated_lines.append(line)
            
    if not found:
        return {"status": "error", "msg": f"Item '{item_name}' not found or already checked."}
        
    # 3. Write Back
    new_content = "\n".join(updated_lines)
    return await update_note(title, new_content)

async def update_note(title: str, content: str) -> dict:
    """Overwrites an existing note with new raw content."""
    if not (NEXTCLOUD_URL and NEXTCLOUD_USER and NEXTCLOUD_PASS):
        return {"status": "error", "msg": "Nextcloud credentials missing"}

    safe_title = "".join([c for c in title if c.isalnum() or c in " -_"]).strip()
    filename = f"{safe_title}.md"
    url = _get_webdav_url(filename)

    def _write():
        return requests.put(url, data=content.encode('utf-8'), auth=(NEXTCLOUD_USER, NEXTCLOUD_PASS), verify=False)

    try:
        resp = await run_blocking(_write)
        if resp.status_code in [200, 201, 204]:
            return {"status": "success", "msg": f"Note '{safe_title}' updated."}
        return {"status": "error", "msg": f"WebDAV Error: {resp.status_code}"}
    except Exception as e:
        return {"status": "error", "msg": str(e)}

async def read_note(title: str) -> dict:
    """Reads a note's content directly from WebDAV."""
    if not (NEXTCLOUD_URL and NEXTCLOUD_USER and NEXTCLOUD_PASS):
        return {"status": "error", "msg": "Nextcloud credentials missing"}

    safe_title = "".join([c for c in title if c.isalnum() or c in " -_"]).strip()
    filename = f"{safe_title}.md"
    url = _get_webdav_url(filename)

    def _read():
        return requests.get(url, auth=(NEXTCLOUD_USER, NEXTCLOUD_PASS), verify=False)

    try:
        resp = await run_blocking(_read)
        if resp.status_code == 200:
            return {"status": "success", "content": resp.text, "filename": filename}
        elif resp.status_code == 404:
            return {"status": "error", "msg": f"Note '{safe_title}' not found."}
        return {"status": "error", "msg": f"WebDAV Error: {resp.status_code}"}
    except Exception as e:
        return {"status": "error", "msg": str(e)}

async def delete_note(title: str) -> dict:
    """Deletes a note/file from Nextcloud."""
    if not (NEXTCLOUD_URL and NEXTCLOUD_USER and NEXTCLOUD_PASS):
        return {"status": "error", "msg": "Nextcloud credentials missing"}

    safe_title = "".join([c for c in title if c.isalnum() or c in " -_"]).strip()
    filename = f"{safe_title}.md"
    url = _get_webdav_url(filename)

    def _delete():
        return requests.delete(url, auth=(NEXTCLOUD_USER, NEXTCLOUD_PASS), verify=False)

    try:
        resp = await run_blocking(_delete)
        if resp.status_code in [200, 204]:
            return {"status": "success", "msg": f"Note '{safe_title}' deleted."}
        elif resp.status_code == 404:
            return {"status": "error", "msg": f"Note '{safe_title}' not found (already deleted?)."}
        return {"status": "error", "msg": f"WebDAV Error: {resp.status_code}"}
    except Exception as e:
        return {"status": "error", "msg": str(e)}

# --- Intent Engine Logic ---

async def tool_note_add(title: str, content: str, category: str = "General"):
    """
    Creates a new note/file in Nextcloud.
    """
    return await create_note(title, content, category)

async def tool_note_append(title: str, content: str):
    """
    Appends text to a note. Useful for lists.
    """
    return await append_note(title, content)

async def tool_note_update(title: str, content: str):
    """
    Overwrites a note with new content.
    """
    return await update_note(title, content)

async def tool_note_check_off(title: str, item: str):
    """
    Marks an item as done in a note/list.
    """
    res = await check_off_item(title, item)
    return res.get("msg") or res

async def tool_note_read(title: str):
    """
    Reads a note from Nextcloud.
    """
    res = await read_note(title)
    if res.get("status") == "success":
        return f"Note Content ({title}):\n{res['content']}"
    return res.get("msg")

async def tool_note_delete(title: str):
    """
    Deletes a note/file from Nextcloud.
    """
    res = await delete_note(title)
    return res.get("msg")



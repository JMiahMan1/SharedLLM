# services/execution/handlers/note.py
import logging
import requests

try:
    from ..schemas import NoteRequest, ExecutionResult
    from ..personal_data import resolve_personal_data_provider
except ImportError:
    from schemas import NoteRequest, ExecutionResult
    from personal_data import resolve_personal_data_provider

log = logging.getLogger("execution.note")

NOTES_DIR = "Notes"

async def handle_note(req: NoteRequest) -> ExecutionResult:
    provider = resolve_personal_data_provider(req.user_context)
    if not provider:
        return ExecutionResult(status="FAILURE", message="Nextcloud credentials missing.", service="note")

    provider.ensure_directory(NOTES_DIR)
    
    action = req.action
    file_title = provider.sanitize_filename(req.title, "note")
    filename = f"{file_title}.md"
    url = provider.file_url(f"{NOTES_DIR}/{filename}")
    
    try:
        if action == "create":
            content = f"# {req.title}\nCategory: {req.category}\n\n{req.content or ''}"
            resp = requests.put(url, data=content.encode('utf-8'), auth=(provider.username, provider.password), verify=False)
            if resp.status_code in [200, 201, 204]:
                return ExecutionResult(status="SUCCESS", message=f"Note '{req.title}' created.", service="note_create")
            
        elif action == "read":
            resp = requests.get(url, auth=(provider.username, provider.password), verify=False)
            if resp.status_code == 200:
                return ExecutionResult(status="SUCCESS", message=resp.text, service="note_read")
            elif resp.status_code == 404:
                return ExecutionResult(status="FAILURE", message=f"Note '{req.title}' not found.", service="note_read")
        
        elif action == "append":
            # Read first
            r_resp = requests.get(url, auth=(provider.username, provider.password), verify=False)
            existing = r_resp.text if r_resp.status_code == 200 else ""
            new_content = f"{existing}\n\n- [ ] {req.content}"
            resp = requests.put(url, data=new_content.encode('utf-8'), auth=(provider.username, provider.password), verify=False)
            if resp.status_code in [200, 201, 204]:
                return ExecutionResult(status="SUCCESS", message=f"Appended to '{req.title}'.", service="note_append")

        elif action == "delete":
            resp = requests.delete(url, auth=(provider.username, provider.password), verify=False)
            if resp.status_code in [200, 204]:
                return ExecutionResult(status="SUCCESS", message=f"Note '{req.title}' deleted.", service="note_delete")

        return ExecutionResult(status="FAILURE", message=f"Action {action} failed or not implemented.", service="note")

    except Exception as e:
        log.error(f"Note error: {e}")
        return ExecutionResult(status="FAILURE", message=f"Note error: {str(e)}", service="note")

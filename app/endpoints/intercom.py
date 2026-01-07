from fastapi import APIRouter, UploadFile, File, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
import shutil
import os
import time
from pathlib import Path
from typing import Optional

from app.settings import log, GlobalResources
from app.domains.announcements.logic import process_announcement

router = APIRouter()

# Configuration
UPLOAD_DIR = Path("/app/static/temp/audio")
MAX_FILE_AGE = 3600  # 1 hour in seconds

def cleanup_old_files():
    """Delete files older than MAX_FILE_AGE."""
    try:
        current_time = time.time()
        for f in UPLOAD_DIR.iterdir():
            if f.is_file() and (current_time - f.stat().st_mtime) > MAX_FILE_AGE:
                log.info(f"[INTERCOM] Cleaning up old file: {f.name}")
                f.unlink()
    except Exception as e:
        log.error(f"[INTERCOM] Cleanup failed: {e}")

@router.post("/api/intercom/upload")
async def upload_intercom_audio(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...), 
    target: Optional[str] = None,
    message: Optional[str] = "Voice Message"
):
    """
    Uploads an audio file and triggers an announcement.
    """
    try:
        # Ensure upload dir exists
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        
        # Save file
        # Use simple timestamped filename to avoid collisions
        filename = f"intercom_{int(time.time())}_{file.filename}"
        file_path = UPLOAD_DIR / filename
        
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
            
        log.info(f"[INTERCOM] Saved audio to {file_path}")
        
        # Construct URL (assuming static mount at /static)
        # We need the external URL or relative path that Home Assistant can access.
        # Often HA and this middleware are on same network. 
        # Using relative path for process_announcement to resolve or absolute if HA requires.
        # logic.py usually expects a path it can send to HA, causing HA to play it.
        # HA needs a URL reachable from its Media Player integration.
        # If this app is hosted at http://MIDDLEWARE_IP:PORT, then url is http://.../static/temp/audio/...
        
        # For now, pass the relative local path or a constructable valid URL/path string.
        # Let's pass a special indicator or just the mapped path.
        # If we use a local path "/app/static...", HA might not act access it unless it's mapped in HA.
        # BETTER: Use valid http URL.
        # We'll use a placeholder base URL if not in settings, or assume relative to "/static"
        
        # We will update logic.py to handle "local" paths if needed, or better, 
        # assume the logic can handle a URL.
        # We'll construct a relative URL.
        audio_url = f"/static/temp/audio/{filename}"
        
        # Trigger Announcement
        # We use a user_creds 'admin' fallback if not passed (auth is handled by main:app dep usually)
        # For this endpoint, we assume internal or authorized use.
        # We need user creds to call HA service.
        from app.settings import get_user_creds
        user_creds = get_user_creds("admin") 
        
        # Trigger processing in background
        background_tasks.add_task(process_announcement, message, target, user_creds, audio_url=audio_url)
        
        # Schedule cleanup
        background_tasks.add_task(cleanup_old_files)
        
        return {"status": "SUCCESS", "message": "Intercom message queued.", "url": audio_url}
        
    except Exception as e:
        log.error(f"[INTERCOM] Upload failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

"""
Video cache management for Cast devices.

Handles progressive downloading of videos and cleanup of old files.
"""
import os
import time
import asyncio
import hashlib
from pathlib import Path
from typing import Optional
import logging

log = logging.getLogger(__name__)

# Cache directory
CACHE_DIR = Path("/workspace/temp/cast_videos")
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Configuration
INITIAL_BUFFER_MB = 1  # Wait for 1MB before returning URL
MAX_FILE_AGE_HOURS = 1  # Delete files older than 1 hour


def get_video_id(url: str) -> str:
    """Generate a unique video ID from URL."""
    return hashlib.md5(url.encode()).hexdigest()[:12]


def get_video_path(video_id: str) -> Path:
    """Get the file path for a video ID."""
    return CACHE_DIR / f"{video_id}.mp4"


async def download_video_progressive(url: str, video_id: str) -> tuple[Path, bool]:
    """
    Download video progressively using yt-dlp.
    
    Returns:
        (file_path, ready): file_path is the local path, ready indicates if initial buffer is ready
    """
    try:
        import yt_dlp
    except ImportError:
        log.error("yt-dlp not installed")
        return None, False
    
    file_path = get_video_path(video_id)
    
    # First, check if it's a livestream (don't download those - they're huge!)
    ydl_info_opts = {
        'quiet': True,
        'no_warnings': True,
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_info_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if info.get('is_live') or info.get('was_live'):
                log.warning(f"[VideoCache] Skipping livestream: {url}")
                return None, False
    except Exception as e:
        log.warning(f"[VideoCache] Failed to check if livestream: {e}")
        return None, False
    
    # yt-dlp options for progressive download
    ydl_opts = {
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'outtmpl': str(file_path),
        'quiet': True,
        'no_warnings': True,
        'extract_flat': False,
    }
    
    # Start download in asyncio executor
    loop = asyncio.get_event_loop()
    
    async def run_download():
        def _download():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
        
        await loop.run_in_executor(None, _download)
    
    # Start download task in background
    download_task = asyncio.create_task(run_download())
    
    # Wait for initial buffer
    buffer_bytes = INITIAL_BUFFER_MB * 1024 * 1024
    max_wait_seconds = 30
    start_time = time.time()
    
    while time.time() - start_time < max_wait_seconds:
        if file_path.exists():
            size = file_path.stat().st_size
            if size >= buffer_bytes:
                log.info(f"[VideoCache] Initial buffer ready: {size / 1024 / 1024:.1f}MB")
                return file_path, True
        
        await asyncio.sleep(0.5)
    
    # If we got here, either file is downloading slowly or failed
    if file_path.exists() and file_path.stat().st_size > 0:
        log.warning(f"[VideoCache] Buffer not ready but file exists, proceeding anyway")
        return file_path, True
    
    log.error(f"[VideoCache] Download failed or too slow for {url}")
    return None, False


def cleanup_old_videos(max_age_hours: float = MAX_FILE_AGE_HOURS):
    """Remove video files older than max_age_hours."""
    try:
        now = time.time()
        max_age_seconds = max_age_hours * 3600
        
        deleted = 0
        for file_path in CACHE_DIR.glob("*.mp4"):
            age_seconds = now - file_path.stat().st_mtime
            if age_seconds > max_age_seconds:
                log.info(f"[VideoCache] Deleting old file: {file_path.name} (age: {age_seconds/3600:.1f}h)")
                file_path.unlink()
                deleted += 1
        
        if deleted > 0:
            log.info(f"[VideoCache] Cleaned up {deleted} old video files")
    except Exception as e:
        log.error(f"[VideoCache] Cleanup error: {e}")


async def schedule_periodic_cleanup(interval_minutes: int = 30):
    """Run cleanup periodically in the background."""
    while True:
        await asyncio.sleep(interval_minutes * 60)
        cleanup_old_videos()

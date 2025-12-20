"""
FastAPI endpoint for serving cached video files to Cast devices.
"""
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pathlib import Path
import logging

log = logging.getLogger(__name__)

router = APIRouter()

CACHE_DIR = Path("/workspace/temp/cast_videos")


@router.api_route("/cast_video/{filename}", methods=["GET", "HEAD"])
async def stream_video(filename: str, request: Request):
    """
    Serve video file with HTTP Range support for progressive streaming.
    
    Cast devices use Range requests to stream video progressively.
    """
    # Security: Only allow alphanumeric + dash + underscore + .mp4
    if not filename.replace('.', '').replace('_', '').replace('-', '').isalnum():
        raise HTTPException(status_code=400, detail="Invalid filename")
    
    if not filename.endswith('.mp4'):
        raise HTTPException(status_code=400, detail="Only MP4 files allowed")
    
    file_path = CACHE_DIR / filename
    
    if not file_path.exists():
        log.error(f"[CastVideo] File not found: {filename}")
        raise HTTPException(status_code=404, detail="Video not found")
    
    log.info(f"[CastVideo] Serving {filename} (size: {file_path.stat().st_size / 1024 / 1024:.1f}MB)")
    
    # FileResponse automatically handles Range requests
    return FileResponse(
        path=str(file_path),
        media_type="video/mp4",
        filename=filename,
        headers={
            "Accept-Ranges": "bytes",
            "Cache-Control": "no-cache",
            "contentFeatures.dlna.org": "DLNA.ORG_OP=01;DLNA.ORG_CI=0;DLNA.ORG_FLAGS=01700000000000000000000000000000",
            "transferMode.dlna.org": "Streaming",
            "realTimeInfo.dlna.org": "DLNA.ORG_TLAG=*"
        }
    )

from fastapi import APIRouter, HTTPException, Request, Body
from typing import Optional
from pydantic import BaseModel

from app.logic import music_assistant_ops
from app.settings import get_user_creds

router = APIRouter(
    prefix="/api/ma",
    tags=["Music Assistant"]
)

# --- Request Models ---
class PlayRequest(BaseModel):
    entity_id: str
    media_id: str
    media_type: str = "music"

class ControlRequest(BaseModel):
    entity_id: str
    command: str  # play, pause, stop, next, previous

# --- Endpoints ---

@router.get("/search")
async def search_library(q: str):
    """Search for tracks, artists, albums."""
    # Using dummy creds logic for now or extracting from request if auth middleware existed
    # For now, we reuse the pattern of existing tools which pull global/env creds via helpers if needed
    # But get_user_creds() defaults to environment variables if no user provided.
    creds = get_user_creds()
    result = await music_assistant_ops.tool_music_search(q, creds)
    if result["status"] == "FAILURE":
        raise HTTPException(status_code=404, detail=result["message"])
    return result

@router.get("/playlists")
async def list_playlists():
    """List all playlists."""
    creds = get_user_creds()
    result = await music_assistant_ops.tool_list_playlists("", creds)
    if result["status"] == "FAILURE":
        raise HTTPException(status_code=500, detail=result["message"])
    return result

@router.post("/play")
async def play_media(req: PlayRequest):
    """Play a specific item on a device."""
    creds = get_user_creds()
    result = await music_assistant_ops.play_media(req.entity_id, req.media_id, req.media_type, creds)
    if result["status"] == "FAILURE":
        raise HTTPException(status_code=500, detail=result["message"])
    return result

@router.post("/control")
async def control_player(req: ControlRequest):
    """Control media player transport."""
    creds = get_user_creds()
    if req.command not in ["play", "pause", "stop", "next", "previous"]:
        raise HTTPException(status_code=400, detail=f"Invalid command: {req.command}")
        
    result = await music_assistant_ops.control_player(req.entity_id, req.command, creds)
    if result["status"] == "FAILURE":
        raise HTTPException(status_code=500, detail=result["message"])
    return result

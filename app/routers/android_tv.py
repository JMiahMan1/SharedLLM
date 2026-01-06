from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.logic import android_tv_ops
from app.settings import get_user_creds

router = APIRouter(
    prefix="/api/androidtv",
    tags=["Android TV"]
)

class LaunchRequest(BaseModel):
    entity_id: str
    app_name: str

class PlayRequest(BaseModel):
    entity_id: str
    video_url: str

class SearchPlayRequest(BaseModel):
    entity_id: str
    query: str

class ControlRequest(BaseModel):
    entity_id: str
    command: str

@router.post("/launch")
async def launch_app(req: LaunchRequest):
    creds = get_user_creds()
    result = await android_tv_ops.launch_app(req.entity_id, req.app_name, creds)
    if result["status"] == "FAILURE":
         raise HTTPException(status_code=500, detail=result["message"])
    return result

@router.post("/play")
async def play_video(req: PlayRequest):
    creds = get_user_creds()
    result = await android_tv_ops.play_video(req.entity_id, req.video_url, creds)
    if result["status"] == "FAILURE":
         raise HTTPException(status_code=500, detail=result["message"])
    return result

@router.post("/search_play")
async def search_and_play(req: SearchPlayRequest):
    creds = get_user_creds()
    result = await android_tv_ops.search_and_play(req.entity_id, req.query, creds)
    if result["status"] == "FAILURE":
         raise HTTPException(status_code=500, detail=result["message"])
    return result

@router.post("/control")
async def control_device(req: ControlRequest):
    creds = get_user_creds()
    result = await android_tv_ops.control_device(req.entity_id, req.command, creds)
    if result["status"] == "FAILURE":
         raise HTTPException(status_code=500, detail=result["message"])
    return result

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.logic import webos_ops
from app.settings import get_user_creds

router = APIRouter(
    prefix="/api/webos",
    tags=["WebOS"]
)

class LaunchRequest(BaseModel):
    entity_id: str
    app_name: str

class NotifyRequest(BaseModel):
    entity_id: str
    message: str
    icon: str = None

class ChannelRequest(BaseModel):
    entity_id: str
    channel: str

class ControlRequest(BaseModel):
    entity_id: str
    command: str

@router.post("/launch")
async def launch_app(req: LaunchRequest):
    creds = get_user_creds()
    result = await webos_ops.launch_app(req.entity_id, req.app_name, creds)
    if result["status"] == "FAILURE":
         raise HTTPException(status_code=500, detail=result["message"])
    return result

@router.post("/notify")
async def send_notification(req: NotifyRequest):
    creds = get_user_creds()
    result = await webos_ops.send_notification(req.entity_id, req.message, creds, icon=req.icon)
    if result["status"] == "FAILURE":
         raise HTTPException(status_code=500, detail=result["message"])
    return result

@router.post("/channel")
async def play_channel(req: ChannelRequest):
    creds = get_user_creds()
    result = await webos_ops.play_channel(req.entity_id, req.channel, creds)
    if result["status"] == "FAILURE":
         raise HTTPException(status_code=500, detail=result["message"])
    return result

@router.post("/control")
async def control_device(req: ControlRequest):
    creds = get_user_creds()
    result = await webos_ops.control_device(req.entity_id, req.command, creds)
    if result["status"] == "FAILURE":
         raise HTTPException(status_code=500, detail=result["message"])
    return result

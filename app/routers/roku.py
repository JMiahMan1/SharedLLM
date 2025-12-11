from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from settings import GlobalResources
from logic import roku_ops

router = APIRouter(prefix="/api/roku", tags=["roku"])

class LaunchRequest(BaseModel):
    entity_id: str
    app_name: str

class ChannelRequest(BaseModel):
    entity_id: str
    channel: str

class PlayURLRequest(BaseModel):
    entity_id: str
    url: str
    format: Optional[str] = "mp4"
    name: Optional[str] = None
    thumbnail: Optional[str] = None

class DeepLinkRequest(BaseModel):
    entity_id: str
    app_id: str
    content_id: str
    media_type: str = "movie"

class ButtonRequest(BaseModel):
    entity_id: str
    button: str

class SearchRequest(BaseModel):
    entity_id: str
    keyword: str

@router.post("/launch")
async def launch_app(req: LaunchRequest):
    """Launch a Roku app by name or ID"""
    try:
        user_creds = {"ha_token": GlobalResources.ha_token}
        result = await roku_ops.launch_app(
            req.entity_id,
            req.app_name,
            user_creds,
            GlobalResources.redis_client
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/channel")
async def tune_channel(req: ChannelRequest):
    """Tune to a TV channel (Roku TV with antenna only)"""
    try:
        user_creds = {"ha_token": GlobalResources.ha_token}
        result = await roku_ops.play_channel(
            req.entity_id,
            req.channel,
            user_creds,
            GlobalResources.redis_client
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/play_url")
async def play_url(req: PlayURLRequest):
    """Play a direct media URL"""
    try:
        user_creds = {"ha_token": GlobalResources.ha_token}
        result = await roku_ops.play_media_url(
            req.entity_id,
            req.url,
            user_creds,
            GlobalResources.redis_client,
            req.format,
            req.name,
            req.thumbnail
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/deeplink")
async def deeplink_content(req: DeepLinkRequest):
    """Deep-link to specific content within an app"""
    try:
        user_creds = {"ha_token": GlobalResources.ha_token}
        result = await roku_ops.deep_link(
            req.entity_id,
            req.app_id,
            req.content_id,
            req.media_type,
            user_creds,
            GlobalResources.redis_client
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/button")
async def send_button(req: ButtonRequest):
    """Send a remote button command"""
    try:
        user_creds = {"ha_token": GlobalResources.ha_token}
        result = await roku_ops.send_button(
            req.entity_id,
            req.button,
            user_creds,
            GlobalResources.redis_client
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/search")
async def search_content(req: SearchRequest):
    """Search for content on Roku"""
    try:
        user_creds = {"ha_token": GlobalResources.ha_token}
        result = await roku_ops.search(
            req.entity_id,
            req.keyword,
            user_creds,
            GlobalResources.redis_client
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

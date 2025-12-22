from fastapi import APIRouter, Depends, BackgroundTasks
from pydantic import BaseModel
from typing import Dict, Any, Optional
import logging

from app.settings import GlobalResources
from app.routers.auth import get_current_user_creds

log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/music",
    tags=["MusicAssistant"]
)

class SyncRequest(BaseModel):
    user: str = "admin"

@router.post("/sync")
async def sync_library(
    request: SyncRequest,
    background_tasks: BackgroundTasks,
    user_creds: Dict[str, Any] = Depends(get_current_user_creds)
):
    """
    Trigger background sync of Music Assistant library to Redis cache.
    """
    from app.logic import music_assistant_ops
    
    # Run in background to avoid blocking response
    background_tasks.add_task(
        music_assistant_ops.sync_library_to_redis,
        user_creds,
        GlobalResources.redis_client
    )
    
    return {"status": "SUCCESS", "message": "Library sync started in background"}

@router.get("/stats")
async def get_cache_stats(
    user_creds: Dict[str, Any] = Depends(get_current_user_creds)
):
    """
    Get stats about cached music items.
    """
    stats = {}
    total = 0
    
    for mtype in ["artist", "album", "track", "playlist", "radio"]:
        key = f"ma_cache:{mtype}"
        redis = GlobalResources.redis_client
        count = redis.llen(key) if redis and redis.exists(key) else 0
        stats[mtype] = count
        total += count
        
    last_update = GlobalResources.redis_client.get("ma_cache:updated_at") if GlobalResources.redis_client else None
    
    return {
        "status": "SUCCESS", 
        "stats": stats, 
        "total_items": total,
        "last_updated": last_update.decode() if last_update else "Never"
    }

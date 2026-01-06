from fastapi import APIRouter
from app.settings import GlobalResources, log

router = APIRouter(prefix="/api/context", tags=["context"])

@router.post("/clear")
async def clear_context(body: dict):
    """
    Clears the conversation context for a specific user from Redis.
    Body: {"user": "username"}
    """
    user = body.get("user", "admin")
    if not GlobalResources.redis_client:
        return {"status": "error", "msg": "Redis client not initialized"}
        
    try:
        # Clear specific context keys
        keys_to_delete = [
            f"rag:last_media_entity:{user}",
            f"rag:last_entity:{user}",
            f"rag:conversation:{user}"
        ]
        
        GlobalResources.redis_client.delete(*keys_to_delete)
        log.info(f"Context cleared for user: {user}")
        return {"status": "ok", "msg": f"Context cleared for {user}"}
    except Exception as e:
        log.error(f"Error clearing context for {user}: {e}")
        return {"status": "error", "msg": str(e)}


async def play_url(entity_id: str, url: str, user_creds: dict, redis_client=None) -> dict:
    """
    Opens a URL directly on the TV (useful for YouTube videos).
    Uses the webostv.command service with system.launcher/open.
    """
    from logic.media_ops import execute_ha_service
    
    log.info(f"[WebOS] Opening URL {url} on {entity_id}")
    
    return await execute_ha_service(
        "webostv", "command", entity_id, user_creds,
        {"command": "system.launcher/open", "payload": {"target": url}},
        redis_client
    )

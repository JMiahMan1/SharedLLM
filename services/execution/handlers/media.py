# services/execution/handlers/media.py
import logging
import asyncio
try:
    from .. import ha_client
    from ..schemas import MediaPlayRequest, MediaTransportRequest, TVCastRequest, ExecutionResult
except ImportError:
    import ha_client
    from schemas import MediaPlayRequest, MediaTransportRequest, TVCastRequest, ExecutionResult

log = logging.getLogger("execution.media")

async def handle_media_play(req: MediaPlayRequest) -> ExecutionResult:
    ctx = req.user_context
    log.info(f"[media/play] user={ctx.user} entity={req.entity_id}")

    service_data: dict = {}
    if req.media_content_id:
        service_data["media_content_id"] = req.media_content_id
        service_data["media_content_type"] = req.media_content_type
    if req.enqueue:
        service_data["enqueue"] = req.enqueue

    result = await ha_client.call_service(
        ctx.ha_url, ctx.ha_token,
        "media_player", "play_media",
        req.entity_id, service_data or None,
    )
    
    if result.get("ok"):
        return ExecutionResult(status="SUCCESS", message="Playback started.", service="media_play")
    return ExecutionResult(status="FAILURE", message=f"Playback failed: {result.get('error')}", service="media_play", detail=result)

async def handle_media_transport(req: MediaTransportRequest) -> ExecutionResult:
    ctx = req.user_context
    ha_service_map = {
        "pause": "media_pause",
        "resume": "media_play",
        "stop": "media_stop",
        "next": "media_next_track",
        "previous": "media_previous_track",
        "volume_up": "volume_up",
        "volume_down": "volume_down",
    }
    service = ha_service_map.get(req.command, req.command)
    
    data = {}
    if req.command in ("volume_up", "volume_down") and req.volume_level is not None:
        service = "volume_set"
        data = {"volume_level": req.volume_level}

    result = await ha_client.call_service(
        ctx.ha_url, ctx.ha_token,
        "media_player", service,
        req.entity_id, data or None,
    )
    
    if result.get("ok"):
        return ExecutionResult(status="SUCCESS", message=f"Media command '{service}' executed.", service="media_transport")
    return ExecutionResult(status="FAILURE", message=f"Media command failed: {result.get('error')}", service="media_transport", detail=result)

async def handle_tv_cast(req: TVCastRequest) -> ExecutionResult:
    ctx = req.user_context
    # Power on logic
    state = await ha_client.get_state(ctx.ha_url, ctx.ha_token, req.media_player_entity_id)
    if state and state.get("state") not in ("on", "playing", "paused", "idle"):
        await ha_client.call_service(ctx.ha_url, ctx.ha_token, "media_player", "turn_on", req.media_player_entity_id)
        await asyncio.sleep(req.power_on_wait_ms / 1000)

    result = await ha_client.call_service(
        ctx.ha_url, ctx.ha_token,
        "media_player", "play_media",
        req.media_player_entity_id,
        {"media_content_id": req.media_content_id, "media_content_type": req.media_content_type},
    )
    
    if result.get("ok"):
        return ExecutionResult(status="SUCCESS", message="TV cast started.", service="tv_cast")
    return ExecutionResult(status="FAILURE", message=f"TV cast failed: {result.get('error')}", service="tv_cast", detail=result)

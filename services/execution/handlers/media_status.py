# services/execution/handlers/media_status.py
import logging

try:
    import ha_client
    from schemas import ExecutionResult, MediaStatusRequest
except ImportError:
    from .. import ha_client
    from ..schemas import ExecutionResult, MediaStatusRequest

log = logging.getLogger("execution.media_status")

async def handle_media_status(req: MediaStatusRequest) -> ExecutionResult:
    ctx = req.user_context
    assert ctx.ha_url is not None and ctx.ha_token is not None

    all_states = await ha_client.get_states(ctx.ha_url, ctx.ha_token)
    if not all_states:
        return ExecutionResult(status="FAILURE", message="Could not retrieve HA states.", service="media_status")

    active_players = []
    available_players = []

    for state in all_states:
        entity_id = state.get("entity_id", "")
        if not entity_id.startswith("media_player."):
            continue

        st = state.get("state", "")
        attrs = state.get("attributes", {})
        friendly_name = attrs.get("friendly_name", entity_id)
        volume_level = attrs.get("volume_level")
        is_volume_muted = attrs.get("is_volume_muted", False)
        media_title = attrs.get("media_title", "")
        media_artist = attrs.get("media_artist", "")
        media_album = attrs.get("media_album_name", "")
        source = attrs.get("source", "")
        media_position = attrs.get("media_position")
        media_duration = attrs.get("media_duration")
        entity_picture = attrs.get("entity_picture")

        # Only include MA-compatible devices (those with MA queue integration)
        # Check integration source and queue presence for MA compatibility
        integration = attrs.get("integration", "")
        active_queue = attrs.get("active_queue")
        is_ma_compatible = (
            integration == "music_assistant"
            or "music assistant" in source.lower()
            or active_queue is not None
        )

        if not is_ma_compatible:
            continue

        player = {
            "entity_id": entity_id,
            "friendly_name": friendly_name,
            "state": st,
            "media_title": media_title,
            "media_artist": media_artist,
            "media_album": media_album,
            "source": source,
            "volume_level": round(volume_level, 2) if volume_level is not None else None,
            "is_volume_muted": is_volume_muted,
            "position": media_position,
            "duration": media_duration,
            "entity_picture": entity_picture,
        }

        if st in ("playing", "paused", "buffering"):
            active_players.append(player)

        # Also collect idle/standby/off players for device selection
        if st in ("idle", "standby", "off"):
            available_players.append(player)

    # Filter by area if requested
    if req.area:
        assert ctx.ha_url is not None and ctx.ha_token is not None
        area_map = await ha_client.get_areas(ctx.ha_url, ctx.ha_token)
        area_lower = req.area.lower()
        active_players = [
            mp for mp in active_players
            if area_lower in area_map.get(mp["entity_id"], "").lower()
        ]
        available_players = [
            mp for mp in available_players
            if area_lower in area_map.get(mp["entity_id"], "").lower()
        ]

    # Filter by specific entity if requested
    if req.entity_id:
        active_players = [mp for mp in active_players if req.entity_id.lower() in mp["entity_id"].lower()]
        available_players = [mp for mp in available_players if req.entity_id.lower() in mp["entity_id"].lower()]

    # Return active player as the main result (for UI player header)
    # Return all players as additional data (for device selector)
    result = {
        "active": active_players[0] if active_players else None,
        "available": available_players,
        "all_players": active_players + available_players,
    }

    # Also return the formatted message for backwards compatibility
    lines = ["**Currently Playing:**\n"]
    for mp in active_players:
        vol_str = f" | Vol: {round(mp['volume_level'] * 100) if mp['volume_level'] is not None else 'N/A'}%" if mp['volume_level'] is not None else ""
        lines.append(f"- **{mp['friendly_name']}**: {mp['media_title'] or mp['source'] or mp['state']}{vol_str}")

    return ExecutionResult(
        status="SUCCESS",
        message="\n".join(lines) if active_players else "No media players are currently active.",
        service="media_status",
        detail=result,
    )

# services/execution/handlers/media_status.py
import logging
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
try:
    import ha_client
    from schemas import MediaStatusRequest, ExecutionResult
except ImportError:
    import ha_client
    from schemas import MediaStatusRequest, ExecutionResult

log = logging.getLogger("execution.media_status")

async def handle_media_status(req: MediaStatusRequest) -> ExecutionResult:
    ctx = req.user_context
    
    all_states = await ha_client.get_states(ctx.ha_url, ctx.ha_token)
    if not all_states:
        return ExecutionResult(status="FAILURE", message="Could not retrieve HA states.", service="media_status")
    
    # Filter to media_player entities that are playing/paused/idle (not off/unavailable)
    media_players = []
    for state in all_states:
        entity_id = state.get("entity_id", "")
        if not entity_id.startswith("media_player."):
            continue
        
        st = state.get("state", "")
        if st in ("off", "unavailable", "unknown", "idle"):
            continue
        
        attrs = state.get("attributes", {})
        friendly_name = attrs.get("friendly_name", entity_id)
        media_title = attrs.get("media_title", "")
        media_artist = attrs.get("media_artist", "")
        media_album = attrs.get("media_album_name", "")
        source = attrs.get("source", "")
        volume = attrs.get("volume_level")
        
        # Build display info
        now_playing = []
        if media_artist:
            now_playing.append(media_artist)
        if media_title:
            now_playing.append(media_title)
        if media_album:
            now_playing.append(f"({media_album})")
        
        detail = " - ".join(now_playing) if now_playing else source or st
        
        media_players.append({
            "name": friendly_name,
            "entity_id": entity_id,
            "state": st,
            "now_playing": detail,
            "volume": round(volume * 100) if volume is not None else None,
        })
    
    # Filter by area if requested
    if req.area:
        area_map = await ha_client.get_areas(ctx.ha_url, ctx.ha_token)
        area_lower = req.area.lower()
        filtered = []
        for mp in media_players:
            entity_area = area_map.get(mp["entity_id"], "")
            if area_lower in entity_area.lower():
                mp["area"] = entity_area
                filtered.append(mp)
        media_players = filtered
    
    # Filter by specific entity if requested
    if req.entity_id:
        media_players = [mp for mp in media_players if req.entity_id.lower() in mp["entity_id"].lower()]
    
    if not media_players:
        return ExecutionResult(status="SUCCESS", message="No media players are currently active.", service="media_status")
    
    # Format as a readable table
    lines = ["**Currently Playing:**\n"]
    for mp in media_players:
        vol_str = f" | Vol: {mp['volume']}%" if mp['volume'] is not None else ""
        area_str = f" ({mp['area']})" if mp.get("area") else ""
        lines.append(f"- **{mp['name']}**{area_str}: {mp['now_playing']}{vol_str}")
    
    return ExecutionResult(status="SUCCESS", message="\n".join(lines), service="media_status")

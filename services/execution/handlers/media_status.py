# services/execution/handlers/media_status.py
import logging

try:
    import ha_client
    from schemas import ExecutionResult, MediaStatusRequest
except ImportError:
    from .. import ha_client
    from ..schemas import ExecutionResult, MediaStatusRequest

from services.shared.ma_player import is_music_assistant_player, ma_player_id

log = logging.getLogger("execution.media_status")

_TV_ICON_KINDS = frozenset({
    "android_tv", "webos", "samsung", "roku", "bravia", "generic_tv",
})

async def handle_media_status(req: MediaStatusRequest) -> ExecutionResult:
    ctx = req.user_context
    assert ctx.ha_url is not None and ctx.ha_token is not None
    from ..announce_handlers import detect_tv_type
    from ..capability_index import CapabilityIndexError, UnknownAreaError, routing_index

    # The hub's capability index says which entities can play media, which drive a screen and
    # which area each is in; a plain HA (no index, logged) keeps the domain and area-name matching.
    try:
        idx = await routing_index(ctx.ha_url, ctx.ha_token, "media status")
        area_id = idx.require_area(req.area) if idx is not None and req.area else None
    except (CapabilityIndexError, UnknownAreaError) as e:
        return ExecutionResult(status="FAILURE", message=f"Could not read the media players: {e}",
                               service="media_status")

    all_states = await ha_client.get_states(ctx.ha_url, ctx.ha_token)
    if not all_states:
        return ExecutionResult(status="FAILURE", message="Could not retrieve HA states.", service="media_status")

    active_players = []
    available_players = []

    for state in all_states:
        entity_id = state.get("entity_id", "")
        if idx is not None:
            if not idx.is_media_target(entity_id):
                continue
            if area_id is not None and idx.area_of(entity_id) != area_id:
                continue
        elif not entity_id.startswith("media_player."):
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
        media_position_updated_at = attrs.get("media_position_updated_at")
        media_content_id = attrs.get("media_content_id")
        shuffle = attrs.get("shuffle")
        repeat = attrs.get("repeat")
        group_members = attrs.get("group_members") or []
        app_name = attrs.get("app_name")

        # Music Assistant players are preferred for the "active" slot, but we
        # must not drop cast/Chrome/HA players that carry real now-playing
        # metadata; otherwise Office TV shows "No Active Playback" while
        # transport still works via entity_id.
        is_ma = is_music_assistant_player(attrs, entity_id)
        has_metadata = bool(media_title or media_artist or media_album or entity_picture)
        is_active_state = st in ("playing", "paused", "buffering")
        is_selectable = st not in ("unavailable", "unknown")
        tv_kind = detect_tv_type(entity_id, st, attrs,
                                 platform=idx.tv_platform(entity_id) if idx is not None else None)
        if idx is not None:
            icon_kind = "tv" if idx.is_screen(entity_id) else "speaker"
        else:
            icon_kind = "tv" if attrs.get("device_class") == "tv" or tv_kind in _TV_ICON_KINDS else "speaker"
        ma_id = ma_player_id(attrs, entity_id) if is_ma else None

        # Keep: MA players, anything actively rendering, anything with now-playing
        # metadata, and live selectable targets. Drop dead non-MA entities only.
        if not (is_ma or is_active_state or has_metadata or is_selectable):
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
            "available": is_selectable,
            "supported_features": attrs.get("supported_features", 0),
            "is_ma": is_ma,
            "media_position_updated_at": media_position_updated_at,
            "media_content_id": media_content_id,
            "shuffle": shuffle,
            "repeat": repeat,
            "group_members": group_members,
            "app_name": app_name,
            "icon_kind": icon_kind,
            "ma_player_id": ma_id,
            "area_id": idx.area_of(entity_id) if idx is not None else None,
        }

        # Anything actively rendering media is "active"; everything else is a
        # selectable target. The two lists together must cover every player:
        # states outside a fixed whitelist (e.g. "on", "unavailable") used to
        # fall through both and vanish from the picker.
        if is_active_state:
            active_players.append(player)
        else:
            available_players.append(player)

    def _active_rank(player: dict) -> tuple[bool, bool, bool]:
        has_metadata = bool(
            player.get("media_title")
            or player.get("media_artist")
            or player.get("media_album")
            or player.get("entity_picture")
        )
        non_ma = not player.get("is_ma")
        preferred_app = non_ma and (has_metadata or bool(player.get("app_name")))
        return (not preferred_app, not has_metadata, not non_ma)

    active_players.sort(key=_active_rank)

    # Filter by area if requested (the index already did, by the hub's area registry)
    if req.area and idx is None:
        log.warning("[media_status] area %r matched by Home Assistant area names (no capability index)", req.area)
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

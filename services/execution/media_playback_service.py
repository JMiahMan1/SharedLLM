# services/execution/media_playback_service.py
"""
Unified MediaPlaybackService for routing media commands (play, transport, status)
to either local clients (browser, mobile) or hardware devices (via HA/MA).
"""
import logging
from typing import Any, Dict
from services.execution.schemas import (
    MediaPlayRequest,
    MediaTransportRequest,
    MediaStatusRequest,
    MediaStateSyncRequest,
    ExecutionResult
)
from services.execution.handlers import media as media_handler
from services.execution.handlers import media_status as media_status_handler
from services.execution import media_playback_registry as registry

log = logging.getLogger("execution.media_playback_service")

class MediaPlaybackService:
    """
    Service to abstract differences between HA/MA and direct ABS API calls,
    routing play/transport intents to the correct target (local or hardware).
    """

    @staticmethod
    async def play(req: MediaPlayRequest) -> ExecutionResult:
        """Route a play intent to the correct provider and target."""
        ctx = req.user_context
        username = ctx.user or "default"
        
        # Determine target
        target = req.entity_id or ""
        is_local = target.lower() in ("local", "local_player", "browser", "android")
        
        log.info(f"[MediaPlaybackService] Play request for user={username}, target={target} (local={is_local}), query={req.query}")
        
        # Detect media type
        media_type = media_handler.detect_media_type(req.query or req.media_content_id or "", req.media_type)
        
        if is_local:
            # For local target, persist the target and details, and return stream url/info
            state_data: Dict[str, Any] = {
                "entity_id": "local",
                "state": "playing",
                "media_type": media_type,
                "query": req.query,
                "media_content_id": req.media_content_id,
                "volume_level": req.volume if req.volume is not None else 0.7,
                "is_volume_muted": False,
                "media_title": req.query or "Local Stream",
                "media_artist": "Local Player",
                "updated_at": registry.get_az_timestamp_str()
            }
            
            # If it's an audiobook, try to fetch meta from ABS for richer UI
            if media_type == "audiobook" and req.query:
                try:
                    from services.execution import abs_client
                    abs_url, abs_key, username_abs, password_abs = abs_client.resolve_abs_credentials(ctx)
                    if abs_url:
                        if not abs_key and (username_abs and password_abs):
                            abs_key = await abs_client.abs_login(abs_url, username_abs, password_abs)
                        if abs_key:
                            search_res = await abs_client.search_library(abs_url, abs_key, req.query, limit=1)
                            if search_res.get("book"):
                                book = search_res["book"][0].get("libraryItem", {})
                                meta = book.get("media", {}).get("metadata", {})
                                state_data["media_content_id"] = book.get("id")
                                state_data["media_title"] = meta.get("title", req.query)
                                state_data["media_artist"] = meta.get("authorName", "Unknown Author")
                                state_data["duration"] = book.get("media", {}).get("duration", 0.0)
                except Exception as e:
                    log.warning(f"[MediaPlaybackService] Failed to enrich ABS metadata: {e}")
            
            await registry.save_playback_state(username, state_data)
            return ExecutionResult(
                status="SUCCESS",
                message=f"Local playback initiated for '{state_data['media_title']}'",
                service="media_play",
                detail={
                    "target": "local",
                    "media_type": media_type,
                    "media_title": state_data["media_title"],
                    "media_artist": state_data["media_artist"],
                    "media_content_id": state_data["media_content_id"],
                    "duration": state_data.get("duration", 0.0)
                }
            )
        else:
            # Hardware target - save active target in DB first
            ha_url = ctx.ha_url or ""
            ha_token = ctx.ha_token or ""
            entity_id = await media_handler.resolve_entity(req, ha_url, ha_token, media_type)
            await registry.save_playback_state(username, {
                "entity_id": entity_id,
                "state": "playing",
                "media_type": media_type,
                "query": req.query,
                "media_content_id": req.media_content_id,
                "updated_at": registry.get_az_timestamp_str()
            })
            
            # Delegate to standard media handler
            return await media_handler.handle_media_play(req)

    @staticmethod
    async def transport(req: MediaTransportRequest) -> ExecutionResult:
        """Route a transport intent to the active target."""
        ctx = req.user_context
        username = ctx.user or "default"
        target = req.entity_id
        
        # If no target specified, look up active target in DB
        if not target:
            db_state = await registry.get_playback_state(username)
            target = db_state.get("entity_id") if db_state else "local"
        
        is_local = target.lower() in ("local", "local_player", "browser", "android")
        
        log.info(f"[MediaPlaybackService] Transport request user={username}, command={req.command}, target={target} (local={is_local})")
        
        if is_local:
            # Update local state in DB
            db_state = await registry.get_playback_state(username) or {}
            updated_fields: Dict[str, Any] = {"entity_id": "local"}
            
            if req.command in ("pause", "stop"):
                updated_fields["state"] = "paused" if req.command == "pause" else "idle"
            elif req.command in ("play", "resume"):
                updated_fields["state"] = "playing"
            
            if req.command == "volume_set" and req.volume_level is not None:
                updated_fields["volume_level"] = req.volume_level
            elif req.command == "volume_up":
                current_vol = db_state.get("volume_level", 0.5)
                updated_fields["volume_level"] = min(current_vol + 0.1, 1.0)
            elif req.command == "volume_down":
                current_vol = db_state.get("volume_level", 0.5)
                updated_fields["volume_level"] = max(current_vol - 0.1, 0.0)
            
            # Merge and save
            for k, v in updated_fields.items():
                db_state[k] = v
            await registry.save_playback_state(username, db_state)
            
            return ExecutionResult(
                status="SUCCESS",
                message=f"Local playback command '{req.command}' executed.",
                service="media_transport"
            )
        else:
            # Delegate to HA handler
            return await media_handler.handle_media_transport(req)

    @staticmethod
    async def status(req: MediaStatusRequest) -> ExecutionResult:
        """Get media status, merging local state with HA/MA active players."""
        ctx = req.user_context
        username = ctx.user or "default"
        
        # Read the user's active playback target choice from DB
        db_state = await registry.get_playback_state(username)
        active_target = db_state.get("entity_id") if db_state else ""
        
        is_local = active_target.lower() in ("local", "local_player")
        
        log.debug(f"[MediaPlaybackService] Status request user={username}, active_target={active_target} (is_local={is_local})")
        
        # Get standard HA players status
        ha_res = await media_status_handler.handle_media_status(req)
        
        # If the active target choice is local, override the 'active' player with local state
        if is_local and db_state:
            local_player = {
                "entity_id": "local_player",
                "friendly_name": "Local Player",
                "state": db_state.get("state", "idle"),
                "media_title": db_state.get("media_title", "Unknown Title"),
                "media_artist": db_state.get("media_artist", "Unknown Artist"),
                "media_album": db_state.get("media_album", ""),
                "source": "local",
                "volume_level": db_state.get("volume_level", 0.7),
                "is_volume_muted": db_state.get("is_volume_muted", False),
                "position": db_state.get("position", 0.0),
                "duration": db_state.get("duration", 0.0),
                "media_content_id": db_state.get("media_content_id", ""),
                "media_type": db_state.get("media_type", "music")
            }
            
            # Inject local player as active, move other HA active players to available list
            detail = ha_res.detail or {}
            ha_active = detail.get("active")
            ha_available = detail.get("available") or []
            if ha_active:
                ha_available.insert(0, ha_active)
            
            detail["active"] = local_player
            detail["available"] = ha_available
            detail["all_players"] = [local_player] + ha_available
            
            ha_res.detail = detail
            
            # Format high-level status message
            vol_pct = int(local_player["volume_level"] * 100)
            ha_res.message = (
                f"**Currently Playing (Local):**\n"
                f"- **Local Player**: {local_player['media_title']} - {local_player['media_artist']} ({local_player['state']}) | Vol: {vol_pct}%"
            )
        elif active_target and ha_res.detail:
            # If target is specific HA player, make sure it is selected as the 'active' one in response
            detail = ha_res.detail
            all_players = detail.get("all_players") or []
            target_player = next((p for p in all_players if p.get("entity_id") == active_target), None)
            if target_player:
                # Set as active
                detail["active"] = target_player
                # Remove from available if it was there
                detail["available"] = [p for p in detail.get("available", []) if p.get("entity_id") != active_target]
                ha_res.detail = detail
                
                # Format message
                vol_str = f" | Vol: {round(target_player['volume_level'] * 100)}%" if target_player.get('volume_level') is not None else ""
                ha_res.message = f"**Currently Playing:**\n- **{target_player['friendly_name']}**: {target_player['media_title'] or target_player['source'] or target_player['state']}{vol_str}"
                
        return ha_res

    @staticmethod
    async def sync_local(req: MediaStateSyncRequest) -> ExecutionResult:
        """Sync a local playback state from client to database."""
        username = req.user_context.user or "default"
        state_data = {
            "entity_id": req.entity_id or "local",
            "state": req.state or "idle",
            "media_type": req.media_type,
            "query": req.query,
            "media_content_id": req.media_content_id,
            "position": req.position or 0.0,
            "duration": req.duration or 0.0,
            "media_title": req.media_title,
            "media_artist": req.media_artist,
            "media_album": req.media_album,
            "queue": req.queue or []
        }
        if req.volume_level is not None:
            state_data["volume_level"] = req.volume_level
        if req.is_volume_muted is not None:
            state_data["is_volume_muted"] = req.is_volume_muted
            
        success = await registry.save_playback_state(username, state_data)
        if success:
            return ExecutionResult(
                status="SUCCESS",
                message="Local playback state synced.",
                service="media_sync_local"
            )
        return ExecutionResult(
            status="FAILURE",
            message="Failed to sync local playback state.",
            service="media_sync_local"
        )

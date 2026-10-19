"""
Handler for Household Intercom System (Section 3.16).
Manages 1-way and 2-way intercom sessions, broadcasts, and TV/speaker announcements.
Reuses the Unified TV & Device Communication Layer (`announce_handlers.py` & `announce_routes.py`).
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from types import SimpleNamespace

import aiohttp

from services.execution.schemas import ExecutionResult, UserContext
from services.shared.rag_client import push_conversation

log = logging.getLogger("execution/intercom")


async def _call_identity(method: str, path: str, json_data: dict | None = None) -> dict | list:
    from services.common.http import get_client
    from services.config import IDENTITY_SVC_URL, INTERNAL_SECRET

    url = f"{IDENTITY_SVC_URL.rstrip('/')}{path}"
    headers = {"X-Internal-Secret": INTERNAL_SECRET}
    async with get_client() as client, client.request(
        method, url, json=json_data, headers=headers, timeout=aiohttp.ClientTimeout(total=10.0)
    ) as resp:
        resp.raise_for_status()
        return await resp.json()


async def _resolve_user_room(user_id: str) -> str | None:
    """Resolve user's current room via ESPresense presence data."""
    try:
        from services.execution.presence import get_presence_tracker
        tracker = get_presence_tracker()
        presence = await tracker.get_user_presence(user_id)
        if presence and presence.get("room") and presence.get("room") != "unknown":
            return presence["room"]
    except Exception as e:
        log.warning(f"[intercom] Presence lookup failed for {user_id}: {e}")
    return None


def _find_assist_satellite_for_room_or_entities(
    all_states: list[dict],
    target_room: str | None,
    target_entities: list[str],
) -> str | None:
    """Find an `assist_satellite.*` voice satellite in the target room or paired with target entities."""
    from services.execution.announce_handlers import _room_slug_from_entity, resolve_tv_companions

    for eid in target_entities:
        if eid.startswith("assist_satellite."):
            return eid
        comp = resolve_tv_companions(eid, all_states)
        if comp.get("room_assist_satellite"):
            return comp["room_assist_satellite"]

    if target_room:
        room_norm = target_room.strip().lower().replace(" ", "_")
        for st in all_states:
            eid = str(st.get("entity_id") or "")
            if not eid.startswith("assist_satellite."):
                continue
            attrs = st.get("attributes") or {}
            slug = _room_slug_from_entity(eid, str(attrs.get("friendly_name") or ""))
            if room_norm in eid.lower() or slug == room_norm:
                return eid
    return None


# One-Way & Two-Way Intercom Sessions
async def handle_intercom_start(req, user_context: UserContext) -> ExecutionResult:
    """Start a two-way or one-way intercom session across voice satellites, TVs, and speakers."""
    try:
        from services.execution import ha_client
        from services.execution.announce_routes import run_announcement
        from services.execution.targets import resolve_targets

        caller = getattr(req, "caller_user_id", None) or user_context.user or "Household"
        target_room = getattr(req, "target_room", None)
        target_user_id = getattr(req, "target_user_id", None)
        requested_type = (getattr(req, "session_type", None) or "twoway").lower()
        explicit_entities = list(getattr(req, "target_entity_ids", None) or [])
        custom_message = (getattr(req, "message", None) or "").strip()
        volume = getattr(req, "volume", None)

        # Resolve target room via presence if not explicitly provided
        if not target_room and target_user_id:
            target_room = await _resolve_user_room(target_user_id)
            if target_room:
                log.info(f"[intercom] Resolved {target_user_id} -> room: {target_room}")

        ha_url = user_context.ha_url or os.environ.get("HA_URL", "")
        ha_token = user_context.ha_token or os.environ.get("HA_TOKEN", "")

        resolved_entities = list(explicit_entities)
        if not resolved_entities and (target_room or explicit_entities) and ha_url and ha_token:
            with contextlib.suppress(Exception):
                resolved_entities = await resolve_targets(
                    entity_ids=explicit_entities,
                    rooms=[target_room] if target_room else [],
                    ha_url=ha_url,
                    ha_token=ha_token,
                )

        all_states: list[dict] = []
        if ha_url and ha_token:
            with contextlib.suppress(Exception):
                all_states = await ha_client.get_states(ha_url, ha_token) or []

        assist_satellite = _find_assist_satellite_for_room_or_entities(
            all_states, target_room, resolved_entities
        )

        effective_mode = requested_type
        satellite_dispatched = False
        oneway_result: dict | None = None
        prompt_text = custom_message or f"Incoming intercom from {caller}."

        # 1. Two-way voice satellite conversation (if room has microphone satellite & twoway requested)
        if requested_type == "twoway" and assist_satellite and ha_url and ha_token:
            with contextlib.suppress(Exception):
                sat_res = await ha_client.call_service(
                    ha_url,
                    ha_token,
                    "assist_satellite",
                    "start_conversation",
                    assist_satellite,
                    {"start_message": prompt_text, "preannounce": True},
                )
                if not sat_res.get("ok"):
                    sat_res = await ha_client.call_service(
                        ha_url,
                        ha_token,
                        "assist_satellite",
                        "announce",
                        assist_satellite,
                        {"message": prompt_text, "preannounce": True},
                    )
                satellite_dispatched = bool(sat_res.get("ok"))
                log.info(f"[intercom] 2-way satellite {assist_satellite} dispatch: {sat_res}")

        # 2. If no microphone satellite is in the target room/device, or one-way mode requested,
        #    or explicit TV/speaker targets were given with a message, dispatch via unified TV/speaker layer
        if not satellite_dispatched and requested_type == "twoway" and resolved_entities:
            effective_mode = "oneway"

        should_dispatch_media = bool(resolved_entities) and (
            effective_mode in ("oneway", "broadcast", "announcement")
            or bool(custom_message)
            or not satellite_dispatched
        )
        if should_dispatch_media and ha_url and ha_token:
            with contextlib.suppress(Exception):
                ann_req = SimpleNamespace(
                    target_devices=resolved_entities,
                    target_entity_ids=resolved_entities,
                    target_rooms=[target_room] if (target_room and not resolved_entities) else [],
                    group_id=None,
                    message=prompt_text,
                    volume=volume if volume is not None else 0.65,
                )
                ctx_with_ha = UserContext(
                    user=user_context.user or caller,
                    is_admin=user_context.is_admin,
                    ha_url=ha_url,
                    ha_token=ha_token,
                )
                oneway_result = await run_announcement(ann_req, ctx_with_ha)

        payload = {
            "caller_user_id": caller,
            "target_user_id": target_user_id,
            "target_room": target_room,
            "target_entity_ids": resolved_entities or explicit_entities,
            "session_type": effective_mode,
            "assist_satellite": assist_satellite,
            "satellite_dispatched": satellite_dispatched,
        }
        session_data = await _call_identity("POST", "/api/intercom/sessions", payload)
        if isinstance(session_data, dict):
            session_data["effective_mode"] = effective_mode
            session_data["assist_satellite"] = assist_satellite
            session_data["satellite_dispatched"] = satellite_dispatched
            if oneway_result:
                session_data["announcement"] = oneway_result

        return ExecutionResult(
            status="SUCCESS",
            message=(
                f"Intercom session started ({effective_mode}): "
                f"{session_data.get('session_id', 'unknown') if isinstance(session_data, dict) else 'ok'}"
            ),
            service="intercom",
            detail=session_data if isinstance(session_data, dict) else {"session": session_data},
        )
    except Exception as e:
        log.error(f"Intercom start failed: {e}")
        return ExecutionResult(status="FAILURE", message=str(e), service="intercom")


async def handle_intercom_end(req, user_context: UserContext | None = None) -> ExecutionResult:
    """End an active intercom session and stop any active TV/speaker streams."""
    try:
        from services.execution.announce_handlers import stop_tv_or_media_alert

        session_id = getattr(req, "session_id", None)
        if not session_id:
            return ExecutionResult(status="FAILURE", message="session_id is required", service="intercom")

        # Look up session targets before ending so we can stop any active TV/media playback
        target_entities: list[str] = []
        with contextlib.suppress(Exception):
            sessions = await _call_identity("GET", "/api/intercom/sessions")
            if isinstance(sessions, list):
                for s in sessions:
                    if isinstance(s, dict) and (
                        s.get("session_id") == session_id
                        or str(s.get("key") or "").endswith(f":{session_id}")
                    ):
                        target_entities = list(s.get("target_entity_ids") or [])
                        break

        await _call_identity("DELETE", f"/api/intercom/sessions/{session_id}")

        ha_url = (user_context.ha_url if user_context else None) or os.environ.get("HA_URL", "")
        ha_token = (user_context.ha_token if user_context else None) or os.environ.get("HA_TOKEN", "")
        stopped: list[str] = []
        if target_entities and ha_url and ha_token:
            for eid in target_entities:
                with contextlib.suppress(Exception):
                    await stop_tv_or_media_alert(ha_url, ha_token, eid)
                    stopped.append(eid)

        return ExecutionResult(
            status="SUCCESS",
            message=f"Intercom session '{session_id}' ended",
            service="intercom",
            detail={"session_id": session_id, "stopped_entities": stopped},
        )
    except Exception as e:
        log.error(f"Intercom end failed: {e}")
        return ExecutionResult(status="FAILURE", message=str(e), service="intercom")


async def handle_intercom_list_sessions() -> ExecutionResult:
    """List active intercom sessions."""
    try:
        sessions = await _call_identity("GET", "/api/intercom/sessions")
        return ExecutionResult(
            status="SUCCESS",
            message="Intercom sessions retrieved",
            service="intercom",
            detail={"sessions": sessions},
        )
    except Exception as e:
        log.error(f"Intercom list sessions failed: {e}")
        return ExecutionResult(status="FAILURE", message=str(e), service="intercom")


# Broadcast / PA System
async def handle_intercom_broadcast(req, user_context: UserContext) -> ExecutionResult:
    """Broadcast a message to resolved target devices/rooms (real dispatch)."""
    try:
        from services.execution.announce_routes import run_broadcast
        result = await run_broadcast(req, user_context)
        msg = getattr(req, "message", "") or ""
        if msg and result.get("status") == "SUCCESS":
            asyncio.ensure_future(
                push_conversation(user_context.user, msg, room_id="broadcast", user_id=user_context.user)
            )
        return ExecutionResult(
            status=result.get("status", "FAILURE"),
            message=result.get("message", "Broadcast finished"),
            service="intercom",
            detail=result.get("detail"),
        )
    except Exception as e:
        log.error(f"Intercom broadcast failed: {e}")
        return ExecutionResult(status="FAILURE", message=str(e), service="intercom")


# TV/Smart Speaker Announcements
async def handle_intercom_announcement(req, user_context: UserContext) -> ExecutionResult:
    """Send a one-way announcement to TVs or smart speakers (real dispatch)."""
    try:
        from services.execution.announce_routes import run_announcement
        result = await run_announcement(req, user_context)
        msg = getattr(req, "message", "") or ""
        if msg and result.get("status") == "SUCCESS":
            asyncio.ensure_future(
                push_conversation(user_context.user, msg, room_id="announcement", user_id=user_context.user)
            )
        return ExecutionResult(
            status=result.get("status", "FAILURE"),
            message=result.get("message", "Announcement finished"),
            service="intercom",
            detail=result.get("detail"),
        )
    except Exception as e:
        log.error(f"Intercom announcement failed: {e}")
        return ExecutionResult(status="FAILURE", message=str(e), service="intercom")


# Intercom Configuration
async def handle_intercom_config() -> ExecutionResult:
    """Get intercom configuration."""
    try:
        config = await _call_identity("GET", "/api/intercom/config")
        return ExecutionResult(
            status="SUCCESS",
            message="Intercom configuration retrieved",
            service="intercom",
            detail=config if isinstance(config, dict) else {"config": config},
        )
    except Exception as e:
        log.error(f"Intercom config retrieval failed: {e}")
        return ExecutionResult(status="FAILURE", message=str(e), service="intercom")


async def handle_intercom_update_config(req) -> ExecutionResult:
    """Update intercom configuration."""
    try:
        payload = {
            "default_tts_engine": getattr(req, "default_tts_engine", None),
            "default_voice": getattr(req, "default_voice", None),
            "default_volume": getattr(req, "default_volume", None),
            "enable_espresense_routing": getattr(req, "enable_espresense_routing", True),
        }
        config = await _call_identity("PATCH", "/api/intercom/config", payload)
        return ExecutionResult(
            status="SUCCESS",
            message="Intercom configuration updated",
            service="intercom",
            detail=config if isinstance(config, dict) else {"config": config},
        )
    except Exception as e:
        log.error(f"Intercom config update failed: {e}")
        return ExecutionResult(status="FAILURE", message=str(e), service="intercom")

"""
Shared announce/broadcast fan-out used by HTTP routes and intercom handlers.

Lives outside main.py so handlers can import it without circular imports.
Dispatch is injected: main.py passes execute_announce so we never import main here.
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from services.execution.schemas import AnnouncementRequest, UserContext
from services.execution.schemas_intercom import IntercomAnnouncementRequest, IntercomBroadcastRequest
from services.execution.targets import resolve_targets

log = logging.getLogger("execution/announce_routes")

AnnounceFn = Callable[[AnnouncementRequest], Awaitable[Any]]


def _ha_creds(user_context: UserContext) -> tuple[str, str]:
    return (user_context.ha_url or "", user_context.ha_token or "")


async def _resolve_for_request(
    *,
    entity_ids: list[str] | None,
    rooms: list[str] | None,
    group_id: str | None,
    user_context: UserContext,
) -> list[str]:
    ha_url, ha_token = _ha_creds(user_context)
    if not ha_url or not ha_token:
        from services.execution.main import resolve_first_user  # lazy: only when creds missing
        creds = await resolve_first_user() or {}
        ha_url = ha_url or creds.get("ha_url", "")
        ha_token = ha_token or creds.get("ha_token", "")
    return await resolve_targets(
        entity_ids=entity_ids,
        rooms=rooms,
        group_id=group_id,
        ha_url=ha_url,
        ha_token=ha_token,
    )


async def run_announcement(
    req: IntercomAnnouncementRequest | Any,
    user_context: UserContext,
    announce_fn: AnnounceFn | None = None,
) -> dict:
    """Resolve targets and fan out announcements. Returns dict for ExecutionResult."""
    if announce_fn is None:
        from services.execution.main import execute_announce as announce_fn  # type: ignore

    entity_ids = getattr(req, "target_devices", None) or getattr(req, "target_entity_ids", None) or []
    rooms = getattr(req, "target_rooms", None) or []
    group_id = getattr(req, "group_id", None)

    targets = await _resolve_for_request(
        entity_ids=entity_ids,
        rooms=rooms,
        group_id=group_id,
        user_context=user_context,
    )
    if not targets:
        return {
            "status": "FAILURE",
            "message": "No announcement targets resolved. Set target_devices/target_entity_ids, target_rooms (room_speakers config), or group_id.",
            "detail": {"resolved": []},
        }

    volume = getattr(req, "volume", None)
    results = []
    successes = 0
    for eid in targets:
        ann = AnnouncementRequest(
            user_context=user_context,
            entity_id=eid,
            message=req.message,
            volume=volume if volume is not None else 0.6,
        )
        try:
            r = await announce_fn(ann)
            ok = getattr(r, "status", None) == "SUCCESS" or (isinstance(r, dict) and r.get("status") == "SUCCESS")
            if ok:
                successes += 1
            results.append({"entity_id": eid, "result": _result_summary(r)})
        except Exception as e:
            log.error(f"[announce_routes] announce failed for {eid}: {e}")
            results.append({"entity_id": eid, "error": str(e)})

    status = "SUCCESS" if successes else "FAILURE"
    return {
        "status": status,
        "message": f"Announcement sent to {successes}/{len(targets)} devices",
        "detail": {"targets": results, "resolved": targets},
    }


async def run_broadcast(
    req: IntercomBroadcastRequest | Any,
    user_context: UserContext,
    announce_fn: AnnounceFn | None = None,
) -> dict:
    """Same as announcement but accepts broadcast-shaped request (rooms/entities/group)."""
    entity_ids = getattr(req, "target_entity_ids", None) or []
    rooms = getattr(req, "target_rooms", None) or []
    group_id = getattr(req, "group_id", None)

    # Broadcast-shaped body may reuse announcement fan-out
    class _Shim:
        pass

    shim = _Shim()
    shim.target_devices = entity_ids
    shim.target_entity_ids = entity_ids
    shim.target_rooms = rooms
    shim.group_id = group_id
    shim.message = req.message
    shim.volume = getattr(req, "volume", None)
    return await run_announcement(shim, user_context, announce_fn=announce_fn)


def _result_summary(r: Any) -> dict:
    if r is None:
        return {"status": "UNKNOWN"}
    if isinstance(r, dict):
        return {"status": r.get("status"), "message": r.get("message")}
    return {
        "status": getattr(r, "status", None),
        "message": getattr(r, "message", None),
    }

"""Approve or reject a Raven mission from its phone notice.

A self-repair mission waits in Raven Ops (status ``pending``) until an admin
runs it or deletes it. The notice from ``notices.mission_needs_approval``
offers the same two choices as buttons; these are the push callbacks behind
them (``raven_approve`` and ``raven_reject`` in push_callbacks).

The same rules as Execute in Raven Ops apply: admins only, whatever the
mission type. On top of that a mission is only approved while it still waits
and once the mission it depends on (``depends_on_mission_id``) has completed;
until then the background worker starts it by itself when that one finishes.
Each button first claims the mission in identity with one conditional status
change (pending to queued, or pending to dismissed), so two taps, or a tap and
Execute in Raven Ops, can never run it twice.
"""
from __future__ import annotations

import logging

import aiohttp
from fastapi import HTTPException

from services.common.http import get_client
from services.gateway.config import IDENTITY_SVC, INTERNAL_SECRET

log = logging.getLogger("raven_approval")

_HEADERS = {"X-Internal-Secret": INTERNAL_SECRET}

_TIMEOUT = aiohttp.ClientTimeout(total=10)


async def _mission(mission_id: int) -> dict | None:
    async with get_client().get(f"{IDENTITY_SVC}/api/raven/missions/{mission_id}",
                                headers=_HEADERS, timeout=_TIMEOUT) as resp:
        mission = await resp.json(content_type=None) if resp.status == 200 else None
    return mission if isinstance(mission, dict) else None


async def _waiting_mission(creds: dict, params: dict) -> dict:
    if not creds.get("is_admin"):
        raise HTTPException(status_code=403, detail="Only an admin can approve or reject a mission")
    try:
        mission_id = int((params or {}).get("mission_id"))
    except (TypeError, ValueError) as e:
        raise HTTPException(status_code=422, detail="mission_id is required") from e
    mission = await _mission(mission_id)
    if mission is None:
        raise HTTPException(status_code=404, detail="Mission not found")
    if mission.get("status") != "pending":
        raise HTTPException(status_code=409, detail=f"Mission #{mission_id} is already {mission.get('status')}")
    return mission


async def _transition(mission_id: int, from_status: str, to_status: str) -> None:
    """Change the status only if it is still ``from_status`` (409 otherwise)."""
    async with get_client().post(f"{IDENTITY_SVC}/api/raven/missions/{mission_id}/transition",
                                 json={"from_status": from_status, "to_status": to_status},
                                 headers=_HEADERS, timeout=_TIMEOUT) as resp:
        if resp.status == 200:
            return
        try:
            body = await resp.json(content_type=None)
        except ValueError:
            body = None
        detail = body.get("detail") if isinstance(body, dict) else None

        raise HTTPException(status_code=resp.status if resp.status in (404, 409) else 502,
                            detail=detail or f"Could not claim mission #{mission_id}")


async def approve(creds: dict, params: dict) -> dict:
    """Run the mission, exactly as Execute in Raven Ops does."""
    from services.gateway.main import dispatch_admin_mission

    mission = await _waiting_mission(creds, params)
    mission_id = int(mission["id"])
    depends_on = mission.get("depends_on_mission_id")
    if depends_on:
        before = await _mission(int(depends_on))
        if before is None or before.get("status") != "completed":
            state = before.get("status") if before else "gone"
            raise HTTPException(
                status_code=409, detail=f"Mission #{mission_id} waits for mission #{depends_on}, which is {state}")
    await _transition(mission_id, "pending", "queued")
    try:
        return await dispatch_admin_mission(mission_id, creds)
    except Exception:
        # Not enqueued: put it back so it can be approved again.
        try:
            await _transition(mission_id, "queued", "pending")
        except Exception as e:
            log.warning("Mission #%s stays queued after a failed dispatch: %s", mission_id, e)
        raise


async def reject(creds: dict, params: dict) -> dict:
    """Drop the mission, exactly as Delete in Raven Ops does."""
    mission = await _waiting_mission(creds, params)
    await _transition(int(mission["id"]), "pending", "dismissed")
    async with get_client().delete(f"{IDENTITY_SVC}/api/raven/missions/{mission['id']}",
                                   headers=_HEADERS, timeout=_TIMEOUT) as resp:
        if resp.status != 200:
            raise HTTPException(status_code=resp.status, detail="Could not reject the mission")
    return {"status": "SUCCESS", "message": f"Mission #{mission['id']} rejected."}

"""Approve or reject a Raven mission from its phone notice.

A self-repair mission waits in Raven Ops (status ``pending``) until an admin
runs it or deletes it. The notice from ``notices.mission_needs_approval``
offers the same two choices as buttons; these are the push callbacks behind
them (``raven_approve`` and ``raven_reject`` in push_callbacks). Only an
admin, or the person who owns the mission, may act, and only while it is
still waiting.
"""
from __future__ import annotations

import aiohttp
from fastapi import HTTPException

from services.common.http import get_client
from services.gateway.config import IDENTITY_SVC, INTERNAL_SECRET

_HEADERS = {"X-Internal-Secret": INTERNAL_SECRET}
_TIMEOUT = aiohttp.ClientTimeout(total=10)


def may_act(creds: dict, mission: dict) -> bool:
    if creds.get("is_admin"):
        return True
    owner = mission.get("user_id")
    return owner is not None and owner == creds.get("id")


async def _waiting_mission(creds: dict, params: dict) -> dict:
    try:
        mission_id = int((params or {}).get("mission_id"))
    except (TypeError, ValueError) as e:
        raise HTTPException(status_code=422, detail="mission_id is required") from e
    async with get_client().get(f"{IDENTITY_SVC}/api/raven/missions/{mission_id}",
                                headers=_HEADERS, timeout=_TIMEOUT) as resp:
        mission = await resp.json(content_type=None) if resp.status == 200 else None
    if not isinstance(mission, dict):
        raise HTTPException(status_code=404, detail="Mission not found")
    if not may_act(creds, mission):
        raise HTTPException(status_code=403, detail="Only an admin can approve or reject this mission")
    if mission.get("status") != "pending":
        raise HTTPException(status_code=409, detail=f"Mission #{mission_id} is already {mission.get('status')}")
    return mission


async def approve(creds: dict, params: dict) -> dict:
    """Run the mission, exactly as Execute in Raven Ops does."""
    from services.gateway.main import dispatch_admin_mission

    mission = await _waiting_mission(creds, params)
    return await dispatch_admin_mission(int(mission["id"]), creds)


async def reject(creds: dict, params: dict) -> dict:
    """Drop the mission, exactly as Delete in Raven Ops does."""
    mission = await _waiting_mission(creds, params)
    async with get_client().delete(f"{IDENTITY_SVC}/api/raven/missions/{mission['id']}",
                                   headers=_HEADERS, timeout=_TIMEOUT) as resp:
        if resp.status != 200:
            raise HTTPException(status_code=resp.status, detail="Could not reject the mission")
    return {"status": "SUCCESS", "message": f"Mission #{mission['id']} rejected."}

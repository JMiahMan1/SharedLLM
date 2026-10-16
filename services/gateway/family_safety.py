"""Family safety from the app: "On my way", SOS, and important places.

* ``POST /api/family/on-my-way`` tells the rest of the family you are heading
  home, with the drive time by road when geo can give one.
* ``POST /api/family/sos`` with ``{"confirm": true}`` is an urgent (priority 5)
  ``family_safety`` notice to everyone else, with your last known position.
  Telemetry delivers priority 5 family_safety notices even to people who
  switched the category off.
* ``PUT /api/geo/places/{zone_id}`` marks a place important and chooses who
  hears about arrivals and departures there (geo sends those notices).

The other family_safety notices come from identity (low phone battery) and
geo (important places, long stops). See docs/FAMILY_HUB.md.
"""
from __future__ import annotations

import logging
import time
from typing import Any

import aiohttp
from fastapi import APIRouter, HTTPException, Request

from services.common import notify
from services.common.http import get_client
from services.gateway.config import GEO_SVC, IDENTITY_SVC, INTERNAL_SECRET

log = logging.getLogger("gateway.family_safety")

router = APIRouter()

_HEADERS = {"X-Internal-Secret": INTERNAL_SECRET}
_TIMEOUT = aiohttp.ClientTimeout(total=10)
#: A last position older than this is said to be old in the SOS notice.
STALE_FIX_S = 15 * 60


async def _caller(request: Request) -> str:
    from services.gateway.main import _require_authenticated

    return await _require_authenticated(request)


async def _get_json(url: str, **params) -> Any:
    try:
        async with get_client().get(url, params=params or None, headers=_HEADERS, timeout=_TIMEOUT) as resp:
            return await resp.json(content_type=None) if resp.status == 200 else None
    except Exception as e:
        log.warning(f"[family-safety] {url}: {type(e).__name__}: {e}")
        return None


def _ago(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{minutes} min ago"
    hours, rest = divmod(minutes, 60)
    return f"{hours} h {rest} min ago" if rest else f"{hours} h ago"


def on_my_way_text(name: str, eta: dict | None) -> tuple[str, str]:
    if eta and eta.get("arrived"):
        return f"{name} is on the way", "Already at home."
    if eta and eta.get("duration_s") is not None:
        minutes = max(1, round(eta["duration_s"] / 60))
        return f"{name} is on the way home", f"Home in about {minutes} min by road."
    return f"{name} is on the way home", "No drive time yet."


def sos_text(name: str, location: dict | None, now: float) -> tuple[str, str, str | None]:
    """(title, body, map link) for an SOS; the link is None with no position."""
    title = f"SOS from {name}"
    if not location or location.get("latitude") is None:
        return title, f"{name} needs help. No position is known.", None
    lat, lon = float(location["latitude"]), float(location["longitude"])
    stamp = location.get("timestamp")
    if stamp is None:
        stamp = location.get("updated_at")
    seen = float(stamp) if stamp is not None else now
    when = _ago(max(0.0, now - seen))
    old = " (may be out of date)" if now - seen > STALE_FIX_S else ""
    link = f"https://www.openstreetmap.org/?mlat={lat:.5f}&mlon={lon:.5f}#map=17/{lat:.5f}/{lon:.5f}"
    return title, f"{name} needs help. Last seen {when}{old} at {lat:.5f}, {lon:.5f}.", link


@router.post("/api/family/on-my-way")
async def on_my_way(request: Request):
    user = await _caller(request)
    eta = await _get_json(f"{GEO_SVC}/people/{user}/eta", to="home", viewer=user)
    title, body = on_my_way_text(user.title(), eta)
    url = f"/wander?person={user}"
    sent = await notify.send_many(
        [u for u in await notify.family() if u != user],
        lambda viewer: notify.notice(
            viewer, "family_safety", title, body, url=url, priority=3, tag=f"on_my_way:{user}",
            data={"person": user, "eta": (eta or {}).get("eta")}, actions=[notify.view_action(url, "Map")]),
    )
    return {"status": "SUCCESS", "sent": sent, "eta_s": (eta or {}).get("duration_s")}


@router.post("/api/family/sos")
async def sos(request: Request):
    user = await _caller(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict) or body.get("confirm") is not True:
        raise HTTPException(status_code=422, detail="An SOS must be confirmed")
    now = time.time()
    location = await _get_json(f"{IDENTITY_SVC}/api/users/{user}/location")
    title, text, link = sos_text(user.title(), location, now)
    url = f"/wander?person={user}"
    actions = [notify.view_action(url, "Map")]
    if link:
        actions.append(notify.view_action(link, "Directions"))
    family = [u for u in await notify.family() if u != user]
    sent = await notify.send_many(
        family,
        lambda viewer: notify.notice(
            viewer, "family_safety", title, text, url=url, priority=5, tag=f"sos:{user}",
            once=f"sos:{user}:{int(now)}", data={"person": user, "sos": True, "map": link}, actions=actions),
    )
    log.warning(f"[family-safety] SOS from {user}: {sent} of {len(family)} notices accepted")
    return {"status": "SUCCESS", "sent": sent, "family": len(family), "located": link is not None}


@router.put("/api/geo/places/{zone_id}")
async def put_place(request: Request, zone_id: str):
    """Mark a place important and pick who hears about it (empty: everyone)."""
    await _caller(request)
    body = await request.json()
    payload = {
        "important": bool(body.get("important")),
        "notify": [str(u) for u in body.get("notify") or [] if isinstance(u, str)],
    }
    async with get_client().put(f"{GEO_SVC}/place-settings/{zone_id}", json=payload,
                                headers=_HEADERS, timeout=_TIMEOUT) as resp:
        answer = await resp.json(content_type=None)
        if resp.status != 200:
            raise HTTPException(status_code=resp.status, detail=str((answer or {}).get("detail") or "Could not save the place"))
    return answer

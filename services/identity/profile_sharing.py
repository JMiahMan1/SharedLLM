"""Profile stats: which areas the family may see, and the admin's usage read
(docs/PROFILE_STATS.md).

A person always sees all of their own stats. For everyone else in the family
each area is opt-in. Only areas with no sharing switch of their own are kept
here (``PROFILE_SHARE_AREAS``); health, activity, Wander and Bible are decided
by the switches they already have (the health service's HealthShare and
identity's UserActivitySharing), so there is never a second, conflicting
switch for the same thing.

Routes (built by ``build_router`` with the app's dependencies):

* ``GET``/``PUT /api/users/me/profile-sharing``: the caller's choice.
* ``GET /api/internal/profile-sharing?username=``: one person's choice, for
  the gateway's profile page (internal secret).
* ``GET /api/internal/people/{username}/usage``: the admin page's identity
  side (internal secret; the gateway allows admins only): devices and app
  versions, feature use, capability gaps, how many assistant conversations
  per day (counts only, never what was said), and device events per kind.
"""
from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable
from datetime import datetime, timedelta

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlmodel import Session, select

from services.identity.models import (
    AssistantConversation,
    CapabilityGap,
    Device,
    DeviceEvent,
    FeatureUsage,
    User,
    UserProfileSharing,
)

#: Areas of the profile page that have no sharing switch elsewhere.
PROFILE_SHARE_AREAS: tuple[str, ...] = ("garden", "recipes", "jarvis_use")

#: How far back the admin usage read looks, at most.
USAGE_MAX_DAYS = 90


def sharing_data(row: UserProfileSharing | None) -> dict:
    """The stored choice, cleaned: unknown areas are dropped, order is fixed."""
    try:
        raw = json.loads(row.data) if row and row.data else {}
    except (TypeError, ValueError):
        raw = {}
    areas = raw.get("areas") if isinstance(raw, dict) else None
    chosen = {a for a in areas if isinstance(a, str)} if isinstance(areas, list) else set()
    return {"areas": [a for a in PROFILE_SHARE_AREAS if a in chosen]}


def _view(session: Session, username: str) -> dict:
    row = session.get(UserProfileSharing, username)
    return {"username": username, "available": list(PROFILE_SHARE_AREAS), **sharing_data(row)}


def _day(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return None


def _since(days: int) -> str:
    return (datetime.now() - timedelta(days=days)).isoformat()


def usage(session: Session, username: str, days: int) -> dict:
    """The identity side of the admin page for one person. Counts, names and
    versions only: an assistant conversation is counted, never read."""
    days = max(1, min(int(days), USAGE_MAX_DAYS))
    since = _since(days)
    devices = [
        {
            "label": d.label,
            "kind": d.kind,
            "model": d.model,
            "manufacturer": d.manufacturer,
            "os_version": d.os_version,
            "app_version": d.app_version,
            "app_build": d.app_build,
            "last_seen_at": d.last_seen_at,
            "first_seen_at": d.first_seen_at,
            "archived": bool(d.archived_at),
            "revoked": bool(d.revoked),
        }
        for d in session.exec(select(Device).where(Device.owner_username == username)).all()
    ]
    devices.sort(key=lambda d: d.get("last_seen_at") or "", reverse=True)
    features = [
        {"feature": f.feature, "kind": f.kind, "uses": f.uses, "first_used_at": f.first_used_at, "last_used_at": f.last_used_at}
        for f in session.exec(select(FeatureUsage).where(FeatureUsage.username == username)).all()
    ]
    features.sort(key=lambda f: -int(f["uses"] or 0))
    gaps = [
        {
            "capability": g.capability,
            "kind": g.kind,
            "request": g.request,
            "occurrences": g.occurrences,
            "first_seen_at": g.first_seen_at,
            "last_seen_at": g.last_seen_at,
            "resolved": bool(g.resolved),
        }
        for g in session.exec(select(CapabilityGap).where(CapabilityGap.username == username)).all()
    ]
    gaps.sort(key=lambda g: (g["resolved"], -(g["occurrences"] or 0)))
    per_day: Counter[str] = Counter()
    conversation_features: Counter[str] = Counter()
    for row in session.exec(
        select(AssistantConversation.at, AssistantConversation.features).where(
            AssistantConversation.username == username, AssistantConversation.at >= since
        )
    ).all():
        at, feats = row
        day = _day(at)
        if day:
            per_day[day] += 1
        try:
            for f in json.loads(feats or "[]"):
                if isinstance(f, str):
                    conversation_features[f] += 1
        except (TypeError, ValueError):
            pass
    events: Counter[str] = Counter()
    app_opens: Counter[str] = Counter()
    for row in session.exec(
        select(DeviceEvent.event, DeviceEvent.at).where(DeviceEvent.username == username, DeviceEvent.at >= since)
    ).all():
        event, at = row
        events[event] += 1
        if event == "app_open" and _day(at):
            app_opens[_day(at) or ""] += 1
    return {
        "username": username,
        "days": days,
        "devices": devices,
        "features": features,
        "capability_gaps": gaps,
        "conversations": {
            "total": sum(per_day.values()),
            "per_day": [{"day": d, "count": per_day[d]} for d in sorted(per_day)],
            "features": [{"feature": f, "count": n} for f, n in conversation_features.most_common(12)],
        },
        "events": dict(sorted(events.items())),
        "app_opens_per_day": [{"day": d, "count": app_opens[d]} for d in sorted(app_opens)],
    }


def build_router(get_session: Callable, require_api_key: Callable, require_internal: Callable) -> APIRouter:
    router = APIRouter()

    @router.get("/api/users/me/profile-sharing")
    def get_profile_sharing(session: Session = Depends(get_session), user: User = Depends(require_api_key)):
        """The caller's profile areas the family may see (none by default)."""
        return {"status": "SUCCESS", **_view(session, user.username)}

    @router.put("/api/users/me/profile-sharing")
    def put_profile_sharing(
        body: dict = Body(...),
        session: Session = Depends(get_session),
        user: User = Depends(require_api_key),
    ):
        """Replace the caller's choice: ``{"areas": [...]}``, a subset of the
        areas listed in ``available``."""
        areas = body.get("areas") if isinstance(body, dict) else None
        if not isinstance(areas, list) or not all(isinstance(a, str) for a in areas):
            raise HTTPException(status_code=422, detail="areas must be a list of area names")
        unknown = sorted(set(areas) - set(PROFILE_SHARE_AREAS))
        if unknown:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown area(s) {unknown}; choose from {list(PROFILE_SHARE_AREAS)}",
            )
        row = session.get(UserProfileSharing, user.username) or UserProfileSharing(username=user.username)
        row.data = json.dumps({"areas": [a for a in PROFILE_SHARE_AREAS if a in set(areas)]})
        session.add(row)
        session.commit()
        return {"status": "SUCCESS", **_view(session, user.username)}

    @router.get("/api/internal/profile-sharing")
    def internal_profile_sharing(
        username: str = Query(...),
        session: Session = Depends(get_session),
        _: None = Depends(require_internal),
    ):
        """Internal: one person's profile areas the family may see."""
        return {"status": "SUCCESS", **_view(session, username.strip().lower())}

    @router.get("/api/internal/people/{username}/usage")
    def internal_person_usage(
        username: str,
        days: int = Query(default=30, ge=1, le=USAGE_MAX_DAYS),
        session: Session = Depends(get_session),
        _: None = Depends(require_internal),
    ):
        """Internal: the identity side of the admin's per-person stats page."""
        name = username.strip().lower()
        if session.exec(select(User).where(User.username == name)).first() is None:
            raise HTTPException(status_code=404, detail="No such person")
        return {"status": "SUCCESS", **usage(session, name, days)}

    return router

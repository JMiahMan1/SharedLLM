"""Push notification data kept by identity (docs/PUSH.md).

Each person's ntfy topic, which notice categories they receive, and their
UnifiedPush registrations. The routes are built by ``build_router`` with
main.py's own session and auth dependencies, so tests that override those
dependencies cover these routes too.
"""
from __future__ import annotations

import json
import re
import secrets
from collections.abc import Callable

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlmodel import Session, select

from services.identity.models import UnifiedPushRegistration, User, UserNotificationPreference

#: Notice categories people can switch off. The web and Android settings
#: screens carry their own labels for these (notificationCategories.ts,
#: PushCategories.java).
CATEGORIES = (
    "chat", "family_presence", "family_safety", "leave_by", "calendar", "timers", "bible",
    "reports", "home", "jobs", "raven", "app_updates", "health", "admin",
)
#: Categories only admins receive.
ADMIN_ONLY = frozenset({"admin"})

UP_TOPIC_PREFIX = "up"
#: Registrations one device may hold (each is one app).
MAX_REGISTRATIONS_PER_DEVICE = 50

_DEVICE_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_APP = re.compile(r"^[A-Za-z0-9._-]{1,255}$")


def new_push_topic(username: str) -> str:
    """``jarvis-<user>-<24 random URL-safe chars>``, a valid ntfy topic name."""
    safe = re.sub(r"[^A-Za-z0-9_-]", "", username)[:24] or "user"
    return f"jarvis-{safe}-{secrets.token_urlsafe(18)}"


def new_up_topic() -> str:
    """``up`` + 32 random URL-safe chars: the capability for one registration."""
    return UP_TOPIC_PREFIX + secrets.token_urlsafe(24)


def visible_categories(user: User) -> list[str]:
    return [c for c in CATEGORIES if user.is_admin or c not in ADMIN_ONLY]


def _stored(session: Session, username: str) -> tuple[UserNotificationPreference | None, dict]:
    row = session.exec(
        select(UserNotificationPreference).where(UserNotificationPreference.username == username)
    ).first()
    try:
        data = json.loads(row.data) if row and row.data else {}
    except (TypeError, ValueError):
        data = {}
    return row, data if isinstance(data, dict) else {}


def effective_preferences(session: Session, user: User) -> dict[str, bool]:
    """Every category with its on/off state: absent is on, ``admin`` only for admins."""
    _, data = _stored(session, user.username)
    visible = set(visible_categories(user))
    return {c: c in visible and bool(data.get(c, True)) for c in CATEGORIES}


def apply_preferences(session: Session, user: User, changes: dict) -> None:
    """Switch some categories on or off; 422 for an unknown one or a non-bool."""
    visible = set(visible_categories(user))
    for key, value in changes.items():
        if key not in visible:
            raise HTTPException(status_code=422, detail=f"Unknown category: {key}")
        if not isinstance(value, bool):
            raise HTTPException(status_code=422, detail=f"{key} must be true or false")
    row, data = _stored(session, user.username)
    if row is None:
        row = UserNotificationPreference(username=user.username)
    data.update(changes)
    row.data = json.dumps(data, sort_keys=True)
    session.add(row)
    session.commit()


def _user_view(session: Session, user: User) -> dict:
    prefs = effective_preferences(session, user)
    return {"status": "SUCCESS", "preferences": {c: prefs[c] for c in visible_categories(user)}}


def _user_or_404(session: Session, username: str) -> User:
    name = (username or "").strip()
    user = session.exec(select(User).where(User.username == name)).first()
    if not user and name.lower() != name:
        user = session.exec(select(User).where(User.username == name.lower())).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


def ensure_push_topic(session: Session, user: User) -> str:
    if not user.push_topic:
        user.push_topic = new_push_topic(user.username)
        session.add(user)
        session.commit()
        session.refresh(user)
    return user.push_topic


def forget_user(session: Session, username: str) -> None:
    """Drop a deleted person's preferences and UnifiedPush registrations, so a
    new account with the same name starts clean."""
    for model in (UserNotificationPreference, UnifiedPushRegistration):
        for row in session.exec(select(model).where(model.username == username)).all():
            session.delete(row)
    session.commit()


def _registration_fields(body: dict, device_required: bool = True) -> tuple[str, str, str, str]:
    username = str(body.get("username") or "").strip()
    device_id = str(body.get("device_id") or "").strip()
    app = str(body.get("app") or "").strip()
    token = str(body.get("token") or "").strip()
    if not username:
        raise HTTPException(status_code=422, detail="username is required")
    if (device_required or device_id) and not _DEVICE_ID.match(device_id):
        raise HTTPException(status_code=422, detail="device_id must be 1 to 128 letters, digits or ._:-")
    if not _APP.match(app):
        raise HTTPException(status_code=422, detail="app must be an application id")
    if not token or len(token) > 1024:
        raise HTTPException(status_code=422, detail="token must be 1 to 1024 characters")
    return username, device_id, app, token


def _registration_read(row: UnifiedPushRegistration) -> dict:
    return {
        "device_id": row.device_id,
        "app": row.app,
        "token": row.token,
        "topic": row.topic,
        "created_at": row.created_at,
    }


def build_router(
    get_session: Callable,
    require_api_key: Callable,
    require_internal: Callable,
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/users/me/notification-preferences")
    def get_notification_preferences(
        session: Session = Depends(get_session),
        user: User = Depends(require_api_key),
    ):
        """The caller's on/off state per notice category they can change."""
        return _user_view(session, user)

    @router.put("/api/users/me/notification-preferences")
    def put_notification_preferences(
        body: dict = Body(...),
        session: Session = Depends(get_session),
        user: User = Depends(require_api_key),
    ):
        """Change some switches: body ``{category: bool}``; others keep their state."""
        changes = body.get("preferences") if isinstance(body.get("preferences"), dict) else body
        apply_preferences(session, user, changes)
        return _user_view(session, user)

    @router.get("/api/internal/push/topic")
    def internal_push_topic(
        username: str = Query(...),
        session: Session = Depends(get_session),
        _: None = Depends(require_internal),
    ):
        """Internal: the person's ntfy topic, created on first use."""
        user = _user_or_404(session, username)
        return {"username": user.username, "topic": ensure_push_topic(session, user)}

    @router.get("/api/internal/push/preferences")
    def internal_push_preferences(
        username: str = Query(...),
        session: Session = Depends(get_session),
        _: None = Depends(require_internal),
    ):
        """Internal: every category's on/off state for one person (telemetry)."""
        user = _user_or_404(session, username)
        return {
            "username": user.username,
            "is_admin": bool(user.is_admin),
            "preferences": effective_preferences(session, user),
        }

    @router.put("/api/internal/push/preferences")
    def internal_put_push_preferences(
        username: str = Query(...),
        body: dict = Body(...),
        session: Session = Depends(get_session),
        _: None = Depends(require_internal),
    ):
        """Internal: change one person's switches (geo moving the old
        per-person presence alerts setting over to family_presence)."""
        user = _user_or_404(session, username)
        apply_preferences(session, user, body)
        return {"username": user.username, "preferences": effective_preferences(session, user)}

    @router.get("/api/internal/push/admins")
    def internal_push_admins(
        session: Session = Depends(get_session),
        _: None = Depends(require_internal),
    ):
        """Internal: usernames a ``"user": "@admins"`` notice fans out to."""
        rows = session.exec(select(User).where(User.is_admin == True)).all()  # noqa: E712
        return {"admins": sorted(u.username for u in rows)}

    @router.post("/api/internal/push/up/register")
    def internal_up_register(
        body: dict = Body(...),
        session: Session = Depends(get_session),
        _: None = Depends(require_internal),
    ):
        """Internal: register one app on one device; the same registration
        again returns its existing topic."""
        username, device_id, app, token = _registration_fields(body)
        user = _user_or_404(session, username)
        mine = select(UnifiedPushRegistration).where(
            UnifiedPushRegistration.username == user.username,
            UnifiedPushRegistration.device_id == device_id,
        )
        existing = session.exec(
            mine.where(UnifiedPushRegistration.app == app, UnifiedPushRegistration.token == token)
        ).first()
        if existing:
            return {"status": "SUCCESS", **_registration_read(existing)}
        if len(session.exec(mine).all()) >= MAX_REGISTRATIONS_PER_DEVICE:
            raise HTTPException(status_code=409, detail="Too many registrations on this device")
        row = UnifiedPushRegistration(
            username=user.username, device_id=device_id, app=app, token=token, topic=new_up_topic()
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return {"status": "SUCCESS", **_registration_read(row)}

    @router.post("/api/internal/push/up/unregister")
    def internal_up_unregister(
        body: dict = Body(...),
        session: Session = Depends(get_session),
        _: None = Depends(require_internal),
    ):
        """Internal: remove one registration (one app on one device). Without
        a device_id (the web settings page) it is found by app and token on
        any of the person's devices."""
        username, device_id, app, token = _registration_fields(body, device_required=False)
        query = select(UnifiedPushRegistration).where(
            UnifiedPushRegistration.username == username,
            UnifiedPushRegistration.app == app,
            UnifiedPushRegistration.token == token,
        )
        if device_id:
            query = query.where(UnifiedPushRegistration.device_id == device_id)
        rows = session.exec(query).all()
        for row in rows:
            session.delete(row)
        session.commit()
        return {"status": "SUCCESS", "removed": len(rows)}

    @router.get("/api/internal/push/up")
    def internal_up_list(
        username: str = Query(...),
        device_id: str | None = Query(default=None),
        session: Session = Depends(get_session),
        _: None = Depends(require_internal),
    ):
        """Internal: a person's registrations, optionally only one device's."""
        query = select(UnifiedPushRegistration).where(UnifiedPushRegistration.username == username)
        if device_id:
            query = query.where(UnifiedPushRegistration.device_id == device_id)
        rows = session.exec(query.order_by(UnifiedPushRegistration.id)).all()
        return {"status": "SUCCESS", "registrations": [_registration_read(r) for r in rows]}

    return router

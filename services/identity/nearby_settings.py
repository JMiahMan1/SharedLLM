"""Settings > Nearby & offline: which Bluetooth LE parts a person's Android
phones run.

The parts (PRs #210, #211, #212, #216):

* ``nearby``: Find family nearby (family phones notice each other).
* ``chat``: the offline family chat (messages phone to phone).
* ``offline``: offline mode (Voice Assistant requests through nearby phones,
  and the household's latest information shared between them).
* ``car``: car detection (which linked car a trip was in).
* ``mine``: my devices (a person's own phone and tablet keep their location,
  steps, watch, chat rooms and offline data in step over Bluetooth).

A grown member switches their own. A minor (``roles.MINOR_FAMILY_ROLES``)
cannot: their parents set them (the same people who manage their screen
time), and the parent link (offline lock, unlock and extra time from a parent,
and the Bluetooth-off reports) is always on for them, whatever the switches
say. The phone reads these each time the app opens (per request, never
cached at service start) and applies them natively.

The routes are built by ``build_router`` with main.py's own session, auth and
"may manage this minor" dependencies, so tests that override those cover these
routes too.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlmodel import Session, select

from services.identity import roles
from services.identity.models import User, UserNearbySetting

#: The switches, in the order the settings page shows them.
PARTS = ("nearby", "chat", "offline", "car", "mine")
#: What a person gets before choosing: car detection was always on once a car
#: was linked (PR #210), a person's own devices kept each other in step from the
#: start (PR #254), the rest were off until turned on.
DEFAULTS = {"nearby": False, "chat": False, "offline": False, "car": True, "mine": True}
#: Always on for a minor, not a switch: a parent's offline commands and the
#: Bluetooth-off reports (PR #216).
MINOR_ALWAYS_ON = ("parent_link",)


def is_minor(user: User) -> bool:
    return (user.family_role or "other") in roles.MINOR_FAMILY_ROLES


def stored(session: Session, username: str) -> dict[str, bool]:
    row = session.get(UserNearbySetting, username)
    try:
        data = json.loads(row.data) if row and row.data else {}
    except (TypeError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    return {p: bool(data[p]) if isinstance(data.get(p), bool) else DEFAULTS[p] for p in PARTS}


def clean(body: object) -> dict[str, bool]:
    """``{part: bool}`` for known parts only; anything else is a 422."""
    changes = body.get("settings") if isinstance(body, dict) and isinstance(body.get("settings"), dict) else body
    if not isinstance(changes, dict):
        raise HTTPException(status_code=422, detail="settings must be an object of part: true or false")
    out: dict[str, bool] = {}
    for key, value in changes.items():
        if key not in PARTS:
            raise HTTPException(status_code=422, detail=f"Unknown part: {key}")
        if not isinstance(value, bool):
            raise HTTPException(status_code=422, detail=f"{key} must be true or false")
        out[key] = value
    return out


def save(session: Session, username: str, changes: dict[str, bool], by: str) -> None:
    data = stored(session, username)
    data.update(changes)
    row = session.get(UserNearbySetting, username) or UserNearbySetting(username=username)
    row.data = json.dumps(data, sort_keys=True)
    row.updated_by = by
    row.updated_at = datetime.now(UTC).isoformat()
    session.add(row)
    session.commit()


def view(session: Session, subject: User, caller: User, may_manage: Callable[[User, User], bool]) -> dict:
    minor = is_minor(subject)
    return {
        "username": subject.username,
        "settings": stored(session, subject.username),
        # Whether anyone has saved a choice: until then the settings are the defaults, and a grown
        # member's phone keeps (and saves) what it already runs instead of taking them.
        "chosen": session.get(UserNearbySetting, subject.username) is not None,
        # A minor's parents set them; the minor sees them read-only.
        "managed_by_parent": minor,
        "can_change": (caller.username == subject.username and not minor)
        or (minor and caller.username != subject.username and may_manage(caller, subject)),
        "always_on": list(MINOR_ALWAYS_ON) if minor else [],
    }


def forget_user(session: Session, username: str) -> None:
    for row in session.exec(select(UserNearbySetting).where(UserNearbySetting.username == username)).all():
        session.delete(row)
    session.commit()


def build_router(
    get_session: Callable,
    require_api_key: Callable,
    may_manage: Callable[[User, User], bool],
) -> APIRouter:
    router = APIRouter()

    def _subject(session: Session, username: str) -> User:
        name = (username or "").strip().lower()
        user = session.exec(select(User).where(User.username == name)).first()
        if user is None:
            raise HTTPException(status_code=404, detail="No such member")
        return user

    def _may_read(caller: User, subject: User) -> bool:
        return caller.username == subject.username or (is_minor(subject) and may_manage(caller, subject))

    @router.get("/api/users/me/nearby-settings")
    def get_mine(session: Session = Depends(get_session), user: User = Depends(require_api_key)):
        """The caller's own parts (read-only for a minor)."""
        return view(session, user, user, may_manage)

    @router.put("/api/users/me/nearby-settings")
    def put_mine(
        body: dict = Body(...),
        session: Session = Depends(get_session),
        user: User = Depends(require_api_key),
    ):
        """Change some of the caller's own parts; a minor's parents set theirs."""
        changes = clean(body)
        if is_minor(user):
            raise HTTPException(status_code=403, detail="A parent manages these on a child's phone")
        save(session, user.username, changes, user.username)
        return view(session, user, user, may_manage)

    @router.get("/api/users/{username}/nearby-settings")
    def get_member(
        username: str,
        session: Session = Depends(get_session),
        caller: User = Depends(require_api_key),
    ):
        """A member's parts: their own, or a minor's for that minor's parents."""
        subject = _subject(session, username)
        if not _may_read(caller, subject):
            raise HTTPException(status_code=403, detail="Only this member or their parents can see this")
        return view(session, subject, caller, may_manage)

    @router.put("/api/users/{username}/nearby-settings")
    def put_member(
        username: str,
        body: dict = Body(...),
        session: Session = Depends(get_session),
        caller: User = Depends(require_api_key),
    ):
        """A grown member sets their own; a minor's parents set the minor's."""
        subject = _subject(session, username)
        changes = clean(body)
        if is_minor(subject):
            if caller.username == subject.username or not may_manage(caller, subject):
                raise HTTPException(status_code=403, detail="Only this child's parents can change these")
        elif caller.username != subject.username:
            raise HTTPException(status_code=403, detail="Grown members set these themselves")
        save(session, subject.username, changes, caller.username)
        return view(session, subject, caller, may_manage)

    return router

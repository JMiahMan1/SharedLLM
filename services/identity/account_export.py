"""The person's identity data for the account export (docs/ACCOUNT_EXPORT.md).

Internal-secret routes only; the gateway calls them on the person's behalf
after checking who may export or restore whom:

* ``GET  /api/internal/account/export/{username}``: the person's rows.
* ``POST /api/internal/account/import/{username}``: merge rows back
  (``dry_run`` first for the summary).
* ``GET  /api/internal/account/may-manage?actor=&subject=``: whether the
  actor may export or restore the subject (themselves, or an admin or
  household manager for anyone, as Admin > Users already allows).
* ``POST /api/internal/account/audit``: one line in the audit log for an
  export or a restore.

Never exported: the password hash, every API key and stored service password
or token (``*_enc``, ``*_hash``), linked-account tokens, push registrations,
the guard's offline code verifier, and the voice signature (a biometric
template; enrol again instead). Rules a parent set for a child (screen time,
recommendation lanes) are exported for reading but never restored, so a file
cannot loosen them.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlmodel import Session, select

from services.common.account_data import Subject, Table, export_tables, import_tables
from services.identity import roles
from services.identity.models import (
    AssistantConversation,
    AuditEvent,
    ChoreCheck,
    FeatureUsage,
    GuardianNote,
    GuardRequest,
    GuardUnlockRequest,
    RecommendationLanes,
    ScreenRules,
    ScreenTimeRequest,
    User,
    UserActivitySharing,
    UserAvatarImage,
    UserCalendarSetting,
    UserNotificationPreference,
    UserProfileSharing,
    UserThemeSetting,
    UserWeatherSetting,
    UserWidget,
    UserWorkspaceSession,
    WorkspaceChat,
    WorkspaceChatMessage,
)

#: The profile fields a file may set on restore; everything else on User is the server's.
PROFILE_RESTORABLE = frozenset({"display_name", "avatar", "preferred_tts_voice"})
#: The profile fields shown in an export (no credentials, no keys, no topics).
PROFILE_FIELDS = frozenset(
    {
        "id",
        "username",
        "display_name",
        "family_role",
        "mother_username",
        "birth_date",
        "avatar",
        "preferred_tts_voice",
        "nextcloud_url",
        "nextcloud_user",
        "github_url",
        "github_user",
        "gitlab_url",
        "gitlab_user",
        "audiobookshelf_url",
        "audiobookshelf_user",
        "mailcow_url",
        "mail_user",
        "mass_url",
        "skylight_url",
        "skylight_email",
        "skylight_enabled",
        "git_url",
        "git_user",
    }
)


def _profile_spec() -> Table:
    every = {c.key for c in User.__table__.columns}  # type: ignore[attr-defined]
    return Table(
        name="profile",
        model=User,
        owner="username",
        natural_key=("username",),
        exclude=frozenset(every - PROFILE_FIELDS),
        read_only=frozenset(PROFILE_FIELDS - PROFILE_RESTORABLE),
    )


def specs() -> list[Table]:
    """The identity tables in the export, in restore order (parents before children)."""
    return [
        _profile_spec(),
        Table("avatar_image", UserAvatarImage, owner="username", updated="updated_at"),
        Table("theme", UserThemeSetting, owner="username"),
        Table("weather", UserWeatherSetting, owner="username"),
        Table("calendar_settings", UserCalendarSetting, owner="username"),
        Table("notification_preferences", UserNotificationPreference, owner="username"),
        Table("activity_sharing", UserActivitySharing, owner="username"),
        Table("profile_sharing", UserProfileSharing, owner="username"),
        Table("dashboard_widgets", UserWidget, owner="username", natural_key=("widget_key",), updated="updated_at"),
        Table("workspace_sessions", UserWorkspaceSession, owner="username", natural_key=("workspace_id",), updated="updated_at"),
        Table("workspace_chats", WorkspaceChat, owner="username", updated="updated_at"),
        Table(
            "workspace_chat_messages", WorkspaceChatMessage, parent="workspace_chats", parent_fk="chat_id",
            natural_key=("created_at", "role", "parts"),
        ),
        Table("assistant_conversations", AssistantConversation, owner="username", exclude=frozenset({"device_key"})),
        Table("feature_usage", FeatureUsage, owner="username", export_only=True),
        Table("screen_rules", ScreenRules, owner="username", export_only=True),
        Table("recommendation_lanes", RecommendationLanes, owner="username", export_only=True),
        Table("screen_time_requests", ScreenTimeRequest, owner="username", export_only=True),
        Table("guard_requests", GuardRequest, owner="username", export_only=True),
        Table("unlock_requests", GuardUnlockRequest, owner="username", export_only=True, exclude=frozenset({"device_key"})),
        Table("chore_checks", ChoreCheck, owner="username", export_only=True),
        Table("guardian_notes_written", GuardianNote, owner="author", export_only=True),
        Table("account_history", AuditEvent, owner="target", export_only=True, exclude=frozenset({"client", "user_agent"})),
    ]


def build_router(
    get_session: Callable[..., Any],
    require_internal: Callable[..., Any],
    audit: Callable[..., None],
) -> APIRouter:
    router = APIRouter(prefix="/api/internal/account", dependencies=[Depends(require_internal)])

    def _subject(session: Session, username: str) -> tuple[User, Subject]:
        name = (username or "").strip().lower()
        user = session.exec(select(User).where(User.username == name)).first()
        if user is None:
            raise HTTPException(status_code=404, detail="No such person")
        return user, Subject(username=user.username, user_id=user.id)

    @router.get("/export/{username}")
    def export_account(username: str, session: Session = Depends(get_session)) -> dict[str, Any]:
        _, subject = _subject(session, username)
        return {"service": "identity", "collections": export_tables(session, specs(), subject)}

    @router.post("/import/{username}")
    def import_account(
        username: str,
        body: dict[str, Any] = Body(...),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        _, subject = _subject(session, username)
        collections = body.get("collections")
        if not isinstance(collections, dict):
            raise HTTPException(status_code=422, detail="collections must be an object")
        results = import_tables(
            session,
            specs(),
            subject,
            collections,
            dry_run=bool(body.get("dry_run", True)),
            replace_conflicts=bool(body.get("replace_conflicts", False)),
        )
        return {"service": "identity", "dry_run": bool(body.get("dry_run", True)), "collections": results}

    @router.get("/may-manage")
    def may_manage(actor: str, subject: str, session: Session = Depends(get_session)) -> dict[str, Any]:
        who = session.exec(select(User).where(User.username == actor.strip().lower())).first()
        whom = session.exec(select(User).where(User.username == subject.strip().lower())).first()
        if who is None or whom is None:
            return {"allowed": False, "reason": "no such person"}
        if who.username == whom.username:
            return {"allowed": True, "reason": "self"}
        if who.is_admin or roles.has_permission(session, who, "family.manage"):
            return {"allowed": True, "reason": "manager"}
        return {"allowed": False, "reason": "not a manager"}

    @router.post("/audit")
    def audit_account(request: Request, body: dict[str, Any] = Body(...), session: Session = Depends(get_session)) -> dict[str, Any]:
        action = str(body.get("action") or "")
        if action not in {"account.export", "account.import"}:
            raise HTTPException(status_code=422, detail="unknown action")
        actor = session.exec(select(User).where(User.username == str(body.get("actor") or "").strip().lower())).first()
        target = str(body.get("target") or "").strip().lower() or None
        note = str(body.get("note") or "")[:500] or None
        audit(request, session, action, target, actor=actor, changes=[], note=note)
        return {"ok": True}

    return router

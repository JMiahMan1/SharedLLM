"""The reader's Bible data for the account export (docs/ACCOUNT_EXPORT.md).

Mounted under ``/internal/account``, behind the service's internal-secret
middleware. Highlights keep only the path of their note: the note's text lives
in the person's Nextcloud notes, which are not part of this export. Plan
groups belong to whoever made them and their members, so membership is
exported for reading and never restored; a restored enrollment is not put
back into a group.
"""
from __future__ import annotations

from services.bible.models import (
    AchievementEarned,
    GameScore,
    MemoryVerse,
    PlanEnrollment,
    PlanGroupMember,
    PlanReflection,
    ReadingEvent,
    StudySession,
    StudySessionTurn,
    UserBibleState,
    VerseMark,
)
from services.common.account_data import Table


def specs() -> list[Table]:
    return [
        Table("reading_state", UserBibleState, owner="username"),
        Table("marks", VerseMark, owner="username", natural_key=("osis", "chapter", "verse_start", "kind", "created_at"), updated="updated_at"),
        Table("memory_verses", MemoryVerse, owner="username", natural_key=("ref", "created_at")),
        Table("plan_enrollments", PlanEnrollment, owner="username", natural_key=("plan_id", "started_on"), read_only=frozenset({"group_id"})),
        Table("plan_reflections", PlanReflection, owner="username", natural_key=("plan_id", "day"), updated="updated_at"),
        Table("reading_events", ReadingEvent, owner="username", natural_key=("kind", "day", "created_at")),
        Table("achievements", AchievementEarned, owner="username"),
        Table("game_scores", GameScore, owner="username", natural_key=("mode", "day", "created_at")),
        Table("study_sessions", StudySession, owner="username", natural_key=("created_at",), updated="updated_at"),
        Table("study_session_turns", StudySessionTurn, parent="study_sessions", parent_fk="session_id", natural_key=("created_at", "question")),
        Table("plan_groups_joined", PlanGroupMember, owner="username", export_only=True),
    ]

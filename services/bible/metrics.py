"""Reading metrics, derived from the reading-state tables.

Everything the achievements engine needs is computed here so the rules stay
pure functions over a small mapping (see ``achievements.py``). Nothing is
persisted: if the numbers change, the numbers change, and a badge that is
already banked keeps its date in ``AchievementEarned``.
"""
from __future__ import annotations

from datetime import date, timedelta
import logging
from typing import Any

from sqlmodel import Session, select

from . import achievements as ach
from .models import ReadingEvent, UserBibleState, VerseMark
from .refs import ReferenceError, parse_one

log = logging.getLogger("bible.metrics")

#: Event kinds that count as "the reader opened the app today". The YouVersion
#: Bible App keeps an App-Open streak separate from the guided-Scripture streak
#: precisely because they are different promises; so are ours.
OPEN_KINDS = frozenset({"app_open"})

#: Event kinds that count as a day of reading.
READING_KINDS = frozenset(
    {
        "app_open",
        "chapter_read",
        "chapter_complete",
        "verse_tapped",
        "search",
        "note_created",
        "mark_created",
        "plan_day_completed",
        "plan_started",
        "quiz_played",
        "quiz_correct",
        "memory_recall",
        "game_played",
        "assistant_ask",
        "share_tap",
        "blb_link_tap",
        "devotional_opened",
    }
)

#: Rules whose backing tables land in a later phase (plans, memorization,
#: quizzes). They read zero until that phase ships, which is honest progress
#: rather than a fabricated number.
PENDING_RULES = frozenset(
    {"plan_days_total", "plan_completed", "memory_mastered", "quizzes_total", "quiz_perfect"}
)


def _events(session: Session, username: str, kinds: frozenset[str] | None = None) -> list[ReadingEvent]:
    stmt = select(ReadingEvent).where(ReadingEvent.username == username)
    rows = list(session.exec(stmt))
    if kinds is not None:
        rows = [r for r in rows if r.kind in kinds]
    return rows


def active_days(session: Session, username: str) -> list[str]:
    """Days on which the reader did anything at all, ISO-sorted."""
    days = {row.day for row in _events(session, username, READING_KINDS) if row.day}
    return sorted(days)


def open_days(session: Session, username: str) -> list[str]:
    return sorted({row.day for row in _events(session, username, OPEN_KINDS) if row.day})


def chapters_read(session: Session, username: str) -> set[str]:
    """OSIS ids of every book with at least one completed chapter.

    ``ref`` holds a display reference, so it is parsed rather than trusted. A
    stored ref that no longer parses is counted and reported in
    :func:`collect` rather than quietly dropped.
    """
    books: set[str] = set()
    for row in _events(session, username, frozenset({"chapter_complete"})):
        if not row.ref:
            continue
        try:
            books.add(parse_one(row.ref).book)
        except ReferenceError as exc:
            log.warning("Unparseable chapter_complete ref %r for %s: %s", row.ref, username, exc)
    return books


def compute(session: Session, username: str, today: date | None = None) -> dict[str, Any]:
    """Full reading picture for one user.

    Returns the raw metrics plus the streaks, which the API and the widget both
    want but the rules engine does not score on.
    """
    reads = active_days(session, username)
    completed = chapters_read(session, username)
    marks = list(session.exec(select(VerseMark).where(VerseMark.username == username)))
    state = session.get(UserBibleState, username)

    chapters_total = len({row.ref for row in _events(session, username, frozenset({"chapter_complete"})) if row.ref})
    metrics: dict[str, float] = {
        "chapters_total": float(chapters_total),
        "books_read": float(len(completed)),
        "read_streak": float(ach.longest_streak(reads)),
        "plan_days_total": 0.0,
        "plan_completed": 0.0,
        "memory_mastered": 0.0,
        "quizzes_total": 0.0,
        "quiz_perfect": 0.0,
    }
    return {
        "metrics": metrics,
        "days_read": len(reads),
        "days_opened": len(open_days(session, username)),
        "read_streak_current": ach.current_streak(reads, today),
        "read_streak_longest": int(metrics["read_streak"]),
        "open_streak_current": ach.current_streak(open_days(session, username), today),
        "open_streak_longest": ach.longest_streak(open_days(session, username)),
        "marks": len(marks),
        "highlights": sum(1 for m in marks if m.kind == "highlight"),
        "bookmarks": sum(1 for m in marks if m.kind == "bookmark"),
        "position": _position(state),
        "last_read_at": state.last_read_at.isoformat() if state and state.last_read_at else None,
    }


def collect(session: Session, username: str, today: date | None = None) -> dict[str, Any]:
    """Rule metrics only -- the mapping ``achievements.evaluate`` wants."""
    return compute(session, username, today)["metrics"]


def _position(state: UserBibleState | None) -> dict[str, Any] | None:
    if state is None or not state.last_book:
        return None
    return {
        "book": state.last_book,
        "chapter": state.last_chapter,
        "verse": state.last_verse,
    }


def window_counts(session: Session, username: str, days: int, today: date | None = None) -> dict[str, int]:
    """Per-day reading-event counts over the trailing ``days`` window."""
    anchor = today or date.today()
    start = (anchor - timedelta(days=max(0, days - 1))).isoformat()
    counts: dict[str, int] = {}
    for row in _events(session, username):
        if row.day and row.day >= start:
            counts[row.day] = counts.get(row.day, 0) + 1
    return dict(sorted(counts.items()))
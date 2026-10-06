"""Verse of the Day.

Deterministic: the same date and version always yields the same verse, on every
device, with no stored state to migrate or re-seed. That matters because the
verse appears in three places at once -- the dashboard widget, the Android home
screen, and the reading app's home section -- and they must agree without
talking to each other.

``seed`` lets an operator rotate the draw without changing the date (settings
live in Identity, not here, so nothing is hardcoded).
"""
from __future__ import annotations

from datetime import date
import hashlib

from sqlmodel import Session, select

from . import books as book_table
from . import corpus
from .models import BibleBook, BibleVerse, BibleVersion

#: Old Testament book count, for the ``scope`` filter.
_OT_COUNT = 39


def _digest(version: str, doy: int, seed: str, scope: str) -> int:
    raw = f"{version}|{doy}|{seed}|{scope}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(raw).digest()[:8], "big")


def _versions(session: Session) -> list[str]:
    rows = session.exec(select(BibleVersion.code).order_by(BibleVersion.code)).all()
    return [str(code) for code in rows]


def _pick_version(session: Session, requested: str | None) -> str:
    """Resolve the version to draw from.

    An explicitly requested version must exist -- a typo must not silently draw
    from a different translation than the one the reader is reading in.
    """
    available = _versions(session)
    if not available:
        raise LookupError(
            "No Bible text is installed on this server, so there is no verse of "
            "the day to draw. An administrator needs to load a translation in "
            "Admin > Bible."
        )
    if requested:
        if requested not in available:
            raise LookupError(
                f"Bible version {requested!r} is not imported. Available: {', '.join(available)}."
            )
        return requested
    return corpus.default_version_code(session) or available[0]


def _pool(session: Session, version: str, scope: str) -> list[tuple[str, int, int]]:
    """Ordered verse keys for the version, optionally limited to a testament."""
    stmt = (
        select(BibleVerse.osis, BibleVerse.chapter, BibleVerse.verse)
        .where(BibleVerse.version_code == version)
        .order_by(BibleVerse.osis, BibleVerse.chapter, BibleVerse.verse)
    )
    keys = [(str(osis), int(chapter), int(verse)) for osis, chapter, verse in session.exec(stmt)]
    if scope == "ot":
        keys = [k for k in keys if _osis_order(k[0]) <= _OT_COUNT]
    elif scope == "nt":
        keys = [k for k in keys if _osis_order(k[0]) > _OT_COUNT]
    if not keys:
        raise LookupError(
            f"Bible version {version!r} has no verses in scope {scope!r}. Import a full "
            "translation, or pick a different scope."
        )
    return keys


def _osis_order(osis: str) -> int:
    return book_table.BOOK_BY_OSIS.get(osis, {}).get("order", 999)


def pick(
    session: Session,
    *,
    day: date | None = None,
    version: str | None = None,
    scope: str = "all",
    seed: str = "",
) -> dict:
    """Return today's verse.

    ``scope`` is ``all`` | ``ot`` | ``nt``. An unknown scope is an error rather
    than a quiet fallback to ``all``.
    """
    if scope not in ("all", "ot", "nt"):
        raise ValueError(f"Unknown verse-of-the-day scope {scope!r}; expected all, ot or nt.")
    when = day or date.today()
    resolved = _pick_version(session, version)
    keys = _pool(session, resolved, scope)
    index = _digest(resolved, when.timetuple().tm_yday, seed, scope) % len(keys)
    osis, chapter, verse = keys[index]
    row = session.get(BibleVerse, {"version_code": resolved, "osis": osis, "chapter": chapter, "verse": verse})
    if row is None:  # pragma: no cover - keys came from these very rows
        raise LookupError(f"Verse {resolved} {osis} {chapter}:{verse} vanished mid-read.")
    name = _book_name(session, resolved, osis)
    return {
        "day": when.isoformat(),
        "day_of_year": when.timetuple().tm_yday,
        "version": resolved,
        "scope": scope,
        "osis": osis,
        "book": osis,
        "book_name": name,
        "chapter": chapter,
        "verse": verse,
        "reference": f"{name} {chapter}:{verse}",
        "text": row.text,
    }


def _book_name(session: Session, version: str, osis: str) -> str:
    row = session.exec(
        select(BibleBook.name).where(BibleBook.version_code == version, BibleBook.osis == osis)
    ).first()
    if row:
        return str(row)
    entry = book_table.BOOK_BY_OSIS.get(osis)
    return str(entry["name"]) if entry else osis
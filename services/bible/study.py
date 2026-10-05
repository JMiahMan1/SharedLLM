"""Store and serve the study notes that ship beside the Bible text.

A study Bible has two independent layers: the text people read and the notes
that explain it. This module owns the second layer. It never stores verse text,
never guesses which verse a note belongs to, and reports what it could not
resolve rather than quietly dropping the note.

Two axes decide which notes a reader sees, and both are explicit:

* **edition** -- which study Bible explains the text. Several editions of one
  translation coexist because their commentary differs; asking for one is not a
  fallback for the other.
* **cross_version** -- whether notes published against a *different* translation
  are shown alongside the ones written for this one. Off by default, because a
  commentary written for the NIV is not a commentary on the NKJV wording, and
  presenting it without saying so would be misleading. When it is on, every note
  is labelled with the translation and edition it belongs to.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlmodel import Session, delete, select

from services.bible.books import BOOK_BY_OSIS, book
from services.bible.corpus import (
    default_edition,
    ensure_default_edition,
    require_version,
)
from services.bible.models import BibleEdition, BibleVersion, StudyNote
from services.bible.refs import VerseSpan

NOTE_KINDS = ("commentary", "footnote", "introduction", "heading")
DEFAULT_KINDS = ("commentary", "footnote")

_SOURCE_MAX = 200
_BODY_MAX = 20000


class StudyNoteError(ValueError):
    """Raised when a note cannot be stored or a verse has no position."""


@dataclass(frozen=True)
class StudyNoteRecord:
    """One stored note, ready to be serialised.

    Carries the translation and edition it came from even when the reader
    obviously knows, because ``cross_version`` makes those two fields the
    difference between a labelled commentary and a misleading one.
    """

    osis: str
    book_name: str
    chapter: int
    verse: int
    kind: str
    ordinal: int
    body: str
    source: str
    version_code: str
    edition_code: str
    version_name: str
    edition_name: str

    def as_dict(self) -> dict:
        return {
            "osis": self.osis,
            "book": self.book_name,
            "chapter": self.chapter,
            "verse": self.verse,
            "reference": _reference(self.book_name, self.chapter, self.verse),
            "kind": self.kind,
            "ordinal": self.ordinal,
            "body": self.body,
            "source": self.source,
            "version": self.version_code,
            "version_name": self.version_name,
            "edition": self.edition_code,
            "edition_name": self.edition_name,
        }

    @property
    def identity(self) -> tuple[str, str, str, int, int, str, int]:
        """What makes this note distinct from every other note.

        Includes the translation and the edition, so two study Bibles covering
        the same verse are two notes rather than one note shown twice.
        """
        return (
            self.version_code,
            self.edition_code,
            self.osis,
            self.chapter,
            self.verse,
            self.kind,
            self.ordinal,
        )


def _reference(book_name: str, chapter: int, verse: int) -> str:
    return book_name if not chapter else f"{book_name} {chapter}" if not verse else f"{book_name} {chapter}:{verse}"


def available_kinds(
    session: Session,
    version: str,
    *,
    edition: str | None = None,
    cross_version: bool = False,
) -> dict[str, int]:
    """How many notes of each kind are installed, so callers can explain gaps."""
    require_version(session, version)
    resolved = _edition_filter(session, version, edition, cross_version)
    rows = session.exec(select(StudyNote.kind, StudyNote.id).where(*resolved)).all()
    counts: dict[str, int] = {}
    for kind, _id in rows:
        counts[kind] = counts.get(kind, 0) + 1
    return dict(sorted(counts.items()))


def editions_with_notes(session: Session, version: str) -> list[dict]:
    """Every installed edition of ``version`` with a note count, richest first.

    The picker needs all of them even when the reader has chosen one, because
    switching study Bibles is a normal thing to want mid-session.
    """
    from services.bible.corpus import list_editions

    rows = session.exec(
        select(StudyNote.edition_code, StudyNote.id).where(
            StudyNote.version_code == version
        )
    ).all()
    counts: dict[str, int] = {}
    for edition_code, _id in rows:
        counts[edition_code] = counts.get(edition_code, 0) + 1
    entries = []
    for entry in list_editions(session, version):
        entry["note_count"] = counts.get(entry["code"], 0)
        entries.append(entry)
    return entries


def versions_with_notes(session: Session, *, exclude: str = "") -> list[dict]:
    """Which translations on this install carry any study material at all.

    This is what makes ``cross_version`` honest: the toggle can say how many
    other translations would contribute, and name them, instead of appearing to
    do nothing.
    """
    rows = session.exec(
        select(StudyNote.version_code, StudyNote.edition_code, StudyNote.id)
    ).all()
    counts: dict[tuple[str, str], int] = {}
    for version_code, edition_code, _id in rows:
        key = (version_code, edition_code)
        counts[key] = counts.get(key, 0) + 1
    if not counts:
        return []
    editions = {
        row.code: (row.version_code, row.name)
        for row in session.exec(select(BibleEdition)).all()
    }
    names = {row.code: row.name for row in session.exec(select(BibleVersion)).all()}
    out: list[dict] = []
    for (version_code, edition_code), count in sorted(counts.items()):
        if version_code == exclude:
            continue
        out.append(
            {
                "version": version_code,
                "version_name": names.get(version_code, version_code),
                "edition": edition_code,
                "edition_name": editions.get(edition_code, (version_code, edition_code))[1],
                "note_count": count,
            }
        )
    return out


def _edition_filter(
    session: Session,
    version: str,
    edition: str | None,
    cross_version: bool,
) -> list:
    """Build the WHERE clauses that pick the notes a reader asked for.

    Two independent choices are encoded here so every read path agrees:
    * which study Bible (``edition``), and
    * whether other translations may contribute (``cross_version``).
    """
    clauses = []
    if cross_version:
        return clauses
    resolved = edition or default_edition(session, version)
    clauses.append(StudyNote.version_code == version)
    if resolved:
        clauses.append(StudyNote.edition_code == resolved)
    return clauses


def has_notes(
    session: Session,
    version: str,
    *,
    edition: str | None = None,
    cross_version: bool = False,
) -> bool:
    """Whether any study material is installed for this choice."""
    require_version(session, version)
    clauses = _edition_filter(session, version, edition, cross_version)
    return bool(session.exec(select(StudyNote.id).where(*clauses).limit(1)).first())


def import_notes(
    session: Session,
    version: str,
    notes,
    *,
    source: str,
    edition: str | None = None,
    name: str = "",
    publisher: str = "",
    license_class: str = "licensed",
    rights_holder: str = "",
) -> dict:
    """Replace the study notes for one study Bible of one translation.

    ``notes`` is anything iterable of :class:`services.bible.epub_import.StudyNote`
    or of dicts with ``book``/``chapter``/``verse``/``kind``/``body``. Replacing
    rather than merging keeps a re-import from duplicating notes.

    The delete is scoped to ``(version_code, edition_code)``. That is the whole
    point of the edition axis: re-importing one study Bible must not silently
    remove the commentary from another one over the same translation.
    """
    require_version(session, version)
    if not source:
        raise StudyNoteError("study notes need a source name so a reader can tell where they came from")
    edition_code = str(edition or "").strip().lower() or ensure_default_edition(session, version)
    prepared: list[StudyNote] = []
    seen: set[tuple[str, int, int, str, int]] = set()
    skipped = 0
    for raw in notes:
        record = _normalise(raw)
        if record is None:
            skipped += 1
            continue
        key = (record["osis"], record["chapter"], record["verse"], record["kind"], record["ordinal"])
        if key in seen:
            record["ordinal"] = seen_key_fix(seen, key)
            key = (record["osis"], record["chapter"], record["verse"], record["kind"], record["ordinal"])
            if key in seen:
                skipped += 1
                continue
        seen.add(key)
        declared = record.pop("declared_edition")
        if declared and declared != edition_code:
            # Silently filing MacArthur's commentary under Nelson's would be worse
            # than refusing: the reader would never know whose notes they are.
            raise StudyNoteError(
                f"note {_reference(BOOK_BY_OSIS[record['osis']]['name'], record['chapter'], record['verse'])} "
                f"declares study edition {declared!r} but is being imported as "
                f"{edition_code!r}; pass the right edition instead"
            )
        prepared.append(StudyNote(
            version_code=version,
            edition_code=edition_code,
            osis=record["osis"],
            chapter=record["chapter"],
            verse=record["verse"],
            kind=record["kind"],
            ordinal=record["ordinal"],
            body=record["body"],
            source=source[:_SOURCE_MAX],
        ))

    session.exec(
        delete(StudyNote).where(
            StudyNote.version_code == version,
            StudyNote.edition_code == edition_code,
        )
    )
    for record in prepared:
        session.add(record)
    session.commit()

    kinds = {
        kind: sum(1 for r in prepared if r.kind == kind)
        for kind in sorted({r.kind for r in prepared})
    }
    from services.bible.corpus import record_edition_notes, register_edition

    register_edition(
        session,
        code=edition_code,
        version=version,
        name=name or f"{edition_code} study notes",
        publisher=publisher,
        license_class=license_class,
        rights_holder=rights_holder,
        note_count=len(prepared),
        note_kinds=",".join(kinds),
    )
    record_edition_notes(session, edition_code, len(prepared), kinds)
    return {
        "version": version,
        "edition": edition_code,
        "source": source[:_SOURCE_MAX],
        "imported": len(prepared),
        "skipped": skipped,
        "kinds": kinds,
    }


def seen_key_fix(seen: set[tuple[str, int, int, str, int]], key: tuple[str, int, int, str, int]) -> int:
    """Find the next free ordinal so a duplicate never overwrites a real note."""
    osis, chapter, verse, kind, ordinal = key
    while (osis, chapter, verse, kind, ordinal) in seen:
        ordinal += 1
    return ordinal


def _normalise(raw) -> dict | None:
    if hasattr(raw, "as_dict"):
        raw = raw.as_dict()
    if not isinstance(raw, dict):
        return None
    body = str(raw.get("body") or "").strip()
    if not body:
        return None
    kind = str(raw.get("kind") or "").strip() or "commentary"
    if kind not in NOTE_KINDS:
        return None
    osis = _osis(raw.get("osis") or raw.get("book"))
    if osis is None:
        return None
    chapter = _number(raw.get("chapter"), minimum=0)
    verse = _number(raw.get("verse"), minimum=0)
    if chapter is None or verse is None:
        return None
    return {
        "osis": osis,
        "chapter": chapter,
        "verse": verse,
        "kind": kind,
        "ordinal": _number(raw.get("ordinal"), minimum=0) or 0,
        "body": body[:_BODY_MAX],
        # Carried through only so the caller can be told when a note claims a
        # different study Bible than the one being imported.
        "declared_edition": str(raw.get("edition_code") or "").strip().lower(),
    }


def _osis(value) -> str | None:
    from services.bible.books import resolve_book

    if isinstance(value, int):
        from services.bible.books import BOOKS

        if not 1 <= value <= len(BOOKS):
            return None
        return BOOKS[value - 1]["osis"]
    if isinstance(value, str) and value.strip():
        return resolve_book(value)
    return None


def _number(value, *, minimum: int) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= minimum else None


def notes_for_span(
    session: Session,
    version: str,
    span: VerseSpan,
    *,
    kinds: tuple[str, ...] = DEFAULT_KINDS,
    edition: str | None = None,
    cross_version: bool = False,
) -> list[StudyNoteRecord]:
    """Every stored note for a passage, chapter notes first.

    With ``cross_version=True`` the translation filter is dropped so notes
    written against another translation of the same passage come back too. They
    are labelled, never merged, because they are not commentary on the words the
    reader is looking at.
    """
    require_version(session, version)
    clauses = _edition_filter(session, version, edition, cross_version)
    rows = session.exec(
        select(StudyNote)
        .where(*clauses)
        .where(StudyNote.osis == span.book)
        .where(StudyNote.chapter >= span.chapter_start)
        .where(StudyNote.chapter <= span.resolved_chapter_end)
        .order_by(StudyNote.chapter, StudyNote.verse, StudyNote.kind, StudyNote.ordinal)
    ).all()
    wanted = set(kinds)
    names = _name_index(session)
    records: list[StudyNoteRecord] = []
    seen: set[tuple] = set()
    for row in rows:
        if row.kind not in wanted:
            continue
        if row.verse == 0:
            continue
        if row.chapter == span.chapter_start and span.verse_end is not None:
            if not span.verse_start <= row.verse <= span.verse_end:
                continue
        record = _record(row, names)
        if record.identity in seen:
            continue
        seen.add(record.identity)
        records.append(record)
    return records


def notes_for_verse(
    session: Session,
    version: str,
    osis: str,
    chapter: int,
    verse: int,
    *,
    kinds: tuple[str, ...] = DEFAULT_KINDS,
    edition: str | None = None,
    cross_version: bool = False,
) -> list[StudyNoteRecord]:
    """Every stored note for a single verse, plus that chapter's own material."""
    require_version(session, version)
    clauses = _edition_filter(session, version, edition, cross_version)
    rows = session.exec(
        select(StudyNote)
        .where(*clauses)
        .where(StudyNote.osis == osis)
        .where(StudyNote.chapter == chapter)
        .where(StudyNote.kind.in_(list(kinds)))
        .order_by(StudyNote.verse, StudyNote.kind, StudyNote.ordinal)
    ).all()
    names = _name_index(session)
    records: list[StudyNoteRecord] = []
    seen: set[tuple] = set()
    for row in rows:
        if row.verse not in (0, verse):
            continue
        record = _record(row, names)
        if record.identity in seen:
            continue
        seen.add(record.identity)
        records.append(record)
    return records


def _name_index(session: Session) -> dict[str, tuple[str, str, str]]:
    """Edition code -> (version code, version name, edition name).

    One query for the whole page rather than one per note: the panel renders
    every note for a verse and a per-note lookup would make that N+1 on the
    reader's tap.
    """
    index: dict[str, tuple[str, str, str]] = {}
    version_names: dict[str, str] = {}
    for row in session.exec(select(BibleVersion.code, BibleVersion.name)).all():
        version_names[str(row[0])] = str(row[1])
    for row in session.exec(select(BibleEdition)).all():
        index[row.code] = (
            row.version_code,
            version_names.get(row.version_code, row.version_code),
            row.name,
        )
    return index


def _record(row: StudyNote, names: dict[str, tuple[str, str, str]] | None = None) -> StudyNoteRecord:
    try:
        name = book(row.osis)["name"]
    except KeyError:
        name = BOOK_BY_OSIS.get(row.osis, {}).get("name", row.osis)
    edition = (names or {}).get(row.edition_code)
    if edition:
        version_code, version_name, edition_name = edition
    else:
        version_code, version_name = row.version_code, row.version_code
        edition_name = row.edition_code or row.version_code
    return StudyNoteRecord(
        osis=row.osis,
        book_name=name,
        chapter=row.chapter,
        verse=row.verse,
        kind=row.kind,
        ordinal=row.ordinal,
        body=row.body,
        source=row.source,
        version_code=version_code,
        edition_code=row.edition_code or row.version_code,
        version_name=version_name,
        edition_name=edition_name,
    )
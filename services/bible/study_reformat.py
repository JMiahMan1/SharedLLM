"""Store a normalised book, and re-process an existing one without losing anyone's place.

Storing is the same for a first import and a reformat: the sections are
replaced, and the old ones are mapped to the new ones so a reading position
(an ordinal, a section id, a reminder link) made before still lands on the
same words. The map is kept in ``ImportedBookAlias``.

A reformat is always previewed first. :func:`preview` reads the stored file
again and reports what would change, without writing; :func:`apply` writes it
only if the result is still what was previewed.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass

from sqlmodel import Session, select

from services.bible.models import (
    ImportedBook,
    ImportedBookAlias,
    ImportedBookAsset,
    ImportedBookChapter,
    UserBibleState,
    utcnow,
)
from services.bible.study_blocks import (
    FORMAT_VERSION,
    Block,
    Document,
    section_questions,
    section_text,
)

_WORD = re.compile(r"[a-z0-9]+")
PROBE_WORDS = 8


@dataclass
class OldSection:
    ordinal: int
    section_id: str
    label: str
    text: str


def old_sections(session: Session, book_id: int) -> list[OldSection]:
    rows = session.exec(
        select(ImportedBookChapter)
        .where(ImportedBookChapter.book_id == book_id)
        .order_by(ImportedBookChapter.ordinal)
    ).all()
    return [OldSection(r.ordinal, r.section_id or "", r.label or "", r.text or "") for r in rows]


def _words(text: str) -> list[str]:
    # A word split over a line in the old text is one word in the new one.
    text = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", text or "")
    return _WORD.findall(text.lower())


def fingerprint(document: Document) -> str:
    joined = "\n".join(section.id + "\n" + section_text(section) for section in document.sections)
    return hashlib.sha256(joined.encode()).hexdigest()[:24]


def map_sections(previous: list[OldSection], document: Document) -> list[dict]:
    """Where each old section's words are now.

    Probes of a few consecutive words from the old section are looked for in
    the new book's word stream; the section holding the first hit is where the
    reader goes. Running heads and page numbers are exactly what the new book
    no longer has, so probes are taken from inside the old text, not its first
    line. A section whose words cannot be found falls back to the same relative
    position in the book.
    """
    starts: list[int] = []
    stream: list[str] = []
    for section in document.sections:
        starts.append(len(stream))
        stream.extend(_words(section_text(section)))
    joined = " ".join(stream)
    # Character offset of each word, to turn a string hit into a word index.
    offsets: list[int] = []
    position = 0
    for word in stream:
        offsets.append(position)
        position += len(word) + 1

    def section_at(word_index: int) -> int:
        chosen = 0
        for index, start in enumerate(starts):
            if start <= word_index:
                chosen = index
        return chosen

    import bisect

    total_old = sum(len(_words(p.text)) for p in previous) or 1
    seen_old = 0
    # Reading order survives a reformat, so each old section is looked for
    # after the place the one before it was found.
    cursor = 0
    out: list[dict] = []
    for old in previous:
        words = _words(old.text)
        hit: int | None = None
        if len(words) >= PROBE_WORDS and joined:
            for fraction in (0.15, 0.4, 0.7, 0.0):
                start = int((len(words) - PROBE_WORDS) * fraction)
                probe = " ".join(words[start:start + PROBE_WORDS])
                found = joined.find(probe, cursor)
                if found < 0:
                    found = joined.find(probe)
                if found >= 0:
                    cursor = max(cursor, found)
                    hit = bisect.bisect_right(offsets, found) - 1
                    break
        if hit is not None:
            target = section_at(hit)
            how = "text"
        else:
            target = section_at(int(len(stream) * seen_old / total_old)) if stream else 0
            how = "position"
        seen_old += len(words)
        section = document.sections[target]
        out.append({
            "old_ordinal": old.ordinal,
            "old_section_id": old.section_id,
            "old_label": old.label,
            "ordinal": target + 1,
            "section_id": section.id,
            "label": section.title,
            "matched_by": how,
        })
    return out


def retained_share(previous: list[OldSection], document: Document) -> float:
    """How much of the old wording is still there, as a share of 4-word runs.

    Removing running heads and page numbers lowers it a little; a reader that
    lost a page would lower it a lot. Never invented: the new text cannot
    raise it.
    """
    def shingles(words: list[str]) -> set[str]:
        return {" ".join(words[i:i + 4]) for i in range(max(0, len(words) - 3))}

    old = shingles([w for p in previous for w in _words(p.text) if not w.isdigit()])
    new = shingles([w for s in document.sections for w in _words(section_text(s)) if not w.isdigit()])
    if not old:
        return 1.0
    return round(len(old & new) / len(old), 4)


def store(
    session: Session,
    book: ImportedBook,
    document: Document,
    *,
    previous: list[OldSection] | None = None,
) -> list[dict]:
    """Replace a book's sections with ``document``'s; returns the old-to-new map."""
    assert book.id is not None
    previous = previous if previous is not None else old_sections(session, book.id)
    mapping = map_sections(previous, document) if previous else []

    for row in session.exec(select(ImportedBookChapter).where(ImportedBookChapter.book_id == book.id)).all():
        session.delete(row)
    for row in session.exec(select(ImportedBookAsset).where(ImportedBookAsset.book_id == book.id)).all():
        session.delete(row)
    session.flush()

    for ordinal, section in enumerate(document.sections, start=1):
        session.add(
            ImportedBookChapter(
                book_id=book.id,
                ordinal=ordinal,
                label=section.title[:300],
                text=section_text(section),
                questions=json.dumps(section_questions(section)),
                section_id=section.id,
                blocks=json.dumps([block.as_dict() for block in section.blocks]),
            )
        )
    for asset in document.assets:
        session.add(ImportedBookAsset(book_id=book.id, key=asset.key, mime=asset.mime, data=asset.data))

    if mapping:
        _record_aliases(session, book.id, mapping)
        _move_positions(session, book.id, mapping)

    book.chapter_count = len(document.sections)
    book.format_version = FORMAT_VERSION
    book.import_report = json.dumps(document.report)
    session.add(book)
    return mapping


def _record_aliases(session: Session, book_id: int, mapping: list[dict]) -> None:
    by_old_id = {m["old_section_id"]: m for m in mapping if m["old_section_id"]}
    by_old_ordinal = {m["old_ordinal"]: m for m in mapping}
    # Earlier aliases point at sections that are now themselves old: follow them on.
    for alias in session.exec(select(ImportedBookAlias).where(ImportedBookAlias.book_id == book_id)).all():
        moved = by_old_id.get(alias.section_id) or by_old_ordinal.get(alias.ordinal)
        if moved:
            alias.section_id = moved["section_id"]
            alias.ordinal = moved["ordinal"]
            session.add(alias)
    for entry in mapping:
        session.add(
            ImportedBookAlias(
                book_id=book_id,
                old_ordinal=entry["old_ordinal"],
                old_section_id=entry["old_section_id"],
                section_id=entry["section_id"],
                ordinal=entry["ordinal"],
                created_at=utcnow(),
            )
        )


def _move_positions(session: Session, book_id: int, mapping: list[dict]) -> int:
    """Readers whose saved place is in this book keep their place.

    The saved study position lives on ``UserBibleState`` when the reading
    position feature is installed; this checks for its fields rather than
    assuming them.
    """
    if not hasattr(UserBibleState, "last_study_book_id") or not hasattr(UserBibleState, "last_study_section_ordinal"):
        return 0
    by_old = {m["old_ordinal"]: m["ordinal"] for m in mapping}
    moved = 0
    rows = session.exec(
        select(UserBibleState).where(UserBibleState.last_study_book_id == book_id)  # type: ignore[attr-defined]
    ).all()
    for row in rows:
        old = int(getattr(row, "last_study_section_ordinal", 0) or 0)
        if old in by_old and by_old[old] != old:
            row.last_study_section_ordinal = by_old[old]  # type: ignore[attr-defined]
            session.add(row)
            moved += 1
    return moved


def aliases_for(session: Session, book_id: int) -> list[ImportedBookAlias]:
    return list(session.exec(select(ImportedBookAlias).where(ImportedBookAlias.book_id == book_id)).all())


def resolve(session: Session, book_id: int, *, ordinal: int | None = None, section: str = "", legacy: bool = False) -> ImportedBookChapter | None:
    """The section a locator names now: a section id, an ordinal, or an alias of either.

    ``legacy`` says the ordinal was saved before a reformat (an old client's
    stored page), so it is looked up among the aliases first.
    """
    rows = list(
        session.exec(
            select(ImportedBookChapter)
            .where(ImportedBookChapter.book_id == book_id)
            .order_by(ImportedBookChapter.ordinal)
        ).all()
    )
    if section:
        for row in rows:
            if row.section_id == section:
                return row
        alias = session.exec(
            select(ImportedBookAlias)
            .where(ImportedBookAlias.book_id == book_id, ImportedBookAlias.old_section_id == section)
            .order_by(ImportedBookAlias.id.desc())  # type: ignore[union-attr]
        ).first()
        if alias:
            return next((r for r in rows if r.section_id == alias.section_id), None)
    if ordinal is not None:
        if legacy:
            alias = session.exec(
                select(ImportedBookAlias)
                .where(ImportedBookAlias.book_id == book_id, ImportedBookAlias.old_ordinal == ordinal)
                .order_by(ImportedBookAlias.id)  # type: ignore[arg-type]
            ).first()
            if alias:
                return next((r for r in rows if r.section_id == alias.section_id), None)
        return next((r for r in rows if r.ordinal == ordinal), None)
    return None


def blocks_of(chapter: ImportedBookChapter) -> list[Block]:
    if not chapter.blocks:
        return []
    try:
        data = json.loads(chapter.blocks)
    except (TypeError, ValueError):
        return []
    return [Block.from_dict(item) for item in data if isinstance(item, dict)]


def sample(blocks: Iterable[Block], limit: int = 8) -> list[dict]:
    out = []
    for block in blocks:
        entry = block.as_dict()
        entry["text"] = entry.get("text", "")[:400]
        entry.pop("rows", None) if len(entry.get("rows", [])) > 6 else None
        out.append(entry)
        if len(out) >= limit:
            break
    return out


def preview(book: ImportedBook, previous: list[OldSection], document: Document) -> dict:
    mapping = map_sections(previous, document)
    first_old = next((p for p in previous if len(p.text) > 200), previous[0] if previous else None)
    target = next((m for m in mapping if first_old and m["old_ordinal"] == first_old.ordinal), None)
    new_section = (
        next((s for s in document.sections if s.id == target["section_id"]), None) if target else None
    ) or (document.sections[0] if document.sections else None)
    return {
        "book_id": book.id,
        "fingerprint": fingerprint(document),
        "before": {
            "format_version": int(book.format_version or 0),
            "sections": len(previous),
            "characters": sum(len(p.text) for p in previous),
            "labels": [p.label for p in previous[:12]],
            "sample": {"label": first_old.label, "text": first_old.text[:1200]} if first_old else None,
        },
        "after": {
            "format_version": FORMAT_VERSION,
            "sections": len(document.sections),
            "characters": sum(len(section_text(s)) for s in document.sections),
            "labels": [s.title for s in document.sections[:12]],
            "sample": {"label": new_section.title, "section_id": new_section.id, "blocks": sample(new_section.blocks)}
            if new_section
            else None,
            "report": document.report,
        },
        "retained": retained_share(previous, document),
        "mapping": mapping,
    }

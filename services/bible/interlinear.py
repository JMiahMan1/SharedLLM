# services/bible/interlinear.py
"""Word-by-word interlinear text, imported from files the family supplies.

The format read is STEPBible's tagged texts, which are openly licensed
(CC BY 4.0, https://github.com/STEPBible/STEPBible-Data, folder "Translators
Amalgamated OT+NT"):

* TAGNT (Greek NT): ``Jhn.3.16#03=NKO  ἠγάπησεν (ēgapēsen)  loved  G0025=V-AAI-3S
  ἀγαπάω=to love ...``
* TAHOT (Hebrew OT): ``Gen.1.1#02=L  בָּרָ֣א  ba.Ra'  he created  {H1254A}
  HVqp3ms ... {H1254A=בָּרָא=to create}``

Every other line of those files (headers, notes, the "# Jhn.3.16" verse
summaries) is skipped. References are English versification, which is what the
reader uses; a Hebrew reference that differs follows in brackets and is
ignored. Nothing here is generated: a verse without imported words has no
interlinear, and the reader falls back to Blue Letter Bible's.
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field

from sqlmodel import Session, delete, select

from services.bible.books import BOOK_ORDER, resolve_book
from services.bible.models import InterlinearVerse

ATTRIBUTION = "Interlinear data: STEPBible.org, CC BY 4.0 (Tyndale House, Cambridge)"

#: STEPBible book codes our alias table does not already read.
_STEP_BOOKS = {"rut": "Ruth", "jol": "Joel", "nam": "Nah", "sng": "Song", "amo": "Amos", "phm": "Phlm"}

_ROW = re.compile(r"^([1-3]?[A-Za-z]+)\.(\d+)\.(\d+)[^#\t]*#(\d+)=([^\t]+)\t")
_TRANSLIT = re.compile(r"^(.*?)\s*\(([^)]*)\)\s*$")
_HEB_ROOT = re.compile(r"\{([^}]*)\}")


def step_book(code: str) -> str | None:
    return _STEP_BOOKS.get(code.lower()) or resolve_book(code)


def _clean_gloss(text: str) -> str:
    """``": send»to come out:2_send"`` -> ``send``; ``"to love"`` stays."""
    text = text.strip().lstrip(":").strip()
    return text.split("»")[0].strip()


def _greek(cols: list[str]) -> dict:
    m = _TRANSLIT.match(cols[1])
    original, translit = (m.group(1), m.group(2)) if m else (cols[1], "")
    strongs, _, grammar = cols[3].partition("=")
    lemma, _, gloss = cols[4].partition("=") if len(cols) > 4 else ("", "", "")
    return {
        "w": original.strip().strip("[]").strip(),
        "t": translit.strip(),
        "e": re.sub(r"^\{[^}]*\}\s*", "", cols[2]).strip().strip("[]").strip(),
        "s": strongs.strip(),
        "g": grammar.strip(),
        "l": lemma.strip(),
        "d": _clean_gloss(gloss),
    }


def _hebrew(cols: list[str]) -> dict:
    roots = _HEB_ROOT.findall(cols[4]) if len(cols) > 4 else []
    strongs = roots[0] if roots else (cols[4].split("/")[0] if len(cols) > 4 else "")
    lemma = gloss = ""
    expanded = cols[11] if len(cols) > 11 else ""
    for part in _HEB_ROOT.findall(expanded):
        pieces = part.split("=")
        if len(pieces) >= 3:
            lemma, gloss = pieces[1], "=".join(pieces[2:])
            break
    return {
        "w": cols[1].replace("/", "").replace("\\", "").strip(),
        "t": cols[2].replace("/", "").strip(),
        "e": cols[3].replace("/ ", " ").replace("/", " ").strip(),
        "s": strongs.strip(),
        "g": cols[5].strip() if len(cols) > 5 else "",
        "l": lemma.strip(),
        "d": _clean_gloss(gloss),
    }


@dataclass
class ParsedVerse:
    book: str
    chapter: int
    verse: int
    language: str
    words: list[dict] = field(default_factory=list)


def parse(lines: Iterable[str]) -> Iterator[ParsedVerse | str]:
    """Verses in file order; an unknown book code is yielded as a string."""
    current: ParsedVerse | None = None
    unknown: set[str] = set()
    for raw in lines:
        m = _ROW.match(raw)
        if not m:
            continue
        code, chapter, verse, _word, kind = m.groups()
        osis = step_book(code)
        if osis is None:
            if code not in unknown:
                unknown.add(code)
                yield code
            continue
        cols = raw.rstrip("\n").split("\t")
        greek = bool(re.match(r"^G\d", cols[3])) if len(cols) > 3 else False
        if not greek and kind.startswith("X"):
            continue  # reconstructed from the Septuagint, not in the Hebrew text
        key = (osis, int(chapter), int(verse))
        if current is None or (current.book, current.chapter, current.verse) != key:
            if current is not None and current.words:
                yield current
            current = ParsedVerse(*key, language="greek" if greek else "hebrew")
        word = _greek(cols) if greek else _hebrew(cols)
        if kind not in ("NKO", "L"):
            word["k"] = kind  # not in every edition / manuscript tradition
        current.words.append(word)
    if current is not None and current.words:
        yield current


@dataclass
class ImportResult:
    verses: int = 0
    words: int = 0
    books: list[str] = field(default_factory=list)
    unknown_books: list[str] = field(default_factory=list)


def import_lines(session: Session, lines: Iterable[str], source: str) -> ImportResult:
    """Replace the interlinear of every book the file covers with its contents.

    A book is cleared the first time the file reaches it, so re-importing the
    same file (or a corrected one) never leaves two copies of a verse.
    """
    result = ImportResult()
    pending: list[InterlinearVerse] = []
    for item in parse(lines):
        if isinstance(item, str):
            result.unknown_books.append(item)
            continue
        if item.book not in result.books:
            session.exec(delete(InterlinearVerse).where(InterlinearVerse.book == item.book))
            result.books.append(item.book)
        pending.append(
            InterlinearVerse(
                book=item.book,
                chapter=item.chapter,
                verse=item.verse,
                language=item.language,
                words=json.dumps(item.words, ensure_ascii=False),
                source=source,
            )
        )
        result.verses += 1
        result.words += len(item.words)
        if len(pending) >= 1000:
            session.add_all(pending)
            session.flush()
            pending.clear()
    session.add_all(pending)
    session.commit()
    return result


def verses_for(session: Session, book: str, chapter: int, verse_start: int, verse_end: int) -> list[InterlinearVerse]:
    rows = session.exec(
        select(InterlinearVerse)
        .where(InterlinearVerse.book == book)
        .where(InterlinearVerse.chapter == chapter)
        .where(InterlinearVerse.verse >= verse_start)
        .where(InterlinearVerse.verse <= verse_end)
        .order_by(InterlinearVerse.verse)
    ).all()
    return list(rows)


def coverage(session: Session) -> dict:
    """Which books have an interlinear, in canonical order, and from where."""
    from sqlalchemy import func

    rows = session.exec(
        select(InterlinearVerse.book, InterlinearVerse.language, InterlinearVerse.source, func.count())
        .group_by(InterlinearVerse.book, InterlinearVerse.language, InterlinearVerse.source)
    ).all()
    books = sorted(
        ({"book": b, "language": lang, "source": src, "verses": n} for b, lang, src, n in rows),
        key=lambda r: BOOK_ORDER.get(r["book"], 999),
    )
    return {"books": books, "verse_count": sum(r["verses"] for r in books), "attribution": ATTRIBUTION}

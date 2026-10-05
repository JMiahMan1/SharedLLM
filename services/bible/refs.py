# services/bible/refs.py
"""Scripture reference parsing.

References are a tiny language and the whole reader is built on being able to
read it: the URL bar, deep links into blb.org, TTS narration, chat envelope
chips and quiz questions all funnel through here. Everything downstream
consumes :class:`VerseSpan` and never a raw string, so a reference is parsed
exactly once.

Grammar accepted (matching what people actually paste, and blb.org's own
search-form syntax):

    John 3:16                 single verse
    John 3:16-18               verse range inside a chapter
    John 3:16-4:2              range across chapters
    John 3                     whole chapter
    John                       whole book
    John 3:16, 4:22            multi-passage, book inherited
    John 3; John 4             semicolon group (blb.org syntax)
    Gen 1:26-28; 3:15          semicolon group with an inherited book
    Psa-Mal                    book range, whole books
    John 3:16 ESV              trailing translation token (ignored here)
    Jn 3:16, 1 Cor 13:4-7     abbreviated books, multi-passage

Anything else raises :class:`ReferenceError` with a message naming what it
could not read -- never a best-guess reference.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from services.bible.books import BOOK_ORDER, BOOKS, book as book_row, resolve_book


class ReferenceError(ValueError):
    """A reference string could not be parsed. The message names the input."""


@dataclass(frozen=True)
class VerseSpan:
    """A contiguous run of verses.

    ``verse_start``/``verse_end`` are 1-based and inclusive. ``verse_end`` is
    ``None`` meaning "through the end of ``chapter_end``", which is how a
    whole chapter and a half-open range (``John 3:16-4``) stay
    distinguishable from a range that genuinely ends at the last verse.
    """

    book: str
    chapter_start: int
    verse_start: int = 1
    chapter_end: int | None = None
    verse_end: int | None = None
    whole_book: bool = False

    @property
    def resolved_chapter_end(self) -> int:
        return self.chapter_end if self.chapter_end is not None else self.chapter_start

    def display(self) -> str:
        label = book_row(self.book)["name"]
        same_chapter = self.chapter_start == self.resolved_chapter_end
        if self.whole_book:
            return label
        if self.verse_end is None:
            if self.verse_start == 1:
                return f"{label} {self.chapter_start}" if same_chapter else f"{label} {self.chapter_start}-{self.resolved_chapter_end}"
            tail = f"-{self.resolved_chapter_end}" if not same_chapter else ""
            return f"{label} {self.chapter_start}:{self.verse_start}{tail}"
        if same_chapter:
            if self.verse_start == self.verse_end:
                return f"{label} {self.chapter_start}:{self.verse_start}"
            return f"{label} {self.chapter_start}:{self.verse_start}-{self.verse_end}"
        return f"{label} {self.chapter_start}:{self.verse_start}-{self.resolved_chapter_end}:{self.verse_end}"


# Translation suffixes a reader may paste off a web page. Matched as a
# trailing ALL-CAPS token; a lowercase word is never treated as a translation
# so "John 3:16 Christ" stays a reference plus stray text we will reject.
_TRANSLATION_SUFFIX = re.compile(r"\s+[A-Z][A-Z0-9]{1,7}$")

# "3" | "3:16" -- one side of a range, or a whole reference on its own.
_SPAN_SIDE = re.compile(r"^(?P<chapter>\d{1,3})(?::(?P<verse>\d{1,3}))?$")


def _squash(token: str) -> str:
    """Lowercase and drop spaces/periods so book spellings match one way."""
    return re.sub(r"[.\s]+", "", token).lower()


def _normalize_separators(token: str) -> str:
    """Treat ``Jn. 3.16`` and ``3:16`` as the same reference.

    A period between two digits is a verse separator in every English
    Bible citation style. A period after a letter is an abbreviation dot
    ("Jn.", "2pe.") and must survive, so this only rewrites digit-dot-digit.
    """
    return re.sub(r"[-\u2010-\u2015\u2212]+", "-", re.sub(r"(?<=\d)\s*\.\s*(?=\d)", ":", token)).strip()


def _strip_translation_suffix(token: str) -> str:
    return _TRANSLATION_SUFFIX.sub("", token).strip()


def _match_book(token: str) -> tuple[str | None, str]:
    """Split a leading book token off ``token``.

    Returns ``(osis, remainder)``; ``osis`` is None when the token names no
    book we know -- which is how an inherited-book continuation (``4:22``)
    and genuine garbage (``Foo 1:1``) both stay distinguishable downstream.
    """
    squashed = _squash(token)
    if not squashed:
        return None, token
    # Longest alias first so "1 John" wins over a bare number, and
    # "Song of Songs" over "Song". Numbered books are why a leading digit is
    # not a short-circuit here.
    for length in range(min(len(squashed), 24), 0, -1):
        osis = resolve_book(squashed[:length])
        if osis:
            return osis, squashed[length:]
    return None, token


def _parse_side(side: str, item: str) -> tuple[int, int | None]:
    """Parse one ``chapter[:verse]`` half of a reference."""
    match = _SPAN_SIDE.match(side.strip())
    if not match:
        raise ReferenceError(f"could not read reference: {item!r}")
    return int(match.group("chapter")), int(match.group("verse")) if match.group("verse") else None


def _check_chapter(book_osis: str, chapter: int, item: str) -> int:
    """Reject a chapter the book does not have.

    Chapter counts are fixed knowledge, so a bad chapter is a typo we can name
    now rather than an empty passage the reader discovers later.
    """
    total = book_row(book_osis)["chapters"]
    if chapter < 1 or chapter > total:
        raise ReferenceError(f"{book_row(book_osis)['name']} has {total} chapters, not {chapter}: {item!r}")
    return chapter


def _parse_item(item: str, book_osis: str, inherited: dict) -> VerseSpan:
    """Parse one comma-separated item.

    ``inherited`` carries the previous item's book and chapter so the
    shorthand forms people actually type resolve the way they mean:
    ``John 3:16, 17`` is verse 17 of chapter 3, while ``John 3, 4`` is
    chapters 3 and 4. ``inherited["from_verse"]`` is what separates the two --
    a bare number continues as a verse only when the item before it ended on
    a verse.
    """
    item = _normalize_separators(item.strip())
    if not item:
        raise ReferenceError(f"empty reference in list: {item!r}")

    matched_book, remainder = _match_book(item)
    if matched_book:
        book_osis = matched_book
    elif not book_osis:
        raise ReferenceError(f"reference has no book: {item!r}")

    remainder = remainder.strip().lstrip(":").strip()
    if not remainder:
        # Bare book: the whole book.
        inherited.update(book=book_osis, chapter=None, from_verse=False)
        return VerseSpan(
            book=book_osis,
            chapter_start=1,
            chapter_end=book_row(book_osis)["chapters"],
            whole_book=True,
        )

    if (
        matched_book is None
        and inherited.get("book") == book_osis
        and inherited.get("from_verse")
        and inherited.get("chapter")
        and ":" not in remainder
        and re.match(r"^\d{1,3}(-\d{1,3})?$", remainder)
    ):
        # "Deut 8:2-6, 10, 17-18" -- an earlier item fixed the chapter, so every
        # bare number after it is a verse of that chapter, not a new chapter.
        start_text, _, end_text = remainder.partition("-")
        chapter_start = int(inherited["chapter"])
        verse_start = int(start_text)
        verse_end = int(end_text) if end_text else verse_start
        if verse_end < verse_start:
            raise ReferenceError(f"range ends before it starts: {item!r}")
        inherited.update(book=book_osis, chapter=chapter_start, from_verse=True)
        return VerseSpan(
            book=book_osis,
            chapter_start=chapter_start,
            verse_start=verse_start,
            chapter_end=chapter_start,
            verse_end=verse_end,
        )

    if "-" in remainder:
        if remainder.count("-") > 1:
            raise ReferenceError(f"could not read reference: {item!r}")
        left, _, right = remainder.partition("-")
        chapter_start, verse_start = _parse_side(left, item)
        chapter_end, verse_end = _parse_side(right, item)
        if verse_end is None and verse_start is not None:
            bare = int(right.strip())
            # A bare right-hand number after a verse is a *verse* when it could
            # continue the range, and a *chapter* when it could not. A range
            # never runs backwards, so "3:16-18" is verses 16-18 and
            # "3:16-4" is verse 16 through the end of chapter 4. Reading it
            # one way unconditionally turns half of real references into either
            # nonsense or the wrong text.
            if bare >= verse_start:
                chapter_end = chapter_start
                verse_end = bare
            else:
                chapter_end = bare
                verse_end = None
        _check_chapter(book_osis, chapter_start, item)
        _check_chapter(book_osis, chapter_end, item)
        if chapter_end < chapter_start:
            raise ReferenceError(f"range ends before it starts: {item!r}")
        if (
            chapter_end == chapter_start
            and verse_end is not None
            and verse_start is not None
            and verse_end < verse_start
        ):
            raise ReferenceError(f"range ends before it starts: {item!r}")
        inherited.update(book=book_osis, chapter=chapter_start, from_verse=verse_start is not None)
        return VerseSpan(
            book=book_osis,
            chapter_start=chapter_start,
            verse_start=verse_start if verse_start is not None else 1,
            chapter_end=chapter_end,
            # verse_end None means "through the end of chapter_end" -- which is
            # what "John 3:16-4" asks for, and never a single-verse span.
            verse_end=verse_end,
        )

    chapter_start, verse_start = _parse_side(remainder, item)
    _check_chapter(book_osis, chapter_start, item)
    inherited.update(book=book_osis, chapter=chapter_start, from_verse=verse_start is not None)
    return VerseSpan(
        book=book_osis,
        chapter_start=chapter_start,
        verse_start=verse_start if verse_start is not None else 1,
        chapter_end=chapter_start,
        # "John 3" is the whole chapter (None); "John 3:16" is a closed
        # single verse. Collapsing these would make "John 3:1" indistinguishable
        # from "John 3:1 to the end of the chapter".
        verse_end=verse_start,
    )


def _parse_book_range(left: str, right: str) -> list[VerseSpan]:
    left_osis = resolve_book(_squash(left))
    right_osis = resolve_book(_squash(right))
    if not left_osis or not right_osis:
        raise ReferenceError(f"unknown book in range: {left!r}-{right!r}")
    start_order = BOOK_ORDER[left_osis]
    end_order = BOOK_ORDER[right_osis]
    if end_order < start_order:
        raise ReferenceError(f"book range ends before it starts: {left!r}-{right!r}")
    ordered = [b["osis"] for b in BOOKS if start_order <= BOOK_ORDER[b["osis"]] <= end_order]
    return [
        VerseSpan(
            book=osis,
            chapter_start=1,
            chapter_end=book_row(osis)["chapters"],
            whole_book=True,
        )
        for osis in ordered
    ]


def parse_reference(text: str) -> list[VerseSpan]:
    """Parse a reference (or list) into spans. Raises ReferenceError."""
    if text is None:
        raise ReferenceError("reference is empty")
    raw = _strip_translation_suffix(_strip_translation_suffix(_normalize_separators(str(text).strip())))
    if not raw:
        raise ReferenceError("reference is empty")

    spans: list[VerseSpan] = []
    inherited: dict[str, object] = {"book": "", "chapter": None, "from_verse": False}
    for group in raw.split(";"):
        group = group.strip()
        if not group:
            continue
        items = [part for part in group.split(",")]
        items = [part.strip() for part in items if part.strip()]
        if not items:
            continue

        # Whole group may be a book range: "Psa-Mal", "Gen-Ex".
        if len(items) == 1 and "-" in items[0]:
            left, _, right = items[0].partition("-")
            if left.strip() and right.strip() and not any(c.isdigit() for c in items[0][-1:]):
                left_book, _ = _match_book(left)
                right_book, _ = _match_book(right)
                if left_book and right_book and BOOK_ORDER[left_book] != BOOK_ORDER[right_book]:
                    spans.extend(_parse_book_range(left, right))
                    inherited.update(book=right_book, chapter=None, from_verse=False)
                    continue

        for item in items:
            spans.append(_parse_item(item, str(inherited["book"] or ""), inherited))

    if not spans:
        raise ReferenceError(f"reference contained no passages: {text!r}")
    return spans


def parse_one(text: str) -> VerseSpan:
    """Parse a reference expected to name exactly one passage."""
    spans = parse_reference(text)
    if len(spans) != 1:
        raise ReferenceError(f"expected a single passage, got {len(spans)}: {text!r}")
    return spans[0]


def format_reference(spans: list[VerseSpan]) -> str:
    """Render spans back to a display string (semicolon-joined like blb.org)."""
    return "; ".join(span.display() for span in spans)
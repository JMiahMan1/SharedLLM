"""The rest of a study Bible's apparatus: footnotes, cross references,
articles, profiles, chapter features and book introductions.

``epub_import`` joins commentary to verses through the links a verse
paragraph makes. Most study Bibles say more than that, and say it in one of
two ways this module reads:

* **By id.** A publisher's ids encode the canonical position as eight
  digits, book-chapter-verse (``01001002`` is Genesis 1:2), behind a prefix
  that says what the block is. Measured against the books in the family
  library: footnotes are ``fn19003000a`` (NIV Zondervan), ``f01001002-…``
  (NKJV Apply the Word) and ``f43001004.1`` (ESV); cross references are
  ``x01001001a`` (NIV) and ``c43001001.a`` (ESV, the id sits on a link inside
  the paragraph); articles, charts and character profiles are sidebars
  ``sb-01001001-or0003`` / ``sbmap01012009-0048``; and a book's introduction
  is the element ``bk01``. Verse 000 is the chapter itself (a psalm's title).

* **By structure**, for a converted EPUB whose ids and classes are generated
  (the NLT Courage For Life): EPUB3 endnotes name their verse in their first
  link ("John 1:4"); a chapter document links each article and profile to
  its own document, which opens with the passage it covers ("Jesus is God
  1:1-18"); a boxed feature at the head of a chapter belongs to the chapter;
  and a book's introduction is the document just before its first chapter
  whose title is the book's name.

Nothing is guessed onto a verse: a block whose position cannot be read is
counted under ``unplaced`` and left out.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from html.parser import HTMLParser

from services.bible.books import BOOKS, resolve_book

# id prefix -> what the block is. Eight digits are book (2), chapter (3), verse (3).
_POSITIONAL: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^fn(\d{2})(\d{3})(\d{3})[a-z]*$"), "footnote"),
    (re.compile(r"^f(\d{2})(\d{3})(\d{3})[.\-]"), "footnote"),
    (re.compile(r"^x(\d{2})(\d{3})(\d{3})[a-z]+$"), "crossref"),
    (re.compile(r"^c(\d{2})(\d{3})(\d{3})\.[a-z]+$"), "crossref"),
    (re.compile(r"^sb[a-z]*-?(\d{2})(\d{3})(\d{3})(?:\D|$)"), "article"),
)
_INTRO_ID = re.compile(r"^bk(\d{2})$")
# Elements that hold a block's text; an id on a link inside one names the block.
_BLOCKS = frozenset({"p", "div", "aside", "li", "section", "blockquote", "td", "dd"})
_BREAKS = _BLOCKS | {"br", "tr", "table", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol"}
_SKIP = frozenset({"script", "style"})
# Navigation inside a sidebar ("Return to Index of Articles", "Go to Maps Index").
_NAVIGATION = re.compile(r"^(return to|go to)\b", re.IGNORECASE)
# The marker a note opens with ("[1]", "a", "*") and the verse it repeats ("1:4").
_LEAD_MARKER = re.compile(r"^\s*(?:\[\w{1,3}\]|\*|[a-z]{1,2}\b|\d{1,3}\b(?!:))\s*")
_LEAD_VERSE = re.compile(r"^\s*\d{1,3}:\d{1,3}(?:\s*[\u2013\u2014-]\s*\d{1,3}(?::\d{1,3})?)?\s*")
# A passage at the end of an article's title line: "Jesus is God 1:1-18".
_TITLE_PASSAGE = re.compile(r"(\d{1,3}):(\d{1,3})(?:\s*[\u2013\u2014-]\s*\d{1,3}(?::\d{1,3})?)?\s*$")
# A reference a note or profile names: "John 1:4", "Isa 36:2a" (its second note), "Genesis 11-25".
_REFERENCE = re.compile(r"^\s*((?:[1-3]\s?)?[A-Za-z][A-Za-z .]*?)\s+(\d{1,3})(?::(\d{1,3})(?:[a-z]\b)?)?\b")
_RETURN = re.compile(r"^return to\b", re.IGNORECASE)
_CHAPTER_HEADING = re.compile(r"^((?:[1-3]\s?)?[A-Za-z][A-Za-z .]*?)\s+(\d{1,3})$")
_WS = re.compile(r"[ \t\r\f\v\u00a0]+")
_PROSE = 80  # characters that make a paragraph prose rather than a label or a link

_BOOK_NUMBER = {entry["osis"]: position for position, entry in enumerate(BOOKS, start=1)}


@dataclass
class StudyExtras:
    """Notes found here, as ``(book, chapter, verse, kind, body)``, and what was left out."""

    notes: list[tuple[int, int, int, str, str]] = field(default_factory=list)
    counts: Counter[str] = field(default_factory=Counter)
    unplaced: Counter[str] = field(default_factory=Counter)

    def add(self, book: int, chapter: int, verse: int, kind: str, body: str) -> None:
        body = body.strip()
        if body and 1 <= book <= len(BOOKS) and chapter >= 1 and verse >= 0:
            self.notes.append((book, chapter, verse, kind, body))
            self.counts[kind] += 1


@dataclass
class _Element:
    tag: str
    attrs: dict[str, str]
    start: int
    paragraphs: list[str]
    parent: _Element | None = None
    #: Only these keep their text: a study Bible runs to thousands of elements.
    wanted: bool = False

    def ancestors(self) -> list[_Element]:
        out, node = [], self.parent
        while node is not None:
            out.append(node)
            node = node.parent
        return out


def _marked(ident: str) -> bool:
    return bool(ident) and (bool(_INTRO_ID.match(ident)) or any(p.match(ident) for p, _k in _POSITIONAL))


def _is_note(epub_type: str) -> bool:
    return any(word in epub_type.split() for word in ("endnote", "footnote", "rearnote"))


class _Document(HTMLParser):
    """One document as text: its paragraphs, its links, and the elements of interest.

    ``closed`` holds only the elements this module reads (one whose id says
    where it belongs, an EPUB3 note, and -- with ``boxes`` -- a top-level box),
    each with the text it encloses split into paragraphs.
    """

    def __init__(self, *, boxes: bool = False) -> None:
        super().__init__(convert_charrefs=True)
        self._boxes = boxes
        self.text: list[str] = []
        self.stack: list[_Element] = []
        self.closed: list[_Element] = []
        self.paragraphs: list[tuple[int, str]] = []  # (depth below body, text)
        self.links: list[tuple[str, str]] = []  # (href, link text)
        self._skip = 0
        self._link: list[str] | None = None
        self._href = ""
        self._para: list[str] | None = None
        self._para_depth = 0

    # -- parsing ---------------------------------------------------------
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        data = {k: (v or "") for k, v in attrs}
        if tag in _SKIP:
            self._skip += 1
            return
        if tag in _BREAKS:
            self.text.append("\n")
        if tag == "p" or (tag in _BLOCKS and tag != "div" and self._para is None):
            self._para = []
            self._para_depth = sum(1 for e in self.stack if e.tag == "div")
        if tag == "a":
            self._link = []
            self._href = data.get("href", "")
        ident = data.get("id", "")
        if tag not in _BLOCKS and _marked(ident):
            # An id on a link names the paragraph around it (the ESV's cross references).
            block = next((e for e in reversed(self.stack) if e.tag in _BLOCKS), None)
            if block is not None:
                block.wanted = True
        if tag not in {"br", "img", "hr", "link", "meta"}:
            element = _Element(tag, data, len(self.text), [], self.stack[-1] if self.stack else None)
            element.wanted = (
                _marked(ident)
                or _is_note(data.get("epub:type", ""))
                or (self._boxes and tag == "div" and not any(e.tag == "div" for e in self.stack))
            )
            self.stack.append(element)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in {"br", "img", "hr", "link", "meta"} and tag not in _SKIP:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP:
            self._skip = max(0, self._skip - 1)
            return
        if tag == "a" and self._link is not None:
            self.links.append((self._href, _clean("".join(self._link))))
            self._link = None
        if self._para is not None and (tag == "p" or (tag in _BLOCKS and tag != "div")):
            text = _clean("".join(self._para))
            if text:
                self.paragraphs.append((self._para_depth, text))
            self._para = None
        if tag in _BREAKS:
            self.text.append("\n")
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index].tag == tag:
                element = self.stack.pop(index)
                del self.stack[index:]
                if element.wanted:
                    element.paragraphs = _paragraphs("".join(self.text[element.start:]))
                    self.closed.append(element)
                return

    def handle_data(self, data: str) -> None:
        # The letter or number a note is called by ("ennum", "fnnum") is not its text.
        if self._skip or any(e.attrs.get("class", "").endswith("num") for e in self.stack[-3:]):
            return
        self.text.append(data)
        if self._link is not None:
            self._link.append(data)
        if self._para is not None:
            self._para.append(data)


def _clean(text: str) -> str:
    return _WS.sub(" ", text).strip()


def _paragraphs(text: str) -> list[str]:
    return [p for p in (_clean(line) for line in text.split("\n")) if p]


def _strip_lead(text: str) -> str:
    """Drop the marker and repeated verse a footnote or cross reference opens with."""
    for _ in range(2):
        text = _LEAD_VERSE.sub("", _LEAD_MARKER.sub("", text, count=1), count=1)
    return text.strip()


def _body(paragraphs: Iterable[str]) -> str:
    return "\n\n".join(p for p in paragraphs if not _NAVIGATION.match(p))


def _parse(markup: str, *, boxes: bool = False) -> _Document:
    document = _Document(boxes=boxes)
    document.feed(markup)
    document.close()
    while document.stack:  # unclosed elements still hold their text
        document.handle_endtag(document.stack[-1].tag)
    document.text = []
    return document


def _position(match: re.Match[str]) -> tuple[int, int, int]:
    return int(match[1]), int(match[2]), int(match[3])


def _book_number(name: str) -> int | None:
    osis = resolve_book(name.strip())
    return _BOOK_NUMBER.get(osis) if osis else None


def _reference(text: str) -> tuple[int, int, int] | None:
    """(book, chapter, verse) of a reference like "John 1:4"; verse 0 when only a chapter is named."""
    match = _REFERENCE.match(text)
    if not match:
        return None
    book = _book_number(match[1])
    if book is None:
        return None
    return book, int(match[2]), int(match[3] or 0)


def _target(href: str) -> str:
    return href.split("#", 1)[0].rsplit("/", 1)[-1]


# -- by id ---------------------------------------------------------------

def _by_id(document: _Document, extras: StudyExtras) -> None:
    taken: set[int] = set()
    for element in document.closed:
        ident = element.attrs.get("id", "")
        intro = _INTRO_ID.match(ident)
        if intro and element.tag in {"div", "section", "body"}:
            body = _body(element.paragraphs)
            if len(body) >= _PROSE:
                extras.add(int(intro[1]), 1, 0, "introduction", body)
            continue
        for pattern, kind in _POSITIONAL:
            match = pattern.match(ident)
            if not match:
                continue
            # An id on a link names the paragraph around it (the ESV's cross references).
            block = element
            if element.tag not in _BLOCKS:
                block = next((e for e in element.ancestors() if e.tag in _BLOCKS), element)
            if id(block) in taken:
                break
            taken.add(id(block))
            book, chapter, verse = _position(match)
            if kind == "article":
                body = _body(block.paragraphs)
                # A sidebar that is only a map or a picture says nothing to read.
                if len(body) - len(block.paragraphs[0] if block.paragraphs else "") < _PROSE:
                    extras.unplaced["picture"] += 1
                    break
            else:
                body = _strip_lead(" ".join(block.paragraphs))
            if chapter == 0:
                extras.unplaced[kind] += 1
                break
            extras.add(book, chapter, verse, kind, body)
            break


# -- by structure ----------------------------------------------------------

_CHAPTER_VERSE = re.compile(r"(\d{1,3}):(\d{1,3})(?:[a-z]\b)?")


def _endnotes(document: _Document, extras: StudyExtras, chapters: dict[str, _Chapter]) -> None:
    """EPUB3 notes, placed by the reference they open with ("John 1:4").

    An abbreviation the book index does not know ("Hagg 1:1") is placed by
    the chapter document the note links back to, which names its book.
    """
    for element in document.closed:
        if not _is_note(element.attrs.get("epub:type", "")):
            continue
        text = " ".join(element.paragraphs).lstrip("*\u2020 ").strip()
        reference = _REFERENCE.match(text)
        where = _reference(text)
        if where is None and reference:
            back = next(
                (chapters[_target(h)] for h, label in document.links
                 if label and label in text[:40] and _target(h) in chapters),
                None,
            )
            verse = _CHAPTER_VERSE.search(text[: reference.end() + 3])
            if back and verse:
                where = (back.book, int(verse[1]), int(verse[2]))
        if where is None or where[2] == 0:
            extras.unplaced["footnote"] += 1
            continue
        extras.add(*where, "footnote", _strip_lead(text[reference.end():] if reference else text))


@dataclass
class _Chapter:
    book: int
    chapter: int


def _chapter_of(document: _Document) -> _Chapter | None:
    """The book and chapter a chapter document opens with ("John 1")."""
    for _depth, text in document.paragraphs[:2]:
        match = _CHAPTER_HEADING.match(text.rstrip("*\u2020 "))  # "Lamentations 1 *" carries a note
        if match:
            book = _book_number(match[1])
            if book is not None and 1 <= int(match[2]) <= BOOKS[book - 1]["chapters"]:
                return _Chapter(book, int(match[2]))
    return None


def _has_endnotes(document: _Document) -> bool:
    return any("endnote" in e.attrs.get("epub:type", "").split() for e in document.closed)


def _title_book(title: str) -> int | None:
    """The book a title names: "Genesis", or "Acts of the Apostles"."""
    number = _book_number(title)
    if number is None and len(title) < 40:
        number = _book_number(re.split(r"\s+(?:of|to|by)\s+", title, maxsplit=1)[0])
    return number


def _prose(document: _Document) -> list[str]:
    return [text for _depth, text in document.paragraphs]


def _structured(
    names: list[str], documents: dict[str, _Document], chapters: dict[str, _Chapter], extras: StudyExtras,
) -> None:
    if not chapters:
        return

    introductions: set[str] = set()
    # A book's introduction: titled with the book's name, just before its first chapter.
    first_chapter = {}
    for index, name in enumerate(names):
        where = chapters.get(name)
        if where and where.chapter == 1:
            first_chapter.setdefault(where.book, index)
    for book, index in first_chapter.items():
        for name in reversed(names[max(0, index - 12):index]):
            if name in chapters:
                continue
            paragraphs = _prose(documents[name])
            if not paragraphs or _title_book(paragraphs[0]) != book:
                continue
            if sum(1 for p in paragraphs if len(p) >= _PROSE) >= 2:
                extras.add(book, 1, 0, "introduction", _body(paragraphs))
                introductions.add(name)
                break

    # Articles and profiles: a document a chapter links to as a whole, or one
    # that links back to its chapter ("Return to scripture text"), filed where
    # its own title line says, else at the passage it names, else its chapter.
    linked_from: dict[str, list[_Chapter]] = {}
    for name, where in chapters.items():
        for href, _text in documents[name].links:
            target = _target(href)
            # An article is linked as a whole document; a link into a page is a note or a verse.
            if "#" in href or target == name or target not in documents or target in chapters or target in introductions:
                continue
            linked_from.setdefault(target, []).append(where)
    for name, document in documents.items():
        if name in chapters or name in introductions:
            continue
        for href, text in document.links:
            if _RETURN.match(text) and _target(href) in chapters:
                linked_from.setdefault(name, []).append(chapters[_target(href)])
    for target, sources in linked_from.items():
        paragraphs = _prose(documents[target])
        if sum(1 for p in paragraphs if len(p) >= _PROSE) < 2 or _has_endnotes(documents[target]):
            continue  # navigation, a chapter list, a one-line definition, a page of notes
        passage = _TITLE_PASSAGE.search(paragraphs[0])
        place: tuple[int, int, int] | None = None
        if passage:
            chapter, verse = int(passage[1]), int(passage[2])
            book = next((s.book for s in sources if s.chapter == chapter), sources[0].book)
            place = (book, chapter, verse)
        else:
            # A profile opens with the passages it covers ("Genesis 11-25").
            place = next((p for _h, text in documents[target].links if (p := _reference(text))), None)
            if place is None:
                place = (sources[0].book, sources[0].chapter, 0)
        extras.add(*place, "article", _body(paragraphs))

    # A boxed feature at the head of a chapter ("listen to the Word").
    for name, where in chapters.items():
        document = documents[name]
        for element in document.closed:
            if element.tag != "div" or any(e.tag == "div" for e in element.ancestors()):
                continue
            paragraphs = element.paragraphs
            if not any(len(p) >= _PROSE for p in paragraphs) or any(p[:1].isdigit() for p in paragraphs):
                continue
            extras.add(where.book, where.chapter, 0, "commentary", _body(paragraphs))


def harvest(documents: list[tuple[str, str]], *, structured: bool) -> StudyExtras:
    """Every note this module can place, from the archive's documents in reading order.

    ``structured`` reads the generated-class layout too (articles linked from
    chapters, chapter boxes, introductions by title); it is for an EPUB read
    by its numbering, where there are no ids to go by.
    """
    extras = StudyExtras()
    kept: dict[str, _Document] = {}
    chapters: dict[str, _Chapter] = {}
    # One document at a time: a study Bible is tens of megabytes of markup,
    # so only what a later step needs is held on to.
    for name, markup in documents:
        key = name.rsplit("/", 1)[-1]
        document = _parse(markup, boxes=structured)
        if (where := _chapter_of(document)) is not None:
            chapters[key] = where
        _by_id(document, extras)
        if structured or any(_is_note(e.attrs.get("epub:type", "")) for e in document.closed):
            if not structured:
                document.paragraphs = []
            kept[key] = document
    for document in kept.values():
        _endnotes(document, extras, chapters)
    if structured:
        _structured(list(kept), kept, chapters, extras)
    return extras

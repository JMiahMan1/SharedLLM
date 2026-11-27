"""A book's chapters, the way its author numbered them.

"Chapter 4 of Wild at Heart" means the author's chapter 4, not the fourth
file in the EPUB (which is usually the copyright page). So the chapters come
from the book's own table of contents when it has one -- the EPUB 3 ``nav``
document, the EPUB 2 ``toc.ncx``, or a PDF's outline -- and only when it has
none from the headings in its text ("Chapter 4", "CHAPTER FOUR", "4. Title",
"IV"). Front and back matter (cover, copyright, contents, dedication...) is
listed so a reader can see it, but never numbered, so counting starts where
the author's does.

Pure and standard-library only apart from ``pypdf`` (already a storage
dependency), so the parsing is tested without a library or a network.
"""

from __future__ import annotations

import io
import logging
import posixpath
import re
import threading
import unicodedata
import zipfile
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from html.parser import HTMLParser
from xml.etree import ElementTree as ET

from services.common.epub_text import (
    EpubTextError,
    _match_member,
    _rootfile,
    _spine,
    markup_text,
)

log = logging.getLogger("storage.book_chapters")

_OPF_NS = "{http://www.idpf.org/2007/opf}"
_NCX_NS = "{http://www.daisy.org/z3986/2005/ncx/}"

#: Labels that are front or back matter: listed, never numbered.
_MATTER = re.compile(
    r"^(?:cover|title(?:\s+page)?|half\s+title|copyright|contents|table\s+of\s+contents|toc|"
    r"dedication|epigraph|acknowledg(?:e)?ments?|foreword|preface|introduction|prologue|"
    r"epilogue|afterword|about\s+the\s+authors?|about\s+the\s+book|also\s+by\b.*|other\s+books\s+by\b.*|"
    r"praise\s+for\b.*|endorsements?|notes|endnotes|bibliography|index|glossary|appendix\b.*|"
    r"study\s+guide|discussion\s+guide|readers?\s+guide|front\s+matter|back\s+matter|"
    r"part\s+\w+\b.*|book\s+(?:[ivxlc]+|\d+|one|two|three|four|five)\b.*)$",
    re.IGNORECASE,
)

_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
_ORDINALS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7,
    "eighth": 8, "ninth": 9, "tenth": 10, "eleventh": 11, "twelfth": 12, "thirteenth": 13,
    "fourteenth": 14, "fifteenth": 15, "sixteenth": 16, "seventeenth": 17, "eighteenth": 18,
    "nineteenth": 19, "twentieth": 20, "thirtieth": 30, "fortieth": 40, "fiftieth": 50,
}
_NUMBER_WORD = (
    r"(?:(?:twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)(?:[\s-](?:one|two|three|four|five|six|seven|eight|nine))?"
    r"|zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|"
    r"sixteen|seventeen|eighteen|nineteen)"
)
_PART_LABEL = re.compile(r"^(?:part|book|section|volume)\b", re.IGNORECASE)
_ROMAN = re.compile(r"^M{0,3}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3})$", re.IGNORECASE)

_CHAPTER_PREFIX = re.compile(
    r"^\s*(?:chapter|chap\.?|ch\.?)\s+(?P<num>\d{1,3}|[ivxlcdm]{1,8}|" + _NUMBER_WORD + r")\b"
    r"[\s.:\-\u2013\u2014]*(?P<title>.*)$",
    re.IGNORECASE,
)
_DIGIT_PREFIX = re.compile(r"^\s*(?P<num>\d{1,3})\s*(?:[.:)\-\u2013\u2014]\s*|\s+)(?P<title>\S.*)?$")
_ROMAN_PREFIX = re.compile(r"^\s*(?P<num>[IVXLC]{1,7})(?:\s*[.:\-\u2013\u2014]\s*(?P<title>\S.*)?|\s*$)")
_WORD_PREFIX = re.compile(r"^\s*(?P<num>" + _NUMBER_WORD + r")\s*(?:[.:\-\u2013\u2014]\s*(?P<title>.*))?$", re.IGNORECASE)

#: Default size of one part of a long chapter.
DEFAULT_PART_CHARS = 60000


class ChapterError(ValueError):
    """The book or the chapter asked for cannot be read as chapters."""


@dataclass
class BookChapter:
    """One entry of a book's contents, in reading order."""

    label: str
    text: str = ""
    number: int | None = None
    title: str = ""
    matter: bool = False
    #: ``(first, end)`` page indexes of a PDF chapter whose text is read only when
    #: asked for (``OpenedBook.chapter_text``); ``None`` once read or for other formats.
    pages: tuple[int, int] | None = None

    def row(self, index: int) -> dict:
        return {
            "index": index,
            "number": self.number,
            "title": self.title or self.label,
            "label": self.label,
            "front_matter": self.matter,
            # Unknown (None) for a PDF chapter not read yet: counting it would mean
            # extracting every page of the book to answer for one chapter.
            "chars": None if self.pages is not None else len(self.text),
        }


@dataclass
class _TocEntry:
    label: str
    href: str
    children: list[_TocEntry] = field(default_factory=list)


# --- Numbers ---


def _words_to_int(text: str) -> int | None:
    parts = [p for p in re.split(r"[\s-]+", text.lower().strip()) if p]
    if not parts:
        return None
    if len(parts) == 1:
        word = parts[0]
        if word in _UNITS:
            return _UNITS[word]
        if word in _TENS:
            return _TENS[word]
        return _ORDINALS.get(word)
    if len(parts) == 2 and parts[0] in _TENS:
        tail = _UNITS.get(parts[1]) or _ORDINALS.get(parts[1])
        if tail and tail < 10:
            return _TENS[parts[0]] + tail
    return None


def _roman_to_int(text: str) -> int | None:
    text = text.strip().upper()
    if not text or not _ROMAN.match(text):
        return None
    values = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    total = 0
    for i, ch in enumerate(text):
        value = values[ch]
        if i + 1 < len(text) and values[text[i + 1]] > value:
            total -= value
        else:
            total += value
    return total or None


def number_of(token: str) -> int | None:
    """A chapter number written as digits, an ordinal, a word or a roman numeral."""
    token = str(token or "").strip().strip(".:")
    if not token:
        return None
    if token.isdigit():
        return int(token)
    match = re.fullmatch(r"(\d+)(?:st|nd|rd|th)", token, re.IGNORECASE)
    if match:
        return int(match.group(1))
    return _words_to_int(token) or _roman_to_int(token)


def parse_chapter_ref(ref: object) -> int | None:
    """The chapter number a request names, or ``None`` when it names a title.

    Accepts 4, "4", "four", "fourth", "IV", "chapter 4", "chapter four", "ch. 4".
    """
    text = str(ref if ref is not None else "").strip()
    if not text:
        return None
    text = re.sub(r"^the\s+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^(?:chapter|chap\.?|ch\.?)\s*", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"\s+chapter$", "", text, flags=re.IGNORECASE).strip()
    return number_of(text)


def _label_number(label: str) -> tuple[int | None, str]:
    """An explicit number in a contents label and the title that follows it."""
    label = label.strip()
    for pattern in (_CHAPTER_PREFIX, _DIGIT_PREFIX, _ROMAN_PREFIX, _WORD_PREFIX):
        match = pattern.match(label)
        if match:
            number = number_of(match.group("num"))
            if number:
                return number, (match.groupdict().get("title") or "").strip()
    return None, label


def is_matter(label: str) -> bool:
    return bool(_MATTER.match(_norm_space(label)))


def _norm_space(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def number_chapters(chapters: list[BookChapter]) -> list[BookChapter]:
    """Number the author's chapters, leaving front and back matter unnumbered.

    When the labels carry numbers ("Chapter Four", "4. A Battle"), those are
    the numbers. Only when none does are the non-matter entries counted in
    order, which is what a reader would do with a contents page of bare titles.
    """
    explicit: list[tuple[int | None, str]] = []
    for chapter in chapters:
        number, title = _label_number(chapter.label)
        if number is not None and re.match(r"^\s*part\b", chapter.label, re.IGNORECASE):
            number = None
        explicit.append((number, title))
    any_explicit = sum(1 for n, _ in explicit if n is not None) >= max(2, len(chapters) // 3)
    count = 0
    for chapter, (number, title) in zip(chapters, explicit, strict=True):
        if any_explicit and number is not None:
            chapter.number = number
            chapter.title = title or chapter.label
            chapter.matter = False
            continue
        if is_matter(chapter.label):
            chapter.matter = True
            chapter.number = None
            chapter.title = chapter.label
            continue
        if any_explicit:
            chapter.number = None
            chapter.title = chapter.label
            continue
        count += 1
        chapter.number = count
        chapter.title = chapter.label
    return chapters


# --- EPUB ---


class _NavParser(HTMLParser):
    """The nested lists inside an EPUB 3 ``<nav epub:type="toc">``."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.navs: list[tuple[bool, list[_TocEntry]]] = []
        self._in_nav = 0
        self._stack: list[list[_TocEntry]] = []
        self._current: list[_TocEntry | None] = []
        self._label: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attr = {k.lower(): (v or "") for k, v in attrs}
        if tag == "nav":
            self._in_nav += 1
            is_toc = "toc" in (attr.get("epub:type", "") + " " + attr.get("role", "")).lower()
            top: list[_TocEntry] = []
            self.navs.append((is_toc, top))
            self._stack = [top]
            self._current = []
            return
        if not self._in_nav:
            return
        if tag == "ol" or tag == "ul":
            if self._current and self._current[-1] is not None:
                self._stack.append(self._current[-1].children)
            elif len(self._stack) > 1 or self._stack[0]:
                self._stack.append(self._stack[-1])
        elif tag == "li":
            self._current.append(None)
        elif tag in ("a", "span") and self._current and self._current[-1] is None:
            entry = _TocEntry(label="", href=attr.get("href", ""))
            self._stack[-1].append(entry)
            self._current[-1] = entry
            self._label = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "nav":
            self._in_nav = max(0, self._in_nav - 1)
            return
        if not self._in_nav:
            return
        if tag in ("a", "span") and self._label is not None:
            if self._current and self._current[-1] is not None:
                self._current[-1].label = _norm_space("".join(self._label))
            self._label = None
        elif tag == "li":
            if self._current:
                self._current.pop()
        elif (tag == "ol" or tag == "ul") and len(self._stack) > 1:
            self._stack.pop()

    def handle_data(self, data: str) -> None:
        if self._label is not None:
            self._label.append(data)


def _nav_entries(markup: str) -> list[_TocEntry]:
    parser = _NavParser()
    parser.feed(markup)
    parser.close()
    for is_toc, entries in parser.navs:
        if is_toc and entries:
            return entries
    for _is_toc, entries in parser.navs:
        if entries:
            return entries
    return []


def _ncx_entries(xml: bytes) -> list[_TocEntry]:
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return []

    def local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

    def walk(node: ET.Element) -> list[_TocEntry]:
        out: list[_TocEntry] = []
        for child in node:
            if local(child.tag) != "navPoint":
                continue
            label = ""
            href = ""
            for sub in child:
                if local(sub.tag) == "navLabel":
                    label = _norm_space("".join(sub.itertext()))
                elif local(sub.tag) == "content":
                    href = sub.get("src") or ""
            out.append(_TocEntry(label=label, href=href, children=walk(child)))
        return out

    for node in root.iter():
        if local(node.tag) == "navMap":
            return walk(node)
    return []


def _epub_toc(archive: zipfile.ZipFile) -> tuple[str, str, list[_TocEntry]]:
    """``(source, toc_path, entries)`` from the nav document, else toc.ncx."""
    opf_path = _rootfile(archive)
    try:
        root = ET.fromstring(archive.read(opf_path))
    except (KeyError, ET.ParseError) as exc:
        raise EpubTextError(f"package document {opf_path} is unreadable: {exc}") from exc
    base = posixpath.dirname(opf_path)
    nav_href = ncx_href = None
    by_id: dict[str, str] = {}
    for item in root.iter(f"{_OPF_NS}item"):
        href = item.get("href") or ""
        by_id[item.get("id") or ""] = href
        if "nav" in (item.get("properties") or "").split():
            nav_href = href
        if (item.get("media-type") or "").lower() == "application/x-dtbncx+xml":
            ncx_href = href
    spine = root.find(f"{_OPF_NS}spine")
    if ncx_href is None and spine is not None and spine.get("toc") in by_id:
        ncx_href = by_id[spine.get("toc") or ""]
    if nav_href:
        name = _match_member(archive, posixpath.normpath(posixpath.join(base, nav_href)))
        if name:
            entries = _nav_entries(archive.read(name).decode("utf-8", "replace"))
            if entries:
                return "epub-nav", name, entries
    if ncx_href:
        name = _match_member(archive, posixpath.normpath(posixpath.join(base, ncx_href)))
        if name:
            entries = _ncx_entries(archive.read(name))
            if entries:
                return "epub-ncx", name, entries
    return "", "", []


def _flatten(entries: list[_TocEntry]) -> list[_TocEntry]:
    """Top-level entries, with the chapters inside a "Part" brought up beside it.

    A book divided into parts lists "Part One" with its chapters nested
    beneath; a reader still counts those chapters, so they are flattened in.
    A lone top-level entry (the book's own title) is unwrapped the same way.
    """
    if len(entries) == 1 and entries[0].children:
        return [entries[0], *_flatten(entries[0].children)]
    out: list[_TocEntry] = []
    for entry in entries:
        out.append(entry)
        if entry.children and (_PART_LABEL.match(entry.label.strip()) or _all_chapterish(entry.children)):
            out.extend(_flatten(entry.children))
    return out


def _all_chapterish(entries: list[_TocEntry]) -> bool:
    """Nested entries that are themselves numbered chapters ("Chapter 3")."""
    return bool(entries) and all(_CHAPTER_PREFIX.match(e.label) for e in entries)


def _anchor_offset(markup: str, fragment: str) -> int:
    if not fragment:
        return 0
    match = re.search(r"<[^>]*\b(?:id|name)\s*=\s*[\"']" + re.escape(fragment) + r"[\"']", markup)
    return match.start() if match else 0


def epub_chapters(data: bytes) -> tuple[str, list[BookChapter]]:
    """An EPUB's chapters from its own contents, else from its headings."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, OSError) as exc:
        raise ChapterError(f"not a readable EPUB: {exc}") from exc
    with archive:
        try:
            documents = [name for name, _ident, _hint in _spine(archive)]
            source, toc_path, entries = _epub_toc(archive)
        except EpubTextError as exc:
            raise ChapterError(str(exc)) from exc
        markups = [archive.read(name).decode("utf-8", "replace") for name in documents]
        position = {name: i for i, name in enumerate(documents)}

        starts: list[tuple[int, int, str]] = []
        toc_base = posixpath.dirname(toc_path)
        for entry in _flatten(entries):
            href, _, fragment = entry.href.partition("#")
            if not href or not entry.label:
                continue
            name = _match_member(archive, posixpath.normpath(posixpath.join(toc_base, href)))
            if name not in position:
                continue
            doc = position[name]
            starts.append((doc, _anchor_offset(markups[doc], fragment), entry.label))
        starts.sort(key=lambda s: (s[0], s[1]))
        deduped: list[tuple[int, int, str]] = []
        for start in starts:
            if deduped and deduped[-1][:2] == start[:2]:
                continue
            deduped.append(start)

        if len(deduped) < 2:
            text = "\n\n".join(t for t in (markup_text(m) for m in markups) if t)
            return "headings", text_chapters(text)

        chapters: list[BookChapter] = []
        for i, (doc, offset, label) in enumerate(deduped):
            end_doc, end_offset = (deduped[i + 1][0], deduped[i + 1][1]) if i + 1 < len(deduped) else (len(markups), 0)
            pieces: list[str] = []
            if end_doc == doc:
                pieces.append(markups[doc][offset:end_offset])
            else:
                pieces.append(markups[doc][offset:])
                pieces.extend(markups[doc + 1:end_doc])
                if end_doc < len(markups) and end_offset:
                    pieces.append(markups[end_doc][:end_offset])
            text = "\n\n".join(t for t in (markup_text(p) for p in pieces) if t)
            chapters.append(BookChapter(label=label, text=text))
    return source, number_chapters(chapters)


# --- PDF ---


class PdfNoTextLayer(ChapterError):
    """A PDF whose pages carry no text at all: a scan with no OCR layer."""


@dataclass
class PdfPages:
    """The text of a PDF's pages, read in order up to a character budget."""

    #: One string per page read, in order (``pages[0]`` is page 1).
    pages: list[str]
    page_count: int
    #: True when reading stopped at the budget before the last page.
    truncated: bool
    reader: object = None

    @property
    def has_text(self) -> bool:
        return any(p.strip() for p in self.pages)


def read_pdf_pages(data: bytes, max_chars: int | None = None) -> PdfPages:
    """Read a PDF's pages in order, stopping once ``max_chars`` of text is held.

    One page is extracted at a time and nothing past the budget is kept, so a
    2,000-page PDF costs no more memory than its first ``max_chars`` of text. A
    page pypdf cannot read is an empty page, not a failed book. An encrypted PDF
    that opens with the empty password (most "protected" ebooks) is read.
    """
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            raise ChapterError("the PDF is encrypted with a password, so its text cannot be read")
        page_count = len(reader.pages)
    except ChapterError:
        raise
    except Exception as exc:
        raise ChapterError(f"not a readable PDF: {exc}") from exc
    budget = None if max_chars is None else max(int(max_chars), 0)
    pages: list[str] = []
    used = 0
    truncated = False
    for index in range(page_count):
        if budget is not None and used >= budget:
            truncated = True
            break
        try:
            text = reader.pages[index].extract_text() or ""
        except Exception:
            text = ""
        if budget is not None and used + len(text) > budget:
            text = text[: budget - used]
            truncated = True
        pages.append(text)
        used += len(text)
    return PdfPages(pages=pages, page_count=page_count, truncated=truncated, reader=reader)


def _outline_starts(reader) -> list[tuple[int, str]]:
    """``(page index, title)`` for each chapter-level bookmark, in page order."""
    starts: list[tuple[int, str]] = []

    def walk(items: list) -> None:
        tops = [i for i in items if not isinstance(i, list)]
        previous = None
        for item in items:
            if isinstance(item, list):
                label = _norm_space(str(getattr(previous, "title", "") or ""))
                children = [i for i in item if not isinstance(i, list)]
                if len(tops) == 1 or _PART_LABEL.match(label) or (
                    children and all(_CHAPTER_PREFIX.match(str(getattr(c, "title", "") or "")) for c in children)
                ):
                    walk(item)
                continue
            previous = item
            try:
                page = reader.get_destination_page_number(item)
            except Exception:
                continue
            title = _norm_space(str(getattr(item, "title", "") or ""))
            if title and page is not None and page >= 0:
                starts.append((page, title))

    try:
        walk(list(reader.outline or []))
    except Exception:
        return []
    starts.sort(key=lambda s: s[0])
    return starts


def _outline_chapters(pages: list[str], starts: list[tuple[int, str]]) -> list[BookChapter]:
    chapters: list[BookChapter] = []
    for i, (page, label) in enumerate(starts):
        end = max(starts[i + 1][0] if i + 1 < len(starts) else len(pages), page + 1)
        text = "\n\n".join(p.strip() for p in pages[page:end] if p.strip())
        chapters.append(BookChapter(label=label, text=text))
    return number_chapters(chapters)


def pdf_chapters(data: bytes, max_chars: int | None = None) -> tuple[str, list[BookChapter]]:
    """A PDF's chapters from its outline (bookmarks), else from its headings."""
    doc = read_pdf_pages(data, max_chars)
    starts = [s for s in _outline_starts(doc.reader) if s[0] < max(len(doc.pages), 1)]
    if len(starts) < 2:
        return "headings", text_chapters("\n\n".join(doc.pages))
    return "pdf-outline", _outline_chapters(doc.pages, starts)


def _page_label(first: int, last: int) -> str:
    return f"Page {first}" if first == last else f"Pages {first}-{last}"


def pdf_sections(data: bytes, max_chars: int | None = None) -> tuple[str, list[BookChapter]]:
    """A PDF cut into sections for the search index.

    With bookmarks, one section per chapter, labelled with the bookmark's title
    (the pages before the first bookmark become one unnumbered "Pages 1-N"
    section, so nothing is dropped). Without bookmarks, one section per page,
    labelled "Page N", so a search hit still says where in the book it is.
    Raises :class:`PdfNoTextLayer` for a PDF with no text on any page read.
    """
    doc = read_pdf_pages(data, max_chars)
    if not doc.has_text:
        raise PdfNoTextLayer(
            f"no text layer on any of its {doc.page_count} pages (a scan that has not been OCRed)"
        )
    starts = [s for s in _outline_starts(doc.reader) if s[0] < len(doc.pages)]
    if len(starts) >= 2:
        chapters = _outline_chapters(doc.pages, starts)
        first = starts[0][0]
        front = "\n\n".join(p.strip() for p in doc.pages[:first] if p.strip())
        if front:
            label = _page_label(1, first)
            chapters.insert(0, BookChapter(label=label, text=front, title=label, matter=True))
        return "pdf-outline", chapters
    sections = [
        BookChapter(label=f"Page {i + 1}", text=text.strip(), title=f"Page {i + 1}")
        for i, text in enumerate(doc.pages)
        if text.strip()
    ]
    return "pdf-pages", sections


class OpenedBook:
    """A book opened once and kept between requests (``book_cache``).

    EPUB and TXT books are parsed whole (their chapters are cheap to cut once the
    file is unzipped). A PDF with bookmarks is not: its outline gives the chapters
    and their pages, and a chapter's pages are extracted only when that chapter is
    read, each page once. Extracting every page of a 250-page PDF to answer for one
    chapter was most of a 27 s chapter read. A PDF without bookmarks still needs all
    its text to find the headings, which is then kept.
    """

    def __init__(self, fmt: str, source: str, chapters: list[BookChapter], *, reader: object = None,
                 page_count: int = 0, file_bytes: int = 0) -> None:
        self.fmt = fmt.upper()
        self.source = source
        self.chapters = chapters
        self.page_count = page_count
        self.file_bytes = file_bytes
        self._reader = reader
        self._pages: dict[int, str] = {}
        self._lock = threading.Lock()
        #: Pages extracted so far (each page is extracted at most once).
        self.pages_extracted = 0

    def _page(self, index: int) -> str:
        if index not in self._pages:
            try:
                text = self._reader.pages[index].extract_text() or ""  # type: ignore[attr-defined]
            except Exception:
                text = ""
            self._pages[index] = text
            self.pages_extracted += 1
        return self._pages[index]

    def chapter_text(self, index: int) -> str:
        """The chapter's text, reading its pages now if they have not been read."""
        chapter = self.chapters[index]
        if chapter.pages is None:
            return chapter.text
        with self._lock:
            if chapter.pages is not None:
                first, end = chapter.pages
                chapter.text = "\n\n".join(t.strip() for t in (self._page(i) for i in range(first, end)) if t.strip())
                chapter.pages = None
        return chapter.text

    def whole_text(self) -> str:
        """The whole book's text (a PDF's pages in order, an EPUB's chapters)."""
        if self.fmt == "PDF" and self._reader is not None:
            with self._lock:
                pages = [self._page(i).strip() for i in range(self.page_count)]
            return "\n\n".join(p for p in pages if p)
        return "\n\n".join(c.text.strip() for c in self.chapters if c.text.strip())

    def size_bytes(self) -> int:
        """What this costs in memory, roughly: the PDF kept for its reader, plus the text held."""
        text = sum(len(c.text) for c in self.chapters) + sum(len(t) for t in self._pages.values())
        return (self.file_bytes if self._reader is not None else 0) + text


def open_book(data: bytes, fmt: str) -> OpenedBook:
    """Open a book file for chapter reads; a PDF's chapter text is read lazily."""
    fmt = fmt.upper()
    if fmt != "PDF":
        source, chapters = read_book_chapters(data, fmt)
        return OpenedBook(fmt, source, chapters)
    head = read_pdf_pages(data, max_chars=0)  # opens (and decrypts) it, reads no page
    starts = [s for s in _outline_starts(head.reader) if 0 <= s[0] < head.page_count]
    if len(starts) < 2:
        doc = read_pdf_pages(data)
        return OpenedBook(fmt, "headings", text_chapters("\n\n".join(doc.pages)), page_count=doc.page_count)
    chapters: list[BookChapter] = []
    for i, (page, label) in enumerate(starts):
        end = max(starts[i + 1][0] if i + 1 < len(starts) else head.page_count, page + 1)
        chapters.append(BookChapter(label=label, pages=(page, end)))
    return OpenedBook(fmt, "pdf-outline", number_chapters(chapters), reader=head.reader,
                      page_count=head.page_count, file_bytes=len(data))


# --- Plain text ---

_TEXT_PATTERNS = (
    re.compile(
        r"^(?:chapter|chap\.)\s+(?P<num>\d{1,3}|[ivxlcdm]{1,8}|" + _NUMBER_WORD + r")\b[\s.:\-\u2013\u2014]*(?P<title>.{0,80})$",
        re.IGNORECASE,
    ),
    re.compile(r"^(?P<num>\d{1,3})\.\s+(?P<title>[A-Z\"'].{0,78})$"),
    re.compile(r"^(?P<num>[IVXLC]{1,7})\.?(?:\s+(?P<title>[A-Z].{0,78}))?$"),
)


def text_chapters(text: str) -> list[BookChapter]:
    """Chapters found from headings in plain text ("CHAPTER FOUR", "4. Title", "IV").

    A contents page lists the same headings with nothing under them, so when a
    number appears twice the occurrence with the most text under it wins.
    """
    lines = text.split("\n")
    for pattern in _TEXT_PATTERNS:
        hits: list[tuple[int, int, str]] = []
        for i, raw in enumerate(lines):
            line = raw.strip()
            if not line or len(line) > 90:
                continue
            match = pattern.match(line)
            if not match:
                continue
            number = number_of(match.group("num"))
            if not number:
                continue
            title = (match.group("title") or "").strip(" .:-")
            if not title:
                for nxt in lines[i + 1:i + 4]:
                    nxt = nxt.strip()
                    if nxt:
                        if len(nxt) <= 80 and not any(p.match(nxt) for p in _TEXT_PATTERNS):
                            title = nxt
                        break
            hits.append((i, number, title))
        if len(hits) < 2:
            continue
        sections: list[tuple[int, str, str]] = []
        for k, (i, number, title) in enumerate(hits):
            end = hits[k + 1][0] if k + 1 < len(hits) else len(lines)
            body = "\n".join(lines[i:end]).strip()
            sections.append((number, title, body))
        best: dict[int, tuple[int, str, str]] = {}
        for number, title, body in sections:
            if number not in best or len(body) > len(best[number][2]):
                best[number] = (number, title, body)
        kept = [s for s in sections if best[s[0]] == s]
        seen: set[int] = set()
        chapters: list[BookChapter] = []
        first_line = hits[0][0]
        front = "\n".join(lines[:first_line]).strip()
        if len(front) >= 40:
            chapters.append(BookChapter(label="Front matter", text=front, matter=True, title="Front matter"))
        for number, title, body in kept:
            if number in seen:
                continue
            seen.add(number)
            label = f"Chapter {number}" + (f": {title}" if title else "")
            chapters.append(BookChapter(label=label, text=body, number=number, title=title or label))
        return chapters
    whole = text.strip()
    return [BookChapter(label="Whole book", text=whole, title="Whole book")] if whole else []


# --- Choosing one ---


def _norm_title(text: str) -> str:
    text = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode()
    text = re.sub(r"[^\w\s]", " ", text.lower())
    words = [w for w in text.split() if w not in ("the", "a", "an")]
    return " ".join(words)


def find_chapter(chapters: list[BookChapter], ref: object) -> int:
    """The index of the chapter ``ref`` names (a number or a title), or raise."""
    number = parse_chapter_ref(ref)
    if number is not None:
        for i, chapter in enumerate(chapters):
            if chapter.number == number:
                return i
        numbered = [c.number for c in chapters if c.number is not None]
        span = f"chapters {min(numbered)} to {max(numbered)}" if numbered else "no numbered chapters"
        raise ChapterError(f"This book has no chapter {number}; it has {span}.")
    wanted = _norm_title(str(ref))
    if not wanted:
        raise ChapterError("Name a chapter by number (4, four, IV) or by its title.")
    names = [(_norm_title(c.title), _norm_title(c.label)) for c in chapters]
    for i, (title, label) in enumerate(names):
        if wanted in (title, label):
            return i
    for i, (title, label) in enumerate(names):
        if wanted and (wanted in title or wanted in label):
            return i
    scored = sorted(
        ((max(SequenceMatcher(None, wanted, t).ratio(), SequenceMatcher(None, wanted, la).ratio()), i)
         for i, (t, la) in enumerate(names)),
        reverse=True,
    )
    if scored and scored[0][0] >= 0.75:
        return scored[0][1]
    raise ChapterError(f"No chapter titled {str(ref)!r} in this book.")


def split_parts(text: str, max_chars: int = DEFAULT_PART_CHARS) -> list[str]:
    """Split a long chapter at paragraph breaks into parts of at most ``max_chars``."""
    limit = max(int(max_chars), 500)
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    current = ""
    for paragraph in text.split("\n\n"):
        while len(paragraph) > limit:
            if current:
                parts.append(current)
                current = ""
            cut = paragraph.rfind(" ", 0, limit)
            cut = cut if cut > limit // 2 else limit
            parts.append(paragraph[:cut].rstrip())
            paragraph = paragraph[cut:].lstrip()
        candidate = f"{current}\n\n{paragraph}" if current else paragraph
        if len(candidate) > limit:
            parts.append(current)
            current = paragraph
        else:
            current = candidate
    if current:
        parts.append(current)
    return parts


def read_book_chapters(data: bytes, fmt: str) -> tuple[str, list[BookChapter]]:
    """Chapters of a book file in one of the formats read here (EPUB, PDF, TXT)."""
    fmt = fmt.upper()
    if fmt == "EPUB":
        return epub_chapters(data)
    if fmt == "PDF":
        return pdf_chapters(data)
    if fmt == "TXT":
        return "headings", text_chapters(data.decode("utf-8", "replace"))
    raise ChapterError(f"No chapter reader for {fmt} files; convert the book to EPUB in Calibre.")


def read_book_text(data: bytes, fmt: str) -> str:
    """A whole book's text, for a format read here (PDF pages in order, EPUB chapters, TXT)."""
    fmt = fmt.upper()
    if fmt == "PDF":
        return "\n\n".join(p.strip() for p in read_pdf_pages(data).pages if p.strip())
    if fmt == "TXT":
        return data.decode("utf-8", "replace")
    if fmt == "EPUB":
        return "\n\n".join(c.text.strip() for c in epub_chapters(data)[1] if c.text.strip())
    raise ChapterError(f"No text reader for {fmt} files; convert the book to EPUB in Calibre.")


# --- Contents only (the catalogue card) ---


def contents_titles(data: bytes, fmt: str) -> list[str]:
    """The titles in a book's own table of contents, without reading its text.

    An EPUB's nav document or NCX, or a PDF's bookmarks: what the search index
    shows of a book's structure (``CalibreStorageProvider.catalogue_card``).
    Reading the chapters themselves would parse every page; this reads one small
    document. A book whose contents cannot be read has none (an empty list),
    never an error: its card is still written without them.
    """
    fmt = str(fmt or "").upper()
    labels: list[str] = []
    try:
        if fmt == "PDF":
            head = read_pdf_pages(data, max_chars=0)
            labels = [title for _page, title in _outline_starts(head.reader)]
        elif fmt == "EPUB":
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                _source, _path, entries = _epub_toc(archive)
            labels = [entry.label for entry in _flatten(entries)]
    except Exception as exc:  # a broken file is a card without contents
        log.info("Contents of a %s could not be read: %s", fmt or "book", exc)
        return []
    titles: list[str] = []
    for label in labels:
        title = _norm_space(str(label or ""))
        if title and (not titles or titles[-1] != title):
            titles.append(title)
    return titles

"""Extract Bible text from an EPUB study Bible.

Many commercial study Bibles ship as EPUB where every verse carries a machine
readable anchor whose id encodes the canonical position, for example
``id="v04020014"`` is book 04 chapter 02 verse 0014. When those anchors exist we
never have to guess where a verse starts, we only have to read the text between
one anchor and the next.

Verse text and study apparatus are kept apart. Text inside ``<p>`` is harvested
and text inside ``<sup>`` is discarded, so verse numbers, footnote letters and
Strong's markers vanish. A verse runs from its own anchor to the next anchor,
which keeps sidebars, commentary and concordances out of the verse text for
free. The same anchors then say where the study material lives: the verse
paragraph links out to its commentary (``#com…``) and to its footnotes
(``#fn…``), so those blocks are collected separately and joined back onto the
verse they belong to. Nothing is thrown away unread: every other paragraph
class is counted and reported under ``skipped`` so an operator can see exactly
what the extractor declined to keep.
"""

from __future__ import annotations

import re
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from html.parser import HTMLParser
from xml.etree import ElementTree as ET

from services.bible.books import BOOKS, BOOK_BY_OSIS, BOOK_ORDER, resolve_book
from services.bible.corpus import CorpusError

EPUB_KIND = "jarvis.bible.epub"

VERSE_ANCHOR = re.compile(r"^v(\d{2})(\d{3})(\d{1,4})$")

_DROP_TAGS = frozenset({"script", "style", "aside", "svg"})
_WHITESPACE = re.compile(r"\s+")

# Study Bibles mark flow text with css classes. Measured against the NKJV Study
# Bible: a verse anchor sits on sl1/slf/paft/pf/bq*/pcon/sll, and the lines that
# continue a verse are always the poetry and block-quote classes below. Every
# other class (ah headings, sb* sidebars, fn/in1/inh notes, com commentary, glo
# glossary, toc2, nl* numbered lists) is study apparatus and ends the verse --
# with one exception: a paragraph carrying no class at all immediately follows
# the verse paragraph whenever a verse is set as two unlabelled lines.
_CONTINUES_VERSE = frozenset({
    "sl", "sl1", "sl2", "sl3", "slf", "slb", "sll", "sls", "slr",
    "bq", "bql", "bqs", "bqf", "pc", "pcon", "pf", "paft",
})

# Study material we keep and join back onto a verse. Commentary ids encode the
# same nine digits as the verse anchor they belong to; footnote ids are a flat
# sequence number that the verse paragraph references.
_COMMENTARY_ID = re.compile(r"^comx?\d{8}$")
_FOOTNOTE_ID = re.compile(r"^fn\d{4,6}$")
_COMMENTED_ID = re.compile(r"id=(?:&quot;|&#34;|[\"'])?(comx?\d{8})")
_STUDY_IDS = re.compile(r"^(comx?\d{8}|fn\d{4,6})$")

# A commentary block opens by linking back to the verse or verses it covers, so
# its text starts with a bare reference ("24:13, 14" or "3:2 born"). That is
# navigation, not prose, and it is stripped before the note is stored.
_LEADING_REFS = re.compile(r"^[\d\s:;,.\u2013\u2014\-]+")

# Inline markers that ride along with the verse text: the verse number itself,
# cross-reference and footnote letters, chapter apparatus ("GENESIS 1" spans at
# the head of a verse paragraph), and in-verse index terms. Measured across the
# ESV, NIV and NKJV study-Bible exports; every name is a generic class, never a
# translation code, so a new export that reuses these names is absorbed for
# free. Suppressed the same way superscripts are -- spans and bare links alike.
_MARKER_SPANS = frozenset({
    "verse-num", "crossref", "footnote",
    "ver", "ver-b", "book-name", "chapter-num",
    "enref", "fnref", "idx",
})

# Headings (h1-h6) are pericope titles, never scripture. An export that sets
# them inside an open verse would otherwise glue the title onto the verse text.
_HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})

# Some exports glue the verse's own number to the first word of its text
# ("1In the beginning"). The digits are only stripped when they equal the
# verse's canonical position and are immediately followed by a letter or a
# quote, so "20 men" (verse 2) and "1,600" are never mangled.
_OWN_NUMBER_FOLLOW = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZ\u2018\u201c\"'([")


def _strip_own_number(text: str, verse: int) -> str:
    digits = str(verse)
    if (
        text[: len(digits)] == digits
        and len(text) > len(digits)
        and text[len(digits)] in _OWN_NUMBER_FOLLOW
    ):
        return text[len(digits):].lstrip()
    return text

# Paragraph classes that are back matter or apparatus rather than verse text.
# They are counted and reported so nothing disappears without being named.
_BACK_MATTER = frozenset({
    "in1", "in2", "inh", "glo", "toc2", "nl", "nl1", "bibletimes", "idx",
})


_OPF_NS = "{http://www.idpf.org/2007/opf}"
_DC_NS = "{http://purl.org/dc/elements/1.1/}"

# A commentary with no verse anchors still names its position: a section title
# ends with the passage it covers ("... (1:1-4)") beneath a chapter heading that
# reads "<Book> <chapter>". Anchors are what the verse harvester trusts, so when
# none exist this is the only canonical position such a source carries.
_SECTION_REF = re.compile(r"\((\d{1,3}):(\d{1,3})(?:\s*[\u2013\u2014-]\s*\d{0,3}:?\s*(\d{1,3}))?\)\s*$")
_HEADING_CHAPTER = re.compile(r"^(.*?)\s+(\d{1,3})$")
# How far ahead of the expected number a verse may legitimately appear: a
# translation can leave a number out of sequence entirely (the NLT has no
# John 5:4), and the next verse is then simply the next one it does have.
_MAX_VERSE_JUMP = 10
# Verse numbers set as a range ("3-4") share one paragraph of prose; the
# numbers appear in the same superscript span the single numbers use.
_RANGE_SPAN = re.compile(r"^(\d{1,3})-(\d{1,3})$")
# A range may span a whole census listing ("6-19"), which is much wider than
# a single omitted number but still bounded by a chapter.
_MAX_RANGE_SPAN = 30
# Some layouts set the number as plain text at the head of the paragraph
# rather than inside the superscript span ("5-8 The divisions of Judah...").
_LEADING_NUMBER = re.compile(r"^(\d{1,3})(?:-(\d{1,3}))?(?=\s|$)")
_TABLE_TAGS = frozenset({"table", "thead", "tbody", "tfoot", "tr", "td", "th", "caption"})

_BOOK_NUMBER_BY_NAME = {
    str(entry["name"]).casefold(): position
    for position, entry in enumerate(BOOKS, start=1)
}
# Quoted scripture sits in its own shaded paragraph; keeping it would file the
# Bible text as commentary about itself.
_SCRIPTURE_MARKER = "background-color"


class EpubImportError(CorpusError):
    """Raised when an EPUB cannot be trusted as a complete Bible."""


@dataclass(frozen=True)
class StudyNote:
    """One piece of study material belonging to a single verse."""

    book: int
    chapter: int
    verse: int
    kind: str
    body: str
    ordinal: int = 0

    @property
    def key(self) -> tuple[int, int, int]:
        return (self.book, self.chapter, self.verse)

    def as_dict(self) -> dict:
        return {
            "book": self.book,
            "chapter": self.chapter,
            "verse": self.verse,
            "kind": self.kind,
            "ordinal": self.ordinal,
            "body": self.body,
        }


@dataclass
class StudyReport:
    """What study material was found, joined and deliberately left behind."""

    commentary: int = 0
    footnotes: int = 0
    missing_commentary: int = 0
    missing_footnotes: int = 0
    skipped: dict[str, int] = field(default_factory=dict)
    blocks_seen: int = 0
    orphan_footnotes: int = 0

    def as_dict(self) -> dict:
        return {
            "commentary": self.commentary,
            "footnotes": self.footnotes,
            "missing_commentary": self.missing_commentary,
            "missing_footnotes": self.missing_footnotes,
            "orphan_footnotes": self.orphan_footnotes,
            "blocks_seen": self.blocks_seen,
            "skipped": dict(self.skipped),
        }


@dataclass
class BookReport:
    """What the extractor found for one canonical book."""

    osis: str
    name: str
    chapters: int
    expected_chapters: int
    verses: int
    empty_chapters: list[int] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.chapters == self.expected_chapters and not self.problems

    def as_dict(self) -> dict:
        return {
            "osis": self.osis,
            "name": self.name,
            "chapters": self.chapters,
            "expected_chapters": self.expected_chapters,
            "verses": self.verses,
            "empty_chapters": list(self.empty_chapters),
            "problems": list(self.problems),
            "ok": self.ok,
        }


def _class_of(attrs: list[tuple[str, str | None]]) -> str:
    return (dict(attrs).get("class") or "").strip().lower()


def _spine_order(archive: zipfile.ZipFile) -> list[str]:
    """Document names in reading order, falling back to a plain sort."""
    for candidate in archive.namelist():
        if not candidate.lower().endswith(".opf"):
            continue
        try:
            root = ET.fromstring(archive.read(candidate))
        except ET.ParseError:
            continue
        base = candidate.rsplit("/", 1)[0] + "/" if "/" in candidate else ""
        order: list[str] = []
        for item in root.iter(f"{_OPF_NS}spine"):
            ref = item.get("idref")
            if not ref:
                continue
            for node in root.iter(f"{_OPF_NS}item"):
                if node.get("id") == ref:
                    href = node.get("href")
                    if href:
                        order.append(base + href)
                    break
        if order:
            return order
    return sorted(archive.namelist())


def _title(archive: zipfile.ZipFile) -> str:
    for candidate in archive.namelist():
        if not candidate.lower().endswith(".opf"):
            continue
        try:
            root = ET.fromstring(archive.read(candidate))
        except ET.ParseError:
            continue
        node = root.find(f".//{_DC_NS}title")
        if node is not None and node.text:
            return node.text.strip()
    return ""


class _VerseHarvester(HTMLParser):
    """Collects verse text keyed by the canonical position in each anchor.

    A verse runs from its own anchor to the next anchor. In the poetic books the
    anchor sits on the opening ``<p>`` of the verse and the remaining lines are
    sibling ``<p>``s with a poetry class, so those lines are merged rather than
    treated as a boundary. Anything with a class outside
    ``_CONTINUES_VERSE`` closes the verse and its text is study apparatus.

    ``links`` maps each verse to the study blocks it points at, which is how
    commentary and footnotes are later attached to the verse they explain.
    ``skipped`` counts every paragraph class that was declined so the report can
    name material this extractor did not keep.

    Three export shapes are absorbed here, all keyed on markup structure and
    class names rather than any translation: a section heading that *carries*
    the verse anchor (the ESV Study Bible parks ``id`` on ``<p class="heading">``
    and puts the verse in the next paragraph) defers to that paragraph rather
    than storing the heading as scripture; inline marker spans and bare marker
    links (verse numbers, cross-reference and footnote letters, chapter
    apparatus, index terms -- see ``_MARKER_SPANS``) are dropped like
    superscripts; and h1-h6 pericope titles end the verse instead of gluing
    their text onto it. A verse's own number glued to its first word is
    stripped at flush when it matches the canonical position.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.verses: dict[tuple[int, int, int], str] = {}
        self.links: dict[tuple[int, int, int], list[str]] = {}
        self.skipped: Counter[str] = Counter()
        self.blanks: list[tuple[int, int, int]] = []
        self._verse: tuple[int, int, int] | None = None
        self._buffer: list[str] = []
        self._targets: list[str] = []
        self._sup = 0
        self._hidden = 0
        self._in_paragraph = False
        self._paragraph_open = False
        self._after_verse = False
        self._marker = 0
        self._heading_anchor = False
        self._after_heading = False

    def _start(self, match: re.Match[str], heading_anchor: bool = False) -> None:
        self._flush()
        self._verse = (int(match[1]), int(match[2]), int(match[3]))
        self._buffer = []
        self._targets = []
        self._heading_anchor = heading_anchor
        if self._in_paragraph:
            self._after_verse = True

    def _target(self, ident: str) -> None:
        if self._verse is not None and _STUDY_IDS.match(ident) and ident not in self._targets:
            self._targets.append(ident)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        data = dict(attrs)
        if tag == "p":
            self._in_paragraph = True
            css = _class_of(data)
            if self._after_heading and self._verse is not None:
                self._paragraph_open = True
                self._after_heading = False
            elif not css and self._after_verse:
                self._paragraph_open = True
            else:
                self._paragraph_open = css in _CONTINUES_VERSE
                if not self._paragraph_open and css:
                    self.skipped[css] += 1
            if not self._paragraph_open:
                self._flush()
                self._after_verse = False
            elif self._verse is not None and self._buffer:
                self._buffer.append(" ")
        if tag in _DROP_TAGS or tag in _HEADING_TAGS:
            self._hidden += 1
            if tag in _HEADING_TAGS:
                self._flush()
        elif tag == "sup":
            self._sup += 1
        elif tag in {"span", "a"} and _class_of(data) in _MARKER_SPANS:
            self._marker += 1
        href = (data.get("href") or "").strip()
        if "#" in href:
            self._target(href.split("#", 1)[1])
        ident = (data.get("id") or "").strip()
        if ident.endswith("r") and _FOOTNOTE_ID.match(ident[:-1]):
            self._target(ident[:-1])
        match = VERSE_ANCHOR.match(ident)
        if match:
            self._start(match, heading_anchor=(tag == "p" and _class_of(data) == "heading"))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag in _DROP_TAGS or tag in {"sup", "p", "span", "a"}:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag == "sup" and self._sup:
            self._sup -= 1
        elif (tag in _DROP_TAGS or tag in _HEADING_TAGS) and self._hidden:
            self._hidden -= 1
        elif tag in {"span", "a"} and self._marker:
            self._marker -= 1
        elif tag == "p":
            self._in_paragraph = False
            if self._heading_anchor:
                self._heading_anchor = False
                self._after_heading = True
            elif not self._paragraph_open:
                self._flush()
            self._paragraph_open = False

    def handle_data(self, data: str) -> None:
        if (
            self._verse is None
            or self._sup
            or self._hidden
            or self._marker
            or self._heading_anchor
            or not self._in_paragraph
        ):
            return
        self._buffer.append(data)

    def _flush(self) -> None:
        if self._verse is None:
            return
        text = _WHITESPACE.sub(" ", "".join(self._buffer)).strip()
        text = _strip_own_number(text, self._verse[2])
        if text:
            self.verses.setdefault(self._verse, text)
            if self._targets:
                self.links.setdefault(self._verse, []).extend(self._targets)
        else:
            self.blanks.append(self._verse)
        self._verse = None
        self._buffer = []
        self._targets = []

    def close(self) -> None:
        super().close()
        self._flush()


class _StudyHarvester(HTMLParser):
    """Collects the study blocks a study Bible links out from its verses.

    Commentary paragraphs are keyed by ``id="com…"`` and extended commentary
    paragraphs carry the same id inside an html comment. Footnote paragraphs
    are keyed by ``id="fn…"`` and their first link points back at the reference
    that raised them, which is how they are attributed when the caller wants
    the reverse map.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: dict[str, str] = {}
        self.backlinks: dict[str, str] = {}
        self._ids: list[str] = []
        self._links: list[str] = []
        self._buffer: list[str] = []
        self._sup = 0
        self._hidden = 0
        self._in_paragraph = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        data = dict(attrs)
        ident = (data.get("id") or "").strip()
        href = (data.get("href") or "").strip()
        if tag in _DROP_TAGS:
            self._hidden += 1
            return
        if tag == "sup":
            self._sup += 1
            return
        if tag == "p":
            self._in_paragraph = True
            self._buffer = []
            self._ids = []
            self._links = []
        if _COMMENTARY_ID.match(ident) or _FOOTNOTE_ID.match(ident):
            self._ids.append(ident)
        if "#" in href:
            self._links.append(href.split("#", 1)[1])

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag in _DROP_TAGS or tag in {"sup", "p"}:
            self.handle_endtag(tag)

    def handle_comment(self, data: str) -> None:
        match = _COMMENTED_ID.search(data)
        if match and self._in_paragraph and match[1] not in self._ids:
            self._ids.append(match[1])

    def handle_endtag(self, tag: str) -> None:
        if tag == "sup" and self._sup:
            self._sup -= 1
            return
        if tag in _DROP_TAGS:
            self._hidden = max(0, self._hidden - 1)
            return
        if tag != "p":
            return
        self._in_paragraph = False
        body = _WHITESPACE.sub(" ", "".join(self._buffer)).strip()
        for ident in self._ids:
            if body and _COMMENTARY_ID.match(ident):
                body = _LEADING_REFS.sub("", body).strip() or body
            self.blocks[ident] = body
            for target in self._links:
                self.backlinks.setdefault(target, ident)
        self._buffer = []
        self._ids = []
        self._links = []

    def handle_data(self, data: str) -> None:
        if self._sup or self._hidden or not self._in_paragraph:
            return
        self._buffer.append(data)

    def close(self) -> None:
        super().close()
        self.handle_endtag("p")


class _CommentaryHarvester(HTMLParser):
    """Collects section-titled prose keyed by the passage each section covers.

    Used only when the archive carries no verse anchors at all: a modern
    commentary EPUB converted from a word processor has chapter headings
    ("Romans 1"), section titles ending in a passage reference ("... (1:1-4)")
    and shaded paragraphs quoting the scripture under discussion. Paragraphs
    before the first anchored section (introductions, prefaces) are counted as
    unanchored rather than guessed onto a verse, and shaded scripture is dropped
    so the note files the discussion, not the text it discusses.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.notes: list[StudyNote] = []
        self.unanchored_paragraphs = 0
        self._book: int | None = None
        self._heading_tag: str | None = None
        self._heading_text: list[str] = []
        self._paragraph: list[str] | None = None
        self._paragraph_style = ""
        self._section: dict | None = None
        self._ordinals: Counter[tuple[int, int, int]] = Counter()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._heading_tag = tag
            self._heading_text = []
        elif tag == "p":
            self._paragraph = []
            self._paragraph_style = next(
                (str(value) for key, value in attrs if key == "style"), ""
            )

    def handle_data(self, data: str) -> None:
        if self._heading_tag is not None:
            self._heading_text.append(data)
        elif self._paragraph is not None:
            self._paragraph.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._heading_tag is not None and tag == self._heading_tag:
            text = _WHITESPACE.sub(" ", "".join(self._heading_text)).strip()
            level, self._heading_tag, self._heading_text = self._heading_tag, None, []
            if level == "h1":
                self._close_section()
                self._book = self._book_of_heading(text)
            elif level in ("h3", "h4") and self._section is not None and text:
                self._section["parts"].append(text)
            return
        if tag == "p" and self._paragraph is not None:
            self._close_paragraph()

    @staticmethod
    def _book_of_heading(text: str) -> int | None:
        match = _HEADING_CHAPTER.match(text)
        if not match:
            return None
        return _BOOK_NUMBER_BY_NAME.get(str(match.group(1)).strip().casefold())

    def _close_paragraph(self) -> None:
        text = _WHITESPACE.sub(" ", "".join(self._paragraph or [])).strip()
        style, self._paragraph, self._paragraph_style = self._paragraph_style, None, ""
        if not text:
            return
        if _SCRIPTURE_MARKER in style:
            return
        anchor = _SECTION_REF.search(text)
        if anchor:
            if self._book is None:
                self.unanchored_paragraphs += 1
                return
            self._close_section()
            self._section = {
                "book": self._book,
                "chapter": int(anchor.group(1)),
                "verse": int(anchor.group(2)),
                "parts": [text],
            }
            return
        if self._section is None:
            self.unanchored_paragraphs += 1
            return
        self._section["parts"].append(text)

    def _close_section(self) -> None:
        if self._section is None:
            return
        body = "\n\n".join(self._section["parts"]).strip()
        key = (self._section["book"], self._section["chapter"], self._section["verse"])
        self._section = None
        if not body:
            return
        ordinal = self._ordinals[key]
        self._ordinals[key] = ordinal + 1
        self.notes.append(StudyNote(key[0], key[1], key[2], "commentary", body, ordinal))

    def close(self) -> None:
        super().close()
        if self._paragraph is not None:
            self._close_paragraph()
        self._close_section()


# Paragraph-level css classes that mark a heading rather than prose. The
# obfuscated class names of a converted epub mean the only stable signal is the
# declaration itself: a bold or large-font paragraph between two verses is a
# section title, and keeping it would file the title as the previous verse's
# tail.
_CSS_RULE = re.compile(r"\.([A-Za-z][\w-]*)\s*\{([^{}]*)\}")
_CSS_BOLD = re.compile(r"font-weight\s*:\s*(bold|[6-9]00)\b", re.I)
_CSS_EM_SIZE = re.compile(r"font-size\s*:\s*([\d.]+)\s*(em|rem)", re.I)
_CSS_PX_SIZE = re.compile(r"font-size\s*:\s*([\d.]+)\s*px", re.I)


def _strip_numbered_label(text: str) -> str:
    """Remove the ``11:1`` chapter label a pre-heading verse carries.

    Some typeset chapters open with their first verse *before* the heading
    paragraph, and that paragraph starts with a chapter-label span ("11:")
    followed by the verse number span, so the deferred text arrives as
    "11:1And you should..." -- the navigation is stripped so the verse reads
    the way it prints.
    """
    text = re.sub(r"^\d{1,3}:\s*", "", text)
    if text.startswith("1"):
        text = text[1:]
    return text.lstrip()


def _heading_classes(path: str) -> set[str]:
    """Lowercased paragraph classes the archive's stylesheets mark as headings.

    Only the paragraph classes are collected, and only when their declaration
    is bold or at least 1.2em, which is exactly how the observed layouts mark
    chapter and section titles. Class names are folded to lowercase because
    ``_class_of`` folds the class attribute the same way -- a mismatch here
    would disable heading detection in complete silence.
    """
    with zipfile.ZipFile(path) as archive:
        heading: set[str] = set()
        for name in archive.namelist():
            if not name.lower().endswith(".css"):
                continue
            text = archive.read(name).decode("utf-8", "replace")
            for rule in _CSS_RULE.finditer(text):
                css_class, body = rule.group(1), rule.group(2)
                bold = _CSS_BOLD.search(body)
                em = _CSS_EM_SIZE.search(body)
                px = _CSS_PX_SIZE.search(body)
                if bold or (em and float(em.group(1)) >= 1.2) or (px and float(px.group(1)) >= 19):
                    heading.add(css_class.lower())
        return heading


class _NumberedHarvester(HTMLParser):
    """Collects verse text from a translation that numbers verses in the prose.

    Many commercial EPUBs -- converted from typesetting rather than exported
    from a bible database -- carry no ``v`` anchors at all. What they do carry
    is structure: a chapter opens with a heading paragraph reading
    ``"<Book> <chapter>"`` as links, every verse begins with its number inside
    a small superscript span, and the paragraphs that follow continue the last
    verse started. A single paragraph often holds several verses, and some
    chapters open with a ``"8:"`` chapter label span, so the number spans are
    resolved the moment they close rather than at the paragraph boundary --
    that is where a verse boundary actually lives.

    Rules, in the order they apply:

    * anything nested in a ``<div>`` is an editorial box or cross-reference
      list, never scripture, so its content is declined before any other test;
    * a digit span equal to the next expected number splits the text there --
      the paragraph's text so far belongs to the open verse, everything after
      belongs to the new one. Sequential numbering is the whole reason this
      mode can be trusted, so a *leading* number out of sequence declines the
      paragraph rather than guessing; the same number in the middle of prose
      keeps its text, because losing scripture is worse than a stray numeral;
    * a heading paragraph re-anchors position to its book and chapter;
    * a heading class (from the stylesheet, or an id when the archive ships no
      stylesheet) is a section title and is declined;
    * anything else continues the open verse, or is declined when no verse is
      open so unsolicited prose can never silently become scripture.

    Two further shapes this reader meets: a paragraph numbered as a range
    ("3-4") is one piece of prose that *is* every verse it names, and a
    number set as plain text ("6-19 The divisions...") opens its verse the
    same way a span would. Tables and figures (camp diagrams) are apparatus
    whose cells must not glue onto the open verse -- but a table that *is*
    scripture (the census of Revelation 7) still opens verses from its own
    leading numbers; it just never continues one.

    Every decline is counted in ``declined`` so the report can name exactly
    what this reader did not keep, and chapters that end up empty still fail
    the per-book report rather than installing a short book.
    """

    def __init__(self, heading_classes: set[str]) -> None:
        super().__init__(convert_charrefs=True)
        self.verses: dict[tuple[int, int, int], str] = {}
        self.declined: Counter[str] = Counter()
        self._headings = set(heading_classes)
        self._div = 0
        self._hidden = 0
        self._sup = 0
        self._noterefs: list[bool] = []
        self._book: int | None = None
        self._chapter: int | None = None
        self._next_verse = 1
        self._open: tuple[int, int, int] | None = None
        self._open_parts: list[str] = []
        self._in_paragraph = False
        self._p_class = ""
        self._p_ident = ""
        self._p_div = 0
        self._p_links = 0
        self._buffer: list[str] = []
        self._seen = False
        self._number_parts: list[str] | None = None
        self._span_leading = False
        self._declined_p = False
        self._split_p = False
        self._p_first_digits: int | None = None
        self._stashed: dict[int, str] = {}
        self.absent_verses = 0
        self._table = 0
        self._range_end: int | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        data = dict(attrs)
        if tag == "div":
            self._div += 1
        elif tag in _TABLE_TAGS:
            self._table += 1
        elif tag in _DROP_TAGS:
            self._hidden += 1
        elif tag == "sup":
            self._sup += 1
        elif tag == "a":
            media = (data.get("epub:type") or "").lower()
            self._noterefs.append("noteref" in media)
            if self._in_paragraph:
                self._p_links += 1
        elif tag == "span":
            if self._in_paragraph and self._number_parts is None:
                self._number_parts = []
                self._span_leading = not self._seen
        elif tag == "p":
            if self._in_paragraph:
                self._close_paragraph()
            self._in_paragraph = True
            self._p_class = _class_of(data)
            self._p_ident = (data.get("id") or "").strip()
            self._p_div = self._div
            self._p_links = 0
            self._buffer = []
            self._seen = False
            self._number_parts = None
            self._span_leading = False
            self._declined_p = False
            self._split_p = False
            self._p_first_digits = None

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag in _DROP_TAGS or tag in {"sup", "a", "span", "p", "div"}:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag == "div":
            self._div = max(0, self._div - 1)
        elif tag in _TABLE_TAGS:
            self._table = max(0, self._table - 1)
        elif tag in _DROP_TAGS:
            self._hidden = max(0, self._hidden - 1)
        elif tag == "sup":
            self._sup = max(0, self._sup - 1)
        elif tag == "a":
            if self._noterefs:
                self._noterefs.pop()
        elif tag == "span":
            self._resolve_number_span()
        elif tag == "p":
            self._close_paragraph()

    def handle_data(self, data: str) -> None:
        if not self._in_paragraph or self._hidden or self._sup or self._declined_p:
            return
        if self._noterefs and any(self._noterefs):
            return
        if self._number_parts is not None:
            self._number_parts.append(data)
            return
        if not self._seen and not data.strip():
            return
        self._seen = True
        self._buffer.append(data)

    def _resolve_number_span(self) -> None:
        """Judge a closing span: a verse number, or just prose in a span."""
        if self._number_parts is None:
            return
        parts, self._number_parts = self._number_parts, None
        text = "".join(parts).strip()
        if self._declined_p:
            return
        if self._p_div > 0:
            return
        number: int | None = None
        range_end: int | None = None
        if text.isdigit():
            number = int(text)
        else:
            span = _RANGE_SPAN.match(text)
            if span and int(span.group(2)) - int(span.group(1)) <= _MAX_RANGE_SPAN:
                number, range_end = int(span.group(1)), int(span.group(2))
        if number is None:
            self._buffer.extend(parts)
            self._seen = True
            return
        if self._p_first_digits is None:
            self._p_first_digits = number
        context = self._book is not None and self._chapter is not None
        # A translation may simply not have the next number (NLT leaves out
        # John 5:4), so a small forward jump is the number itself, not damage.
        # A large jump means a paragraph was lost somewhere and guessing would
        # misfile the text, so it declines like any other out-of-sequence lead.
        if context and self._next_verse < number <= self._next_verse + _MAX_VERSE_JUMP:
            self.absent_verses += number - self._next_verse
        elif not context or number != self._next_verse:
            if self._span_leading and number != 1:
                self._declined_p = True
                self.declined["a verse number out of sequence"] += 1
                self._buffer = []
            else:
                self._buffer.extend(parts)
                self._seen = True
            return
        if self._open is not None:
            if self._buffer:
                if self._open_parts:
                    self._open_parts.append(" ")
                self._open_parts.extend(self._buffer)
            self._flush_open()
        elif self._buffer:
            self.declined["prose with no position"] += 1
        self._buffer = []
        self._open = (self._book, self._chapter, number)
        self._range_end = range_end
        self._next_verse = (range_end if range_end is not None else number) + 1
        self._open_parts = []
        self._split_p = True

    def _close_paragraph(self) -> None:
        if not self._in_paragraph:
            return
        self._resolve_number_span()
        text = _WHITESPACE.sub(" ", "".join(self._buffer)).strip()
        css_class, ident = self._p_class, self._p_ident
        p_div, links, declined_p, split_p = self._p_div, self._p_links, self._declined_p, self._split_p
        first_digits = self._p_first_digits
        self._in_paragraph = False
        self._p_class = self._p_ident = ""
        self._p_div = self._p_links = 0
        self._buffer = []
        self._seen = False
        self._number_parts = None
        self._span_leading = False
        self._declined_p = False
        self._split_p = False
        self._p_first_digits = None

        if declined_p:
            return
        if p_div > 0:
            self.declined["editorial boxes"] += 1
            return
        if split_p:
            if text and self._open is not None:
                if self._open_parts:
                    self._open_parts.append(" ")
                self._open_parts.append(text)
            return
        if not text:
            return
        match = _HEADING_CHAPTER.match(text)
        if match and (links or self._heading_like(css_class, ident)):
            osis = resolve_book(str(match.group(1)))
            if osis:
                self._place(int(match.group(2)), BOOK_ORDER[osis])
                return
        if self._opens_from_leading_text(text):
            return
        if self._table > 0:
            self.declined["tables and figures"] += 1
            return
        if self._heading_like(css_class, ident):
            self.declined["section headings"] += 1
            return
        if self._open is None:
            if first_digits == 1:
                self._stashed.setdefault(1, _strip_numbered_label(text))
            else:
                self.declined["prose with no position"] += 1
            return
        if self._open_parts:
            self._open_parts.append(" ")
        self._open_parts.append(text)

    def _opens_from_leading_text(self, text: str) -> bool:
        """Open a verse when the paragraph *starts* with its number as text.

        The same judgement the superscript span gets, for layouts that set
        "5-8 The divisions..." as ordinary prose; the number is navigation and
        is stripped, the rest is the verse. Returns False when the text is not
        a usable leading number so the ordinary heading/prose rules apply.
        """
        lead = _LEADING_NUMBER.match(text)
        if not lead:
            return False
        first = int(lead.group(1))
        last = int(lead.group(2)) if lead.group(2) else first
        if last < first or last - first > _MAX_RANGE_SPAN:
            return False
        if self._book is None or self._chapter is None:
            return False
        if not self._next_verse <= first <= self._next_verse + _MAX_VERSE_JUMP:
            return False
        if first > self._next_verse:
            self.absent_verses += first - self._next_verse
        self._flush_open()
        self._open = (self._book, self._chapter, first)
        self._range_end = last if last > first else None
        self._next_verse = last + 1
        rest = text[lead.end():].strip()
        self._open_parts = [rest] if rest else []
        return True

    def _heading_like(self, css_class: str, ident: str) -> bool:
        """Is this paragraph a heading rather than prose?

        The stylesheet is the authority. When the archive ships no stylesheet
        at all the classes say nothing, so an id-bearing paragraph stands in --
        section titles carry their own id while verse text carries none; the
        observed continuation ids all start with "page" and are exempt.
        """
        if css_class and css_class in self._headings:
            return True
        if not self._headings and ident and not ident.lower().startswith("page"):
            return True
        return False

    def _place(self, chapter: int, book: int) -> None:
        self._flush_open()
        self._book = book
        self._chapter = chapter
        self._next_verse = 1
        opening = self._stashed.pop(1, None)
        self._stashed.clear()
        if opening:
            self._open = (book, chapter, 1)
            self._range_end = None
            self._open_parts = [opening]
            self._next_verse = 2

    def _flush_open(self) -> None:
        if self._open is None:
            self._range_end = None
            return
        key, parts = self._open, self._open_parts
        last = self._range_end if self._range_end is not None else key[2]
        self._open = None
        self._range_end = None
        self._open_parts = []
        text = _WHITESPACE.sub(" ", "".join(parts)).strip()
        if not text:
            self.declined["a verse that carried no text"] += 1
            return
        # A ranged paragraph ("3-4") is the whole text of every number it
        # names, so each number gets it rather than leaving a hole behind.
        for number in range(key[2], last + 1):
            verse_key = (key[0], key[1], number)
            if verse_key in self.verses:
                self.declined["a repeated verse"] += 1
                continue
            self.verses[verse_key] = text

    def close(self) -> None:
        super().close()
        if self._in_paragraph:
            self._close_paragraph()
        self._flush_open()
        if self._stashed:
            self.declined["a verse waiting for a heading that never came"] += len(self._stashed)
            self._stashed.clear()


def _documents(path: str) -> list[tuple[str, str]]:
    """Every html document in the archive paired with its decoded text."""
    try:
        archive = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise EpubImportError(f"{path} is not a readable epub: {exc}") from exc
    with archive:
        wanted = set(_spine_order(archive))
        names = [n for n in archive.namelist() if n.lower().endswith((".html", ".xhtml", ".htm"))]
        ordered = [n for n in names if n in wanted] + sorted(n for n in names if n not in wanted)
        return [(n, archive.read(n).decode("utf-8", "replace")) for n in ordered]


def extract(path: str) -> dict:
    """Read every verse and every study note out of the epub and report gaps.

    When the archive carries no verse anchors at all, the same bytes are first
    read as a commentary: section titles that name a passage become notes keyed
    to that passage, and the result is ``mode="commentary"`` so the importer
    knows there is no translation to install. If that finds nothing either, the
    bytes are read once more as a numbered translation -- chapters headed
    ``"<Book> <chapter>"`` with the verse number inside the text -- because a
    typeset EPUB has structure even when it has no anchors. Only a file with
    anchors, sections or a consistent verse numbering keeps the ordinary
    verse-mode result; anything less is refused by name with its per-book
    report rather than imported as a partial or guessed text.
    """
    verses: dict[tuple[int, int, int], str] = {}
    links: dict[tuple[int, int, int], list[str]] = {}
    blocks: dict[str, str] = {}
    skipped: Counter[str] = Counter()
    documents = _documents(path)
    for name, markup in documents:
        harvester = _VerseHarvester()
        harvester.feed(markup)
        harvester.close()
        for key, text in harvester.verses.items():
            verses.setdefault(key, text)
        for key, targets in harvester.links.items():
            links.setdefault(key, []).extend(targets)
        skipped.update(harvester.skipped)
        study = _StudyHarvester()
        study.feed(markup)
        study.close()
        for ident, body in study.blocks.items():
            if body:
                blocks.setdefault(ident, body)

    notes, study_report = _join_notes(links, blocks)
    study_report.skipped = dict(sorted(skipped.items(), key=lambda kv: -kv[1]))
    study_report.blocks_seen = len(blocks)

    books: dict[str, list[list[str]]] = {}
    reports: list[BookReport] = []
    warnings: list[str] = []
    total_books = len(BOOKS)

    if not verses:
        commentary = _CommentaryHarvester()
        for name, markup in documents:
            commentary.feed(markup)
            commentary.close()
        if commentary.notes:
            if commentary.unanchored_paragraphs:
                warnings.append(
                    f"{commentary.unanchored_paragraphs} paragraphs carry no passage heading "
                    "(introductions and prefaces), so they were not filed as notes"
                )
            return {
                "path": path,
                "title": _title_of(path),
                "books": {},
                "flat": {},
                "notes": commentary.notes,
                "study": StudyReport(),
                "reports": [],
                "warnings": warnings,
                "verse_count": 0,
                "mode": "commentary",
            }
        numbered = _NumberedHarvester(_heading_classes(path))
        for name, markup in documents:
            numbered.feed(markup)
            numbered.close()
        if numbered.verses:
            verses = numbered.verses
            if numbered.absent_verses:
                warnings.append(
                    f"{numbered.absent_verses} verse numbers are absent from this translation "
                    "(the next numbered verse was taken at face value)"
                )
            if numbered.declined:
                named = ", ".join(f"{reason}: {count}" for reason, count in sorted(numbered.declined.items()))
                warnings.append(
                    f"{sum(numbered.declined.values())} paragraphs were not kept as verse text "
                    f"while reading numbered paragraphs ({named})"
                )

    for number, entry in enumerate(BOOKS, start=1):
        osis = entry["osis"]
        expected = entry["chapters"]
        collected: dict[int, dict[int, str]] = {}
        problems: list[str] = []
        for (book, chapter, verse), text in verses.items():
            if book != number or not 1 <= chapter <= expected:
                continue
            if not 1 <= verse <= 200:
                problems.append(f"chapter {chapter} verse {verse} is not a plausible verse number")
                continue
            collected.setdefault(chapter, {})[verse] = text

        empty = [c for c in range(1, expected + 1) if not collected.get(c)]
        for chapter in empty:
            problems.append(f"chapter {chapter} has no verse anchors")
        found = sum(len(v) for v in collected.values())
        chapters = [[collected[c][v] for v in sorted(collected[c])] for c in sorted(collected) if collected.get(c)]
        books[osis] = chapters
        reports.append(BookReport(
            osis=osis,
            name=entry["name"],
            chapters=len(chapters),
            expected_chapters=expected,
            verses=found,
            empty_chapters=empty,
            problems=problems,
        ))

    for number in sorted({b for b, _, _ in verses} - set(range(1, total_books + 1))):
        warnings.append(f"verse anchors reference book position {number}, which is not in the 66-book canon")

    return {
        "path": path,
        "title": _title_of(path),
        "books": books,
        "flat": verses,
        "notes": notes,
        "study": study_report,
        "reports": reports,
        "warnings": warnings,
        "verse_count": len(verses),
        "mode": "verse",
    }


def _join_notes(
    links: dict[tuple[int, int, int], list[str]],
    blocks: dict[str, str],
) -> tuple[list[StudyNote], StudyReport]:
    """Attach each commentary block and footnote to the verse that cites it.

    A verse paragraph names its own commentary (``com…``) and footnotes
    (``fn…``), so the join is a lookup rather than a guess. A citation with no
    matching block is counted, never silently dropped.
    """
    report = StudyReport()
    notes: list[StudyNote] = []
    counts: Counter[tuple[int, int, int, str]] = Counter()
    seen: set[tuple[int, int, int, str, str]] = set()

    for key, targets in links.items():
        for ident in targets:
            dedupe = (key[0], key[1], key[2], ident)
            if dedupe in seen:
                continue
            seen.add(dedupe)
            body = blocks.get(ident)
            if not body:
                if ident.startswith("fn"):
                    report.missing_footnotes += 1
                else:
                    report.missing_commentary += 1
                continue
            kind = "footnote" if ident.startswith("fn") else "commentary"
            ordinal = counts[(key[0], key[1], key[2], kind)]
            counts[(key[0], key[1], key[2], kind)] = ordinal + 1
            notes.append(StudyNote(key[0], key[1], key[2], kind, body, ordinal))
            if kind == "footnote":
                report.footnotes += 1
            else:
                report.commentary += 1

    report.orphan_footnotes = sum(1 for ident in blocks if ident.startswith("fn")) - report.footnotes
    return notes, report


def _title_of(path: str) -> str:
    try:
        archive = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile):
        return ""
    with archive:
        return _title(archive)


def report_lines(result: dict) -> list[str]:
    lines = [f"{result['path']}: {result['title'] or 'untitled'} -- {result['verse_count']} verses"]
    for report in result["reports"]:
        flag = "ok  " if report.ok else "FAIL"
        lines.append(
            f"  {flag} {report.name:<22} {report.chapters:>3}/{report.expected_chapters:<3} chapters"
            f" {report.verses:>5} verses"
            + (f"  {'; '.join(report.problems[:3])}" if report.problems else "")
        )
    study = result["study"]
    lines.append(
        f"  study  {study.commentary} commentary notes, {study.footnotes} footnotes"
        f" ({study.blocks_seen} blocks found)"
    )
    if study.missing_commentary or study.missing_footnotes:
        lines.append(
            f"  ! {study.missing_commentary} cited commentary blocks and"
            f" {study.missing_footnotes} cited footnotes had no matching block"
        )
    if study.orphan_footnotes:
        lines.append(f"  ! {study.orphan_footnotes} footnotes are not cited by any verse")
    if study.skipped:
        named = ", ".join(f"{name}={count}" for name, count in list(study.skipped.items())[:8])
        lines.append(f"  ..  paragraph classes not kept as verse text: {named}")
    for warning in result["warnings"]:
        lines.append(f"  ! {warning}")
    return lines


def to_source(result: dict) -> list[dict]:
    """Convert an extraction into the shape ``parse_source`` expects."""
    missing = [r.name for r in result["reports"] if not r.ok]
    if missing:
        first = next(r for r in result["reports"] if not r.ok)
        detail = "; ".join(first.problems[:3]) or f"{first.chapters}/{first.expected_chapters} chapters"
        raise EpubImportError(f"{len(missing)} of {len(result['reports'])} books are incomplete ({first.name}: {detail})")
    return [
        {"name": BOOK_BY_OSIS[osis]["name"], "chapters": chapters}
        for osis, chapters in result["books"].items()
    ]
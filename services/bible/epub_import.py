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

from services.bible.books import BOOKS, BOOK_BY_OSIS, BOOK_ORDER
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

# Paragraph classes that are back matter or apparatus rather than verse text.
# They are counted and reported so nothing disappears without being named.
_BACK_MATTER = frozenset({
    "in1", "in2", "inh", "glo", "toc2", "nl", "nl1", "bibletimes", "idx",
})


_OPF_NS = "{http://www.idpf.org/2007/opf}"
_DC_NS = "{http://purl.org/dc/elements/1.1/}"


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

    def _start(self, match: re.Match[str]) -> None:
        self._flush()
        self._verse = (int(match[1]), int(match[2]), int(match[3]))
        self._buffer = []
        self._targets = []
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
            if not css and self._after_verse:
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
        if tag in _DROP_TAGS:
            self._hidden += 1
        elif tag == "sup":
            self._sup += 1
        href = (data.get("href") or "").strip()
        if "#" in href:
            self._target(href.split("#", 1)[1])
        ident = (data.get("id") or "").strip()
        if ident.endswith("r") and _FOOTNOTE_ID.match(ident[:-1]):
            self._target(ident[:-1])
        match = VERSE_ANCHOR.match(ident)
        if match:
            self._start(match)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag in _DROP_TAGS or tag in {"sup", "p"}:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag == "sup" and self._sup:
            self._sup -= 1
        elif tag in _DROP_TAGS and self._hidden:
            self._hidden -= 1
        elif tag == "p":
            self._in_paragraph = False
            if not self._paragraph_open:
                self._flush()
            self._paragraph_open = False

    def handle_data(self, data: str) -> None:
        if self._verse is None or self._sup or self._hidden or not self._in_paragraph:
            return
        self._buffer.append(data)

    def _flush(self) -> None:
        if self._verse is None:
            return
        text = _WHITESPACE.sub(" ", "".join(self._buffer)).strip()
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
    """Read every verse and every study note out of the epub and report gaps."""
    verses: dict[tuple[int, int, int], str] = {}
    links: dict[tuple[int, int, int], list[str]] = {}
    blocks: dict[str, str] = {}
    skipped: Counter[str] = Counter()
    for name, markup in _documents(path):
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


def to_corpus_source(result: dict) -> tuple[str, list[tuple[str, list[list[str]]]]]:
    """Return the canonical payload ``import_corpus`` parses."""
    return "", [(BOOK_BY_OSIS[osis]["name"], chapters) for osis, chapters in result["books"].items()]
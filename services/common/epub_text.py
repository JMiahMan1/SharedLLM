# services/common/epub_text.py
"""Spine-aware epub text extraction.

An epub is a zip of xhtml documents plus an OPF manifest that names the reading
order. Reading that spine is what makes a *chapter* locator possible: a chunk can
say which spine document it came from, which is stable across re-extraction and
meaningful to a reader, where a fixed-width character window is neither.

This is deliberately prose-only and standard-library-only. The verse-anchored
reader in ``services/bible/epub_import.py`` is a different job with a different
output contract; the subprocess converters in ``services/execution/document_text.py``
are lossier because they throw structure away. Every extraction path in the
monorepo should end up here rather than growing a third opinion.
"""

from __future__ import annotations

import posixpath
import re
import zipfile
from collections import Counter
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import unquote
from xml.etree import ElementTree as ET

_CONTAINER_NS = "{urn:oasis:names:tc:opendocument:xmlns:container}"
_OPF_NS = "{http://www.idpf.org/2007/opf}"
_DC_NS = "{http://purl.org/dc/elements/1.1/}"

_HTML_SUFFIXES = (".html", ".xhtml", ".htm")

#: Tags whose boundaries mean "a new line", not "a new paragraph".
_LINE_BREAK_TAGS = frozenset({"br", "hr"})

#: Block tags that end a paragraph.
_BLOCK_TAGS = frozenset({
    "address", "article", "aside", "blockquote", "dd", "div", "dl", "dt",
    "figcaption", "figure", "footer", "h1", "h2", "h3", "h4", "h5", "h6",
    "header", "li", "main", "p", "pre", "section", "td", "th", "tr",
})

#: Elements whose text is never prose.
_DROP_TAGS = frozenset({"script", "style", "head", "title", "meta", "link", "nav"})

#: Tags that can supply a human label for a spine document, best first.
_LABEL_TAGS = ("h1", "h2", "h3", "title")

_WHITESPACE_RUN = re.compile(r"[ \t\r\f\v]+")
_BLANK_LINES = re.compile(r"\n{3,}")


class EpubTextError(RuntimeError):
    """The bytes could not be read as an epub."""


@dataclass(frozen=True)
class Chapter:
    """One readable section of a book, in reading order.

    ``label`` is the section's own heading, or the deeper heading nested inside
    it, or the spine document's manifest id or file name. ``ordinal`` is 1-based
    over emitted sections and always present, so a caller can order or address a
    chapter without trusting ``label`` to be unique or non-empty.
    """

    ordinal: int
    label: str
    text: str


#: Heading tags that can mark a section boundary. Depth is the ``hN`` number.
_HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})

#: Heading levels deeper than this are treated as part of the preceding label
#: rather than as boundaries of their own. Reading an h6 as a chapter produces a
#: locator nobody would search by.
_MIN_SPLIT_DEPTH = 4

#: Prose shorter than this is treated as a caption or ornament, not a section.
_MIN_SECTION_CHARS = 40

#: Separates a section heading from a deeper heading nested inside it.
_LABEL_JOIN = " — "


class _Section:
    """A labelled run of prose collected from one spine document."""

    __slots__ = ("label", "blocks")

    def __init__(self, label: str) -> None:
        self.label = label
        self.blocks: list[str] = []

    def text(self) -> str:
        return _BLANK_LINES.sub("\n\n", "\n\n".join(self.blocks)).strip()


class _Prose(HTMLParser):
    """Collect readable blocks, recording which of them are headings.

    Headings are kept as blocks rather than being discarded, because a book that
    is a single html file — the usual shape of a Project Gutenberg export — has no
    spine to split on, and its only structure is the headings inside that one
    document.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[tuple[int, str]] = []
        self._dropping = 0
        self._paragraph: list[str] = []
        self._heading: int = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in _DROP_TAGS:
            self._dropping += 1
            return
        if self._dropping:
            return
        if tag in _BLOCK_TAGS or tag in _LINE_BREAK_TAGS:
            self._close_paragraph()
        if tag in _HEADING_TAGS:
            self._heading = int(tag[1])

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if self._dropping:
            return
        if tag in _BLOCK_TAGS or tag in _LINE_BREAK_TAGS:
            self._close_paragraph()

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _DROP_TAGS:
            self._dropping = max(0, self._dropping - 1)
            return
        if self._dropping:
            return
        if tag in _BLOCK_TAGS:
            self._close_paragraph()
        if tag in _HEADING_TAGS:
            self._heading = 0

    def handle_data(self, data: str) -> None:
        if self._dropping or not data.strip():
            return
        self._paragraph.append(data)

    def close(self) -> None:
        super().close()
        self._close_paragraph()

    def _close_paragraph(self) -> None:
        if not self._paragraph:
            return
        joined = _WHITESPACE_RUN.sub(" ", "".join(self._paragraph)).strip()
        self._paragraph = []
        if joined:
            self.blocks.append((self._heading, joined))


def _pick_split_level(blocks: list[tuple[int, str]]) -> int:
    """Choose the heading depth that best divides a document into sections.

    The level with the most headings wins, and a tie goes to the shallower one, so
    a book with one ``h1`` title above thirty-one ``h2`` daily readings splits on
    the readings rather than on the title. A level seen only once is not a
    structure, so it cannot win here.

    When no level repeats but the document nests headings inside one another — an
    ``h1`` title above a single ``h2`` — there is still a title and its
    subdivision, so the deepest level wins. Splitting there keeps the title with
    the prose that precedes the subdivision instead of discarding either.
    """
    counts: Counter[int] = Counter(depth for depth, _ in blocks if depth)
    ranked = [(count, depth) for depth, count in counts.items() if count >= 2]
    if ranked:
        depth = min(ranked, key=lambda pair: (-pair[0], pair[1]))[1]
        return depth if depth <= _MIN_SPLIT_DEPTH else 0
    if len(counts) >= 2:
        nested = max(counts)
        return nested if nested <= _MIN_SPLIT_DEPTH else 0
    return 0


def _split_sections(
    blocks: list[tuple[int, str]], fallback: str
) -> list[_Section]:
    """Group collected blocks into labelled sections at the chosen depth."""
    split_level = _pick_split_level(blocks)
    if not split_level:
        prose = _Section(fallback)
        prose.blocks = [text for depth, text in blocks if not depth]
        if not prose.text():
            return []
        # Nothing divides this document, so a heading still names it better than a
        # manifest id. Only a *repeated* level means the split was rejected for being
        # too deep, and joining those would build a label nobody would search; headings
        # at distinct levels are a title over its subsections, so the first one names
        # the whole document.
        headings = [(depth, text) for depth, text in blocks if depth]
        depths = {depth for depth, _ in headings}
        if headings and len(headings) == len(depths):
            prose.label = headings[0][1].strip() or fallback
        return [prose]

    # A title spanning several blocks — <h1>THE</h1><h1>FAITHFUL PROMISER.</h1>, or a
    # heading whose line breaks became separate paragraphs — is one title, so the
    # shallower headings before the first section are joined rather than taken one
    # at a time.
    title_parts: list[str] = []
    for depth, text in blocks:
        if depth == split_level:
            break
        if depth:
            title_parts.append(text)
    document_title = " ".join(title_parts).strip()

    sections: list[_Section] = []
    opening = _Section(document_title or fallback)
    current = opening
    for depth, text in blocks:
        if depth == split_level:
            if current.text():
                sections.append(current)
            current = _Section(text)
        elif depth > split_level:
            # A deeper heading opens the section, so it belongs in the label
            # rather than in the prose a reader would search for.
            if not current.text():
                current.label = (
                    f"{current.label}{_LABEL_JOIN}{text}" if current.label else text
                )
        elif depth:
            continue
        else:
            current.blocks.append(text)
    if current.text():
        sections.append(current)
    return sections


def _read_document(markup: str, fallback: str) -> list[tuple[str, str]]:
    """Sections of one spine document as ``(label, text)`` pairs."""
    prose = _Prose()
    prose.feed(markup)
    prose.close()
    pairs: list[tuple[str, str]] = []
    for section in _split_sections(prose.blocks, fallback):
        text = section.text()
        label = section.label.strip() or fallback
        if len(text) < _MIN_SECTION_CHARS and not pairs:
            pairs.append((label, text))
        elif text:
            pairs.append((label, text))
    return pairs


def _rootfile(archive: zipfile.ZipFile) -> str:
    """The OPF path named by META-INF/container.xml, else the only OPF found."""
    try:
        root = ET.fromstring(archive.read("META-INF/container.xml"))
    except (KeyError, ET.ParseError):
        pass
    else:
        for node in root.iter(f"{_CONTAINER_NS}rootfile"):
            candidate = node.get("full-path")
            if candidate:
                return candidate
    opfs = [n for n in archive.namelist() if n.lower().endswith(".opf")]
    if not opfs:
        raise EpubTextError("no OPF package document in the archive")
    return sorted(opfs, key=len)[0]


def _spine(archive: zipfile.ZipFile) -> list[tuple[str, str, str]]:
    """Reading order as ``(href, manifest_id, label_hint)``.

    Documents whose manifest properties mark them ``nav`` are excluded: that is
    the table of contents, which duplicates the spine it is describing and would
    otherwise be indexed as if it were prose. If excluding the nav document would
    leave nothing, it is kept, because an empty book is worse than a duplicated
    contents page.
    """
    opf_path = _rootfile(archive)
    try:
        root = ET.fromstring(archive.read(opf_path))
    except (KeyError, ET.ParseError) as exc:
        raise EpubTextError(f"package document {opf_path} is unreadable: {exc}") from exc

    base = posixpath.dirname(opf_path)
    manifest: dict[str, tuple[str, str, bool]] = {}
    for item in root.iter(f"{_OPF_NS}item"):
        ident = item.get("id")
        href = item.get("href")
        if not ident or not href:
            continue
        properties = (item.get("properties") or "").split()
        resolved = posixpath.normpath(posixpath.join(base, href))
        # A manifest item with no declared media-type is judged on its suffix.
        # Trusting the absence of a type would admit cover art and then decode
        # the jpeg bytes as prose.
        media = (item.get("media-type") or "").lower()
        is_html = "html" in media if media else resolved.lower().endswith(_HTML_SUFFIXES)
        if not is_html:
            continue
        manifest[ident] = (resolved, ident, "nav" in properties)

    ordered: list[tuple[str, str, str]] = []
    nav_only: list[tuple[str, str, str]] = []
    for node in root.iter(f"{_OPF_NS}itemref"):
        idref = node.get("idref")
        if not idref or idref not in manifest:
            continue
        href, ident, is_nav = manifest[idref]
        (nav_only if is_nav else ordered).append((href, ident, ""))

    chosen = ordered or nav_only
    if not chosen:
        # A package whose manifest and spine are both unusable is not a reason to
        # return nothing: every html member in the archive is read in name order
        # so a malformed book still reaches the index instead of vanishing.
        chosen = [
            (name, "", "")
            for name in sorted(archive.namelist())
            if name.lower().endswith(_HTML_SUFFIXES)
        ]

    documents: list[tuple[str, str, str]] = []
    for href, ident, _ in chosen:
        name = _match_member(archive, href)
        if name is None:
            raise EpubTextError(f"spine document {href} is missing from the archive")
        documents.append((name, ident, ""))
    return documents


def _member_index(archive: zipfile.ZipFile) -> dict[str, str]:
    """Map every lookup form of each archive member name to its real name.

    An OPF ``href`` is an IRI, so it is normally percent-encoded while the zip
    entry it names is stored literally: Project Gutenberg EPUBs carry
    ``www.gutenberg.org%40files%4027344%40...`` in the package document and
    ``www.gutenberg.org@files@27344@...`` in the archive. Both sides are indexed
    raw and decoded, because either can be the encoded one.
    """
    index: dict[str, str] = {}
    for name in archive.namelist():
        forms = {name.lower()}
        decoded = unquote(name)
        forms.add(decoded.lower())
        for form in forms:
            index.setdefault(form, name)
    return index


def _match_member(archive: zipfile.ZipFile, href: str) -> str | None:
    """Find the archive member an OPF href points at, tolerating encoding, case and prefix."""
    index = _member_index(archive)
    for candidate in (href, unquote(href), href.lstrip("./"), unquote(href).lstrip("./")):
        hit = index.get(candidate.lower())
        if hit is not None:
            return hit
    for candidate in (href, unquote(href)):
        tail = candidate.rsplit("/", 1)[-1].lower()
        for form, name in index.items():
            if unquote(form).rsplit("/", 1)[-1].lower() == tail:
                return name
    return None


def read_chapters(data: bytes) -> list[Chapter]:
    """Read an epub into its spine, in order.

    Raises :class:`EpubTextError` rather than returning empty text, so a caller
    cannot mistake a broken archive for a book with no words in it.
    """
    import io

    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, OSError) as exc:
        raise EpubTextError(f"not a readable epub: {exc}") from exc

    with archive:
        documents = _spine(archive)
        chapters: list[Chapter] = []
        for name, manifest_id, _hint in documents:
            try:
                markup = archive.read(name).decode("utf-8", "replace")
            except KeyError as exc:
                raise EpubTextError(f"spine document {name} is missing from the archive") from exc
            for label, text in _read_document(markup, manifest_id or name):
                if not text:
                    continue
                chapters.append(
                    Chapter(ordinal=len(chapters) + 1, label=label, text=text)
                )
    if not chapters:
        raise EpubTextError("the archive yielded no readable prose")
    return chapters


def read_text(data: bytes, separator: str = "\n\n") -> str:
    """Flatten an epub to plain text, keeping chapter labels as headings."""
    parts: list[str] = []
    for chapter in read_chapters(data):
        parts.append(chapter.label)
        parts.append(chapter.text)
    return separator.join(part for part in parts if part)
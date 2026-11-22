"""One clean structure for every imported study book.

PDF, EPUB, MOBI/AZW3, DOCX and plain text all arrive with their own kind of
mess: hard-wrapped lines and split words, running heads and page numbers on
every page, CSS spans, Word styles, ligatures and mis-decoded quotes. The
format readers (``study_pdf``, ``study_html``, ``study_docx``) only find the
structure; everything they produce is a list of :class:`Block` in reading
order, and this module turns that into a :class:`Document`:

* text is cleaned the same way for every format (ligatures, soft hyphens,
  zero-width characters, mojibake quotes, whitespace),
* blocks get **stable ids** derived from their own words, so a link to a
  paragraph survives a re-import as long as the paragraph does,
* the book is cut into sections at its top heading level, each with a stable
  id derived from its title,
* discussion questions are recognised so the reader can give them answer space,
* scripture references are found and confirmed by the reference parser, so
  every link opens a real passage.

Nothing here invents words. Every transformation either removes layout noise
(and the caller's report says how much) or re-labels text that is already
there. :func:`text_fingerprint` is what the tests and the optional model pass
use to prove it.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field

from services.bible.books import _BOOKS
from services.bible.refs import ReferenceError, parse_one, parse_reference

#: Bump when the structure or the cleaning rules change in a way a stored book
#: should be re-processed for. Books imported before the pipeline are 0.
FORMAT_VERSION = 2

BLOCK_TYPES = (
    "heading",
    "paragraph",
    "list",
    "quote",
    "question",
    "note",
    "table",
    "image",
)


@dataclass
class Block:
    """One unit of the reading surface.

    ``text`` is the block's words (for a list, its items joined by newlines; for
    a table, its cells). ``items`` and ``rows`` carry the structure a list or a
    table needs. ``marker`` is a footnote's own label ("1", "*"), and ``notes``
    the markers a paragraph cites. ``src`` is an image's asset key.
    """

    type: str
    text: str = ""
    level: int = 0
    items: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)
    ordered: bool = False
    marker: str = ""
    notes: list[str] = field(default_factory=list)
    src: str = ""
    id: str = ""
    refs: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        out: dict = {"id": self.id, "type": self.type, "text": self.text}
        if self.level:
            out["level"] = self.level
        if self.items:
            out["items"] = list(self.items)
            out["ordered"] = self.ordered
        if self.rows:
            out["rows"] = [list(row) for row in self.rows]
        if self.marker:
            out["marker"] = self.marker
        if self.notes:
            out["notes"] = list(self.notes)
        if self.src:
            out["src"] = self.src
        if self.refs:
            out["refs"] = list(self.refs)
        return out

    @classmethod
    def from_dict(cls, data: dict) -> Block:
        return cls(
            type=str(data.get("type") or "paragraph"),
            text=str(data.get("text") or ""),
            level=int(data.get("level") or 0),
            items=[str(item) for item in data.get("items") or []],
            rows=[[str(cell) for cell in row] for row in data.get("rows") or []],
            ordered=bool(data.get("ordered")),
            marker=str(data.get("marker") or ""),
            notes=[str(note) for note in data.get("notes") or []],
            src=str(data.get("src") or ""),
            id=str(data.get("id") or ""),
            refs=list(data.get("refs") or []),
        )


@dataclass
class Section:
    title: str
    blocks: list[Block]
    id: str = ""
    #: Source page range for PDFs, so "Page 12" still means something.
    pages: tuple[int, int] | None = None


@dataclass
class Asset:
    key: str
    mime: str
    data: bytes


@dataclass
class Extracted:
    """What a format reader hands over: blocks in order plus what it removed."""

    blocks: list[Block]
    assets: list[Asset] = field(default_factory=list)
    report: dict = field(default_factory=dict)
    #: Set when the reader already knows the section boundaries (EPUB spine
    #: documents); each entry is the index of the block a section starts at.
    breaks: list[int] = field(default_factory=list)
    #: PDF only: the page each block started on, so a book without headings
    #: can still be cut by page.
    pages: list[int] = field(default_factory=list)
    title: str = ""


@dataclass
class Document:
    title: str
    sections: list[Section]
    assets: list[Asset] = field(default_factory=list)
    report: dict = field(default_factory=dict)


# --------------------------------------------------------------------------
# Text cleaning
# --------------------------------------------------------------------------

_LIGATURES = {
    "\ufb00": "ff",
    "\ufb01": "fi",
    "\ufb02": "fl",
    "\ufb03": "ffi",
    "\ufb04": "ffl",
    "\ufb05": "st",
    "\ufb06": "st",
}

#: UTF-8 read as cp1252, the usual source of "God\u00e2\u20ac\u2122s".
_MOJIBAKE = {
    "\u00e2\u20ac\u2122": "\u2019",
    "\u00e2\u20ac\u02dc": "\u2018",
    "\u00e2\u20ac\u0153": "\u201c",
    "\u00e2\u20ac\u009d": "\u201d",
    "\u00e2\u20ac\ufffd": "\u201d",
    "\u00e2\u20ac\u201d": "\u2014",
    "\u00e2\u20ac\u201c": "\u2013",
    "\u00e2\u20ac\u00a6": "\u2026",
    "\u00c2\u00a0": " ",
}

_INVISIBLE = re.compile("[\u00ad\u200b\u200c\u200d\u2060\ufeff]")
_SPACES = re.compile(r"[ \t\u00a0\u2000-\u200a\u202f\u205f\u3000]+")
#: `` and '' as typed on old typewriters and some OCR output.
_TYPEWRITER_OPEN = re.compile(r"``")
_TYPEWRITER_CLOSE = re.compile(r"''")


def clean_text(text: str) -> str:
    """Typography noise out, the words untouched."""
    if not text:
        return ""
    for bad, good in _MOJIBAKE.items():
        if bad in text:
            text = text.replace(bad, good)
    for lig, plain in _LIGATURES.items():
        if lig in text:
            text = text.replace(lig, plain)
    text = _INVISIBLE.sub("", text)
    text = _TYPEWRITER_OPEN.sub("\u201c", text)
    text = _TYPEWRITER_CLOSE.sub("\u201d", text)
    text = unicodedata.normalize("NFC", text)
    text = _SPACES.sub(" ", text)
    return text.strip()


#: A word broken at the end of a line ("redemp-" + "tion"). The continuation
#: has to start lower case, so "Old Testament-" + "New" is left alone.
_HYPHEN_BREAK = re.compile(r"(\w)-$")
#: Prefixes that keep their hyphen when a line happens to end on it.
_KEEP_HYPHEN = (
    "self", "well", "long", "short", "God", "Christ", "non", "co", "ex", "all",
    "half", "great", "anti", "pre", "post", "re", "semi", "full", "high", "low",
)


def join_lines(lines: list[str]) -> tuple[str, int]:
    """Re-flow hard-wrapped lines into one paragraph.

    Returns the text and how many split words were rejoined.
    """
    out = ""
    joined = 0
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if not out:
            out = line
            continue
        if out.endswith("\u00ad"):
            out = out[:-1] + line
            joined += 1
            continue
        match = _HYPHEN_BREAK.search(out)
        if match and line[:1].islower():
            head = re.split(r"[\s\u2014(\[\"\u201c]", out)[-1][:-1]
            if head and any(head.lower().endswith(p.lower()) and len(head) <= len(p) + 1 for p in _KEEP_HYPHEN):
                out = out + line
            else:
                out = out[:-1] + line
                joined += 1
            continue
        out = out + " " + line
    return out, joined


def text_fingerprint(text: str) -> str:
    """Letters and digits only, lower case: what "the text was preserved" means.

    Layout (line breaks, hyphenation, spacing, quote style) does not change
    this; dropping or inventing a word does.
    """
    folded = clean_text(text).lower()
    return "".join(ch for ch in folded if ch.isalnum())


# --------------------------------------------------------------------------
# Ids
# --------------------------------------------------------------------------

_SLUG = re.compile(r"[^a-z0-9]+")


def slugify(text: str, limit: int = 48) -> str:
    folded = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    slug = _SLUG.sub("-", folded).strip("-")
    if len(slug) > limit:
        slug = slug[:limit].rsplit("-", 1)[0] or slug[:limit]
    return slug


def _block_digest(block: Block) -> str:
    basis = text_fingerprint(block.text or " ".join(block.items) or block.src or block.type)
    return hashlib.sha1(f"{block.type}|{basis}".encode()).hexdigest()[:8]


_ID_PREFIX = {
    "heading": "h",
    "paragraph": "p",
    "list": "l",
    "quote": "q",
    "question": "k",
    "note": "n",
    "table": "t",
    "image": "i",
}


def assign_block_ids(blocks: list[Block]) -> None:
    """Ids from the block's own words: stable across a re-import of the same text."""
    seen: dict[str, int] = {}
    for block in blocks:
        base = f"{_ID_PREFIX.get(block.type, 'b')}-{_block_digest(block)}"
        count = seen.get(base, 0) + 1
        seen[base] = count
        block.id = base if count == 1 else f"{base}-{count}"


def assign_section_ids(sections: list[Section]) -> None:
    seen: dict[str, int] = {}
    for index, section in enumerate(sections, start=1):
        base = slugify(section.title) or f"section-{index}"
        base = f"s-{base}" if not base.startswith("page-") else base
        count = seen.get(base, 0) + 1
        seen[base] = count
        section.id = base if count == 1 else f"{base}-{count}"


# --------------------------------------------------------------------------
# Questions
# --------------------------------------------------------------------------

QUESTION_HEADING = re.compile(
    r"\b(questions?|discussion|discuss|reflection|reflect|for reflection|ponder|"
    r"think about it|digging deeper|going deeper|apply it|application|study guide|"
    r"talk it over|consider)\b",
    re.IGNORECASE,
)
_QUESTION_NUMBER = re.compile(r"^\s*(?:\d{1,2}|[a-z])[.)]\s+")


def mark_questions(blocks: list[Block]) -> int:
    """Turn the questions a book asks the reader into ``question`` blocks.

    Under a heading that announces questions, every paragraph or list item
    ending in "?" is one. Elsewhere only a *numbered* line that ends in "?" is,
    so a rhetorical question in the prose stays prose. A list of questions is
    split into one block per question, because each needs its own answer space.
    """
    found = 0
    in_questions = False
    out: list[Block] = []
    for block in blocks:
        if block.type == "heading":
            in_questions = bool(QUESTION_HEADING.search(block.text)) and len(block.text) <= 80
            out.append(block)
            continue
        if block.type == "list":
            items = block.items or [block.text]
            if in_questions or all(item.rstrip().endswith("?") for item in items):
                questions = [item for item in items if item.rstrip().endswith("?")]
                if questions and (in_questions or len(questions) == len(items)):
                    for item in items:
                        if item.rstrip().endswith("?"):
                            out.append(Block(type="question", text=_QUESTION_NUMBER.sub("", item).strip()))
                            found += 1
                        else:
                            out.append(Block(type="paragraph", text=item))
                    continue
            out.append(block)
            continue
        if (
            block.type == "paragraph"
            and block.text.rstrip().endswith("?")
            and len(block.text) <= 600
            and (in_questions or _QUESTION_NUMBER.match(block.text))
        ):
            block.type = "question"
            block.text = _QUESTION_NUMBER.sub("", block.text).strip()
            found += 1
        out.append(block)
    blocks[:] = out
    return found


# --------------------------------------------------------------------------
# Scripture references
# --------------------------------------------------------------------------

#: Book spellings that are ordinary words, or too short to trust on their own.
#: These only count with chapter *and* verse ("Job 1:21", never "a job 4").
_RISKY = {"job", "acts", "mark", "numbers", "judges", "song", "am", "is", "he", "me", "do", "ex", "so"}


def _book_alternatives() -> tuple[str, str]:
    safe: set[str] = set()
    risky: set[str] = set()
    for osis, name, _chapters, aliases in _BOOKS:
        for token in {osis, name, *aliases}:
            token = token.strip()
            if not token or not token[0].isalnum():
                continue
            if len(token.replace(" ", "")) <= 2 or token.lower() in _RISKY:
                risky.add(token)
            else:
                safe.add(token)
    # Roman numerals and spelled ordinals in front of numbered books.
    extra = set()
    for token in list(safe | risky):
        match = re.match(r"^([123]) ?(.+)$", token)
        if match:
            roman = {"1": "I", "2": "II", "3": "III"}[match.group(1)]
            extra.add(f"{roman} {match.group(2)}")
    safe |= {token for token in extra if len(token) > 4}

    def alternation(tokens: set[str]) -> str:
        ordered = sorted(tokens, key=len, reverse=True)
        return "|".join(re.escape(t).replace(r"\ ", r"\s?") for t in ordered)

    return alternation(safe), alternation(risky)


_SAFE_BOOKS, _RISKY_BOOKS = _book_alternatives()
_CV = r"\d{1,3}(?::\d{1,3}(?:[a-c])?(?:\s?[-\u2013\u2014]\s?\d{1,3}(?::\d{1,3})?)?(?:,\s?\d{1,3}(?:[-\u2013]\d{1,3})?)*)?"
_CV_STRICT = r"\d{1,3}:\d{1,3}(?:[a-c])?(?:\s?[-\u2013\u2014]\s?\d{1,3}(?::\d{1,3})?)?(?:,\s?\d{1,3}(?:[-\u2013]\d{1,3})?)*"
#: "; 4:1" or "; 5" after a reference continues the same book.
_CONTINUE = r"(?:;\s?(?:\d{1,3}:\d{1,3}(?:[-\u2013]\d{1,3})?))*"
_REF_PATTERN = re.compile(
    rf"(?<![\w-])(?:(?:{_SAFE_BOOKS})\.?\s?{_CV}{_CONTINUE}|(?:{_RISKY_BOOKS})\.?\s?{_CV_STRICT}{_CONTINUE})(?![\w:])"
)


_ROMAN = {"I": "1", "II": "2", "III": "3"}


def find_references(text: str) -> list[dict]:
    """Scripture references in prose, each confirmed by the parser.

    Returns ``[{start, end, ref}]`` where ``ref`` is the canonical display
    ("Romans 8:28-30") the Bible reader opens. Candidates the parser rejects
    (a chapter Genesis does not have, a stray capitalised word) are dropped.
    """
    found: list[dict] = []
    for match in _REF_PATTERN.finditer(text or ""):
        raw = match.group(0).rstrip(".,;")
        candidate = raw.replace("\u2013", "-").replace("\u2014", "-")
        candidate = re.sub(r"(\d)[a-c]\b", r"\1", candidate)
        candidate = re.sub(r"^(III|II|I)\s", lambda m: _ROMAN[m.group(1)] + " ", candidate)
        try:
            spans = parse_reference(candidate)
        except (ReferenceError, KeyError, ValueError):
            try:
                spans = [parse_one(candidate.split(";")[0])]
            except (ReferenceError, KeyError, ValueError):
                continue
        if not spans:
            continue
        display = "; ".join(span.display() for span in spans)
        found.append({"start": match.start(), "end": match.start() + len(raw), "ref": display})
    return found


def link_references(blocks: list[Block]) -> int:
    count = 0
    for block in blocks:
        if block.type in {"paragraph", "quote", "question", "note", "heading"}:
            block.refs = find_references(block.text)
            count += len(block.refs)
        elif block.type == "list":
            # A list's refs index into ``text``, its items joined by newlines.
            block.refs = find_references(block.text)
            count += len(block.refs)
    return count


# --------------------------------------------------------------------------
# Sections
# --------------------------------------------------------------------------

#: A line that opens a unit of a study whatever its typography says.
UNIT_HEADING = re.compile(
    r"^(?:lesson|chapter|session|week|day|part|study|unit)\s+(?:\d{1,3}|[ivxlc]{1,6}|one|two|three|four|five|six|"
    r"seven|eight|nine|ten|eleven|twelve)\b",
    re.IGNORECASE,
)


def _split_level(blocks: list[Block]) -> int:
    """The heading level that divides the book: the shallowest one used twice."""
    levels = sorted({b.level for b in blocks if b.type == "heading" and b.level})
    for level in levels:
        uses = sum(
            1 for b in blocks
            if b.type == "heading" and b.level == level and not QUESTION_HEADING.search(b.text)
        )
        if uses >= 2:
            return level
    return 0


def build_sections(extracted: Extracted, *, max_sections: int = 2000) -> list[Section]:
    blocks = extracted.blocks
    if not blocks:
        return []
    starts: set[int] = set(extracted.breaks)
    level = _split_level(blocks)
    for index, block in enumerate(blocks):
        if block.type != "heading":
            continue
        if QUESTION_HEADING.search(block.text) and len(block.text) <= 80:
            # "Questions" belongs to the lesson above it, never a lesson of its own.
            continue
        if (level and block.level and block.level <= level) or UNIT_HEADING.match(block.text):
            starts.add(index)

    if not starts - {0} and extracted.pages and not any(b.type == "heading" for b in blocks):
        # A PDF with no headings at all: its pages are the only units it has.
        return _sections_by_page(extracted)

    sections: list[Section] = []
    current: list[Block] = []
    for index, block in enumerate(blocks):
        if index in starts and current:
            sections.append(_section_from(current, len(sections) + 1))
            current = []
        current.append(block)
    if current:
        sections.append(_section_from(current, len(sections) + 1))

    # A cover or a title page with a handful of words is not a section to land on.
    merged: list[Section] = []
    for section in sections:
        chars = sum(len(b.text) for b in section.blocks)
        headed = section.blocks[0].type == "heading" and len(section.blocks) > 1
        if merged and chars < 40 and not headed and not any(b.type == "image" for b in section.blocks):
            merged[-1].blocks.extend(section.blocks)
            continue
        merged.append(section)
    if len(merged) >= 2:
        first = merged[0]
        if sum(len(b.text) for b in first.blocks) < 40 and not any(b.type == "image" for b in first.blocks):
            merged[1].blocks[:0] = first.blocks
            merged.pop(0)
    return merged[:max_sections]


def _section_from(blocks: list[Block], ordinal: int) -> Section:
    heading = next((b.text for b in blocks if b.type == "heading" and b.text), "")
    if not heading:
        first = next((b.text for b in blocks if b.text), "")
        heading = first if len(first) <= 80 else ""
    return Section(title=heading or f"Section {ordinal}", blocks=blocks)


def _sections_by_page(extracted: Extracted) -> list[Section]:
    by_page: dict[int, list[Block]] = {}
    for block, page in zip(extracted.blocks, extracted.pages, strict=False):
        by_page.setdefault(page, []).append(block)
    sections = []
    for page in sorted(by_page):
        sections.append(Section(title=f"Page {page}", blocks=by_page[page], pages=(page, page)))
    return sections


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------


def block_plain(block: Block) -> str:
    if block.type == "list":
        bullet = [f"{n}. {item}" if block.ordered else f"- {item}" for n, item in enumerate(block.items, start=1)]
        return "\n".join(bullet)
    if block.type == "table":
        return "\n".join(" | ".join(row) for row in block.rows)
    if block.type == "image":
        return f"[Image: {block.text}]" if block.text else ""
    if block.type == "note":
        return f"[{block.marker}] {block.text}" if block.marker else block.text
    return block.text


def section_text(section: Section) -> str:
    """The plain text every older consumer reads (Ask, devotionals, Notes)."""
    return "\n\n".join(text for text in (block_plain(b) for b in section.blocks) if text)


def section_questions(section: Section) -> list[str]:
    return [b.text for b in section.blocks if b.type == "question"][:40]


def finish(extracted: Extracted, *, title: str = "") -> Document:
    """Clean, structure and label what a format reader found."""
    report = dict(extracted.report)
    cleaned: list[Block] = []
    for block in extracted.blocks:
        block.text = clean_text(block.text)
        block.items = [clean_text(item) for item in block.items if clean_text(item)]
        block.rows = [[clean_text(cell) for cell in row] for row in block.rows if any(c.strip() for c in row)]
        if block.type == "list":
            if not block.items:
                continue
            block.text = "\n".join(block.items)
        if block.type == "table" and block.rows:
            block.text = "\n".join(" | ".join(row) for row in block.rows)
        if not block.text and block.type not in {"image", "table"}:
            continue
        if block.type == "image" and not block.src and not block.text:
            continue
        cleaned.append(block)
    # Breaks and pages index the uncleaned list; rebuild them against ``cleaned``.
    if len(cleaned) != len(extracted.blocks):
        keep = {id(b) for b in cleaned}
        new_index: dict[int, int] = {}
        pages: list[int] = []
        position = 0
        for index, block in enumerate(extracted.blocks):
            if id(block) in keep:
                new_index[index] = position
                if extracted.pages:
                    pages.append(extracted.pages[index])
                position += 1
        breaks = []
        for brk in extracted.breaks:
            target = next((new_index[i] for i in range(brk, len(extracted.blocks)) if i in new_index), None)
            if target is not None:
                breaks.append(target)
        extracted = Extracted(
            blocks=cleaned, assets=extracted.assets, report=report, breaks=breaks,
            pages=pages, title=extracted.title,
        )
    else:
        extracted.blocks = cleaned

    sections = build_sections(extracted)
    questions = 0
    links = 0
    for section in sections:
        questions += mark_questions(section.blocks)
        links += link_references(section.blocks)
        assign_block_ids(section.blocks)
    assign_section_ids(sections)
    report["questions"] = questions
    report["scripture_links"] = links
    report["sections"] = len(sections)
    report["blocks"] = sum(len(s.blocks) for s in sections)
    kinds: dict[str, int] = {}
    for section in sections:
        for block in section.blocks:
            kinds[block.type] = kinds.get(block.type, 0) + 1
    report["block_types"] = kinds
    report["format_version"] = FORMAT_VERSION
    return Document(
        title=title or extracted.title,
        sections=sections,
        assets=extracted.assets,
        report=report,
    )


# --------------------------------------------------------------------------
# Plain text and Markdown
# --------------------------------------------------------------------------

_MD_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*$")
_BULLET = re.compile(r"^\s*(?:[-*\u2022\u25aa\u25cf\u2013]|\d{1,3}[.)])\s+")


def blocks_from_text(raw: str) -> Extracted:
    """A .txt or .md file: blank lines divide paragraphs, ``#`` marks headings."""
    blocks: list[Block] = []
    joined_total = 0
    for chunk in re.split(r"\n\s*\n", raw.replace("\r\n", "\n").replace("\r", "\n")):
        lines = [line for line in chunk.split("\n") if line.strip()]
        if not lines:
            continue
        heading = _MD_HEADING.match(lines[0].strip())
        if heading and len(lines) == 1:
            blocks.append(Block(type="heading", text=heading.group(2), level=len(heading.group(1))))
            continue
        if all(_BULLET.match(line) for line in lines):
            ordered = bool(re.match(r"^\s*\d", lines[0]))
            blocks.append(Block(type="list", items=[_BULLET.sub("", line) for line in lines], ordered=ordered))
            continue
        if all(line.lstrip().startswith(">") for line in lines):
            text, joined = join_lines([line.lstrip()[1:] for line in lines])
            joined_total += joined
            blocks.append(Block(type="quote", text=text))
            continue
        text, joined = join_lines(lines)
        joined_total += joined
        blocks.append(Block(type="paragraph", text=text))
    return Extracted(blocks=blocks, report={"format": "text", "hyphens_joined": joined_total})

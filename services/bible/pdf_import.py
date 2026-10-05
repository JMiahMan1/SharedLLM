# services/bible/pdf_import.py
"""Turn a Bible PDF into the corpus source JSON shape.

A PDF is a poor source for verse-level text and this module is deliberately
paranoid about it. Every verse number it accepts must equal the number it
expects next, which is what makes running heads, page numbers, footnote
markers and cross-references harmless: none of them happen to be the next
verse. Whatever comes out is then checked against ``books.py`` -- chapter
counts per book are canonical, so a book with 51 chapters in Genesis is
rejected rather than imported.

Nothing here guesses. ``extract()`` returns what it found plus the reasons it
is suspicious; it is the caller's job to decide whether to trust it. The CLI
prints that report before anything is written to the database.
"""
from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field
from pathlib import Path

from services.bible.books import BOOKS, BOOK_BY_OSIS, resolve_book
from services.bible.corpus import CorpusError

_CHAPTER_LINE = re.compile(r"^(?:chapter\s+)?(\d{1,3})\b[\s.:—-]*(.*)$", re.IGNORECASE)
_VERSE_LINE = re.compile(r"^(\d{1,3})\s+(.+)$", re.DOTALL)
_PAGE_NUMBER = re.compile(r"^\s*(?:page\s+)?[-–—•\s]*\d{1,4}\s*[-–—•]?\s*$", re.IGNORECASE)
_CHAPTER_WORD = re.compile(r"^chapter\s+(\d{1,3})\s*$", re.IGNORECASE)
_BOOK_SUFFIX = re.compile(r"^(.*?)\s+(\d{1,3})$")


class PdfImportError(CorpusError):
    """The PDF could not be read as Scripture."""


@dataclass
class PageLine:
    text: str
    size: float
    x0: float
    y0: float
    x1: float
    y1: float
    page: int


@dataclass
class BookReport:
    osis: str
    name: str
    chapters: int = 0
    verses: int = 0
    expected_chapters: int = 0
    empty_chapters: list[int] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return (
            self.expected_chapters > 0
            and self.chapters == self.expected_chapters
            and not self.empty_chapters
        )

    def as_dict(self) -> dict:
        return {
            "osis": self.osis,
            "name": self.name,
            "chapters": self.chapters,
            "expected_chapters": self.expected_chapters,
            "verses": self.verses,
            "ok": self.ok,
            "empty_chapters": self.empty_chapters,
            "problems": self.problems,
        }


def _normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def open_document(path: Path):
    try:
        import pymupdf
    except ImportError:  # pragma: no cover - the dependency is declared
        try:
            import fitz as pymupdf  # type: ignore[no-redef]
        except ImportError as exc:
            raise PdfImportError(
                "PDF import needs PyMuPDF. Install it with: "
                "python3 -m pip install pymupdf"
            ) from exc
    if not path.is_file():
        raise PdfImportError(f"PDF not found: {path}")
    try:
        return pymupdf.open(path)
    except Exception as exc:
        raise PdfImportError(f"{path.name} could not be opened as a PDF: {exc}") from exc


def extract_lines(path: Path) -> tuple[list[PageLine], list[str]]:
    """Every text line in the document, with the geometry needed to sort it."""
    warnings: list[str] = []
    document = open_document(path)
    try:
        if getattr(document, "needs_pass", False):
            raise PdfImportError(f"{path.name} is password protected")
        lines: list[PageLine] = []
        for page_number, page in enumerate(document, start=1):
            data = page.get_text("dict")
            page_rect = page.rect
            for block in data.get("blocks", []):
                for line in block.get("lines", []):
                    spans = [s for s in line.get("spans", []) if s.get("text", "").strip()]
                    if not spans:
                        continue
                    text = "".join(s["text"] for s in spans)
                    if not text.strip():
                        continue
                    weights = [len(s["text"].strip()) for s in spans]
                    size = sum(s["size"] * w for s, w in zip(spans, weights)) / max(sum(weights), 1)
                    bbox = line["bbox"]
                    lines.append(
                        PageLine(
                            text=text,
                            size=round(size, 2),
                            x0=round(bbox[0], 2),
                            y0=round(bbox[1], 2),
                            x1=round(bbox[2], 2),
                            y1=round(bbox[3], 2),
                            page=page_number,
                        )
                    )
            if not lines:
                warnings.append(f"page {page_number} has no extractable text (is it a scan?)")
        return lines, warnings
    finally:
        document.close()


def body_size(lines: list[PageLine]) -> float:
    """The dominant font size, weighted by characters -- that is the body text."""
    weights: dict[float, int] = {}
    for line in lines:
        bucket = round(line.size * 2) / 2
        weights[bucket] = weights.get(bucket, 0) + len(line.text)
    if not weights:
        raise PdfImportError("the PDF contains no text; scanned pages need OCR first")
    return max(weights.items(), key=lambda item: item[1])[0]


def column_count(lines: list[PageLine], cutoff: float) -> int:
    """1 or 2, judged by whether body text sits on one side of the page.

    A single-column page is full-width, so *every* line straddles the midline --
    which is the opposite of the two-column signal, where almost no line does.
    Counting straddle alone would call every Bible two-column.
    """
    body = [line for line in lines if line.size >= cutoff * 0.9 and line.x1 > line.x0]
    if len(body) < 20:
        return 1
    mid = statistics.median((line.x0 + line.x1) / 2 for line in body)
    on_one_side = sum(1 for line in body if line.x1 <= mid or line.x0 >= mid)
    return 2 if on_one_side / len(body) > 0.8 else 1


def reading_order(lines: list[PageLine], cutoff: float, columns: int) -> list[str]:
    """Body text in the order a reader would read it, with furniture removed."""
    kept = [
        line
        for line in lines
        if line.size >= cutoff * 0.9 and not _PAGE_NUMBER.match(line.text)
    ]
    if columns == 1:
        ordered = sorted(kept, key=lambda l: (l.page, l.y0, l.x0))
        return [_tidy(line.text) for line in ordered if _tidy(line.text)]

    half = statistics.median((line.x0 + line.x1) / 2 for line in kept) if kept else 0
    ordered = sorted(kept, key=lambda l: (l.page, 0 if l.x1 <= half + 40 else 1, l.y0, l.x0))
    return [_tidy(line.text) for line in ordered if _tidy(line.text)]


def _tidy(text: str) -> str:
    text = text.replace("­", "")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:-1].rstrip() if text.endswith("-") else text


def _match_book_heading(line: str) -> tuple[str, int | None] | None:
    """A line that is (only) a book name, optionally followed by a chapter."""
    stripped = line.strip().strip("·•—–-— ")
    if not stripped:
        return None
    suffix = _BOOK_SUFFIX.match(stripped)
    chapter = None
    if suffix:
        chapter = int(suffix.group(2))
        stripped = suffix.group(1).strip()
    osis = resolve_book(stripped)
    if osis:
        return osis, chapter
    osis = resolve_book(_normalise(stripped))
    return (osis, chapter) if osis else None


def _as_verse(line: str, expected: int) -> str | None:
    """A verse, but only when its number is the one we expect next.

    That single rule is what makes running heads, page numbers, footnote
    markers and cross-references harmless: none of them is the verse we are
    waiting for, so they fall through and get appended to the verse in progress.
    """
    match = _VERSE_LINE.match(line.strip())
    if not match:
        return None
    number, text = int(match.group(1)), match.group(2).strip()
    if number != expected or not text:
        return None
    return text


def parse(lines: list[str], *, strict: bool = False) -> dict:
    """Walk the text stream and split it into books, chapters and verses."""
    books: dict[str, list[list[str]]] = {}
    reports: dict[str, BookReport] = {}
    state = {"osis": None, "chapter": None, "pending": 1, "verse": 1}

    def materialise() -> list[str]:
        """Create the chapter only once we know it holds a verse."""
        if state["chapter"] is None:
            chapter: list[str] = []
            books[state["osis"]].append(chapter)
            state["chapter"] = chapter
            state["verse"] = 1
        return state["chapter"]

    def target_chapter() -> int:
        return len(books[state["osis"]]) + 1

    for raw in lines:
        line = raw.strip()
        if not line:
            continue

        heading = _match_book_heading(line)
        if heading:
            osis, chapter_hint = heading
            if chapter_hint is None and state["osis"] == osis:
                continue
            state["osis"] = osis
            books.setdefault(osis, [])
            reports[osis] = BookReport(
                osis=osis,
                name=BOOK_BY_OSIS[osis]["name"],
                expected_chapters=BOOK_BY_OSIS[osis]["chapters"],
            )
            state["chapter"] = None
            state["verse"] = 1
            state["pending"] = chapter_hint or target_chapter()
            inline = _VERSE_LINE.match(line)
            if chapter_hint is None and inline and int(inline.group(1)) == 1:
                materialise().append(inline.group(2).strip())
                state["verse"] = 2
            continue

        osis = state["osis"]
        if osis is None:
            continue

        marker = _CHAPTER_WORD.match(line)
        if marker:
            requested = int(marker.group(1))
            expected = reports[osis].expected_chapters
            if requested <= len(books[osis]) or (expected and requested > expected):
                reports[osis].problems.append(
                    f"line {line!r} claims chapter {requested} but we are "
                    f"already in chapter {len(books[osis])}"
                )
                continue
            state["pending"] = requested
            state["chapter"] = None
            state["verse"] = 1
            continue

        verse = _as_verse(line, state["verse"])
        if verse is not None:
            materialise().append(verse)
            state["verse"] += 1
            continue

        numbered = _VERSE_LINE.match(line)
        if numbered and state["chapter"] is None:
            reports[osis].problems.append(
                f"chapter {state['pending']} opens at verse {numbered.group(1)} "
                f"instead of verse 1; the text was kept but the chapter boundary "
                f"is unreliable"
            )
            materialise().append(numbered.group(2).strip())
            state["verse"] = int(numbered.group(1)) + 1
            continue

        chapter = state["chapter"]
        if chapter and chapter[-1]:
            chapter[-1] = f"{chapter[-1]} {line}"

    for osis, chapters in books.items():
        report = reports.setdefault(
            osis,
            BookReport(
                osis=osis,
                name=BOOK_BY_OSIS.get(osis, {}).get("name", osis),
                expected_chapters=BOOK_BY_OSIS.get(osis, {}).get("chapters", 0),
            ),
        )
        report.chapters = len(chapters)
        report.verses = sum(len(c) for c in chapters)
        report.empty_chapters = [i + 1 for i, c in enumerate(chapters) if not c]
        if report.chapters != report.expected_chapters:
            report.problems.append(
                f"found {report.chapters} chapters, the Bible has "
                f"{report.expected_chapters}"
            )
        if strict and not report.ok:
            raise PdfImportError(
                f"{report.name}: {report.chapters} chapters extracted but "
                f"{report.expected_chapters} expected; verse counts per chapter "
                f"are the usual casualty of a PDF layout we cannot read"
            )
    return {"books": books, "reports": reports}


def extract(path: Path, *, strict: bool = False) -> dict:
    """Read ``path`` and report what was found, with reasons for every doubt."""
    lines, warnings = extract_lines(path)
    if not lines:
        raise PdfImportError(f"{path.name} has no extractable text (scanned PDFs need OCR)")
    cutoff = body_size(lines)
    columns = column_count(lines, cutoff)
    ordered = reading_order(lines, cutoff, columns)
    if not ordered:
        raise PdfImportError(
            f"{path.name} produced no body text at size {cutoff}pt; every line "
            "looked like a page header or footer"
        )
    parsed = parse(ordered, strict=strict)
    if not parsed["books"]:
        raise PdfImportError(
            f"{path.name} has no recognisable book headings, so it does not look "
            "like a Bible. Expected a line such as 'Genesis' or 'John 1'."
        )
    reports = [report.as_dict() for report in parsed["reports"].values()]
    good = [r for r in reports if r["ok"]]
    warnings.append(
        f"column layout detected: {columns}; body text {cutoff}pt; "
        f"{len(good)}/{len(reports)} books have the expected chapter count"
    )
    return {
        "path": str(path),
        "columns": columns,
        "body_size": cutoff,
        "books": parsed["books"],
        "reports": reports,
        "warnings": warnings,
        "complete": len(good) == len(reports) and len(good) == len(BOOKS),
    }


def to_source(result: dict) -> list[dict]:
    """The corpus source shape: ``[{"abbrev", "name", "chapters": [[verse]]}]``."""
    payload = []
    for book in BOOKS:
        chapters = result["books"].get(book["osis"])
        if not chapters:
            raise PdfImportError(
                f"{book['name']} is missing from this PDF; a partial Bible is not "
                "importable, because the reader would silently have no chapters to show"
            )
        payload.append(
            {
                "abbrev": book["osis"],
                "name": book["name"],
                "chapters": [[verse for verse in chapter] for chapter in chapters],
            }
        )
    return payload
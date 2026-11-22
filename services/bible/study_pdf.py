"""Layout-aware reading of a study book PDF.

A PDF has no paragraphs, only lines placed on a page. ``page.get_text("text")``
returns those lines in drawing order, which is how imported studies used to
arrive: every line hard-wrapped, words split at a hyphen, the book title and a
page number repeated every few hundred words, two columns interleaved line by
line, and footnotes in the middle of a sentence.

This reader uses PyMuPDF's span data (position, size, weight) instead:

* **Running heads and page numbers.** A line in the top or bottom margin whose
  text (digits folded) repeats on several pages, or that is only a page number,
  is layout, not prose. Counted per page and within a window, so a chapter
  title used as a running head over ten pages is caught as well as the book
  title used over two hundred.
* **Columns.** A page whose lines sit in two separate halves is read left
  column first, then right.
* **Headings** are lines noticeably larger than the body text, or short bold
  lines standing alone; their sizes rank them into levels.
* **Paragraphs** end at a vertical gap, a first-line indent or a short line
  that ends a sentence; split words are rejoined. A paragraph that runs over a
  page break continues instead of being cut in two.
* **Footnotes** (small type at the foot of a page, starting with a marker) become
  note blocks; superscript markers in the prose are lifted out of the sentence.
* **Tables** found by PyMuPDF become table blocks; **images** of a useful size
  are kept as assets.
"""
from __future__ import annotations

import itertools
import re
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from services.bible.study_blocks import (
    UNIT_HEADING,
    Asset,
    Block,
    Extracted,
    join_lines,
)

MARGIN = 0.085
#: Footnotes live in the lower part of a page.
FOOT_ZONE = 0.62
MAX_IMAGES = 60
MAX_IMAGE_BYTES = 2 * 1024 * 1024
MIN_IMAGE_PX = 96

_PAGE_NUMBER = re.compile(
    r"^(?:page\s+)?(?:\d{1,4}|[ivxlcdm]{1,7})(?:\s+of\s+\d{1,4})?$|^[-\u2013\u2014]\s?\d{1,4}\s?[-\u2013\u2014]$",
    re.IGNORECASE,
)
_NOTE_START = re.compile(r"^(\d{1,3}|[*\u2020\u2021\u00a7])[.)]?\s+\S")
_BULLET = re.compile(r"^(?:[\u2022\u25cf\u25aa\u25e6\u2023\u2043\u2219\u00b7*\u2013-]|\(?\d{1,2}[.)]|\(?[a-z][.)])\s+")
_SENTENCE_END = re.compile(r"[.!?:;\"\u201d\u2019)\]]$")


class PdfReadError(ValueError):
    pass


@dataclass
class Line:
    page: int
    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    size: float
    bold: bool
    italic: bool
    notes: list[str] = field(default_factory=list)
    role: str = "body"  # body | running | note | table
    column: int = 0


def _open(path: Path):
    try:
        import pymupdf
    except ImportError:
        try:
            import fitz as pymupdf  # type: ignore[no-redef]
        except ImportError as exc:
            raise PdfReadError(
                "PDF import needs PyMuPDF in the bible service. Install it with `pip install pymupdf`."
            ) from exc
    try:
        return pymupdf, pymupdf.open(path)
    except Exception as exc:
        raise PdfReadError(f"{path.name} could not be opened as a PDF: {exc}") from exc


def _lines_of(page, number: int) -> list[Line]:
    out: list[Line] = []
    data = page.get_text("dict")
    for block in data.get("blocks", []):
        if block.get("type") != 0:
            continue
        for raw in block.get("lines", []):
            spans = [s for s in raw.get("spans", []) if s.get("text")]
            if not spans:
                continue
            body_sizes = [s["size"] for s in spans if not (s.get("flags", 0) & 1)]
            reference = max(body_sizes) if body_sizes else max(s["size"] for s in spans)
            text = ""
            notes: list[str] = []
            bold_chars = italic_chars = total = 0
            for span in spans:
                content = span["text"]
                flags = span.get("flags", 0)
                font = str(span.get("font", ""))
                superscript = bool(flags & 1) or (
                    span["size"] < reference * 0.75 and content.strip().isalnum() and len(content.strip()) <= 3
                )
                if superscript and content.strip() and len(content.strip()) <= 3 and text:
                    notes.append(content.strip())
                    continue
                text += content
                weight = len(content.strip())
                total += weight
                if flags & 16 or "bold" in font.lower() or "black" in font.lower() or font.lower().endswith("bo"):
                    bold_chars += weight
                if flags & 2 or "italic" in font.lower() or "oblique" in font.lower():
                    italic_chars += weight
            text = text.strip()
            if not text:
                continue
            x0, y0, x1, y1 = raw["bbox"]
            out.append(
                Line(
                    page=number,
                    x0=x0,
                    y0=y0,
                    x1=x1,
                    y1=y1,
                    text=text,
                    size=round(reference, 1),
                    bold=total > 0 and bold_chars / total > 0.6,
                    italic=total > 0 and italic_chars / total > 0.6,
                    notes=notes,
                )
            )
    # PyMuPDF can report one visual line as several when spans change font;
    # merge lines on the same baseline that touch.
    out.sort(key=lambda ln: (round(ln.y0), ln.x0))
    merged: list[Line] = []
    for line in out:
        prev = merged[-1] if merged else None
        if prev and abs(prev.y0 - line.y0) < 2 and 0 <= line.x0 - prev.x1 < line.size * 1.5:
            gap = " " if line.x0 - prev.x1 > line.size * 0.15 else ""
            prev.text = prev.text + gap + line.text
            prev.x1 = line.x1
            prev.notes.extend(line.notes)
            continue
        merged.append(line)
    return merged


def _fold(text: str) -> str:
    return re.sub(r"\d+", "#", text.lower()).strip()


def _mark_running(pages: dict[int, list[Line]], heights: dict[int, float]) -> int:
    """Running heads, running feet and page numbers: layout, not prose."""
    marginal: dict[str, set[int]] = defaultdict(set)
    for number, lines in pages.items():
        height = heights[number]
        for line in lines:
            if line.y1 <= height * MARGIN * 1.3 or line.y0 >= height * (1 - MARGIN * 1.3):
                marginal[_fold(line.text)].add(number)
    total = len(pages)
    threshold = max(3, int(total * 0.3)) if total >= 6 else max(2, total)
    removed = 0
    for number, lines in pages.items():
        height = heights[number]
        for line in lines:
            in_margin = line.y1 <= height * MARGIN * 1.3 or line.y0 >= height * (1 - MARGIN * 1.3)
            if not in_margin:
                continue
            key = _fold(line.text)
            seen = marginal.get(key, set())
            window = sum(1 for p in seen if abs(p - number) <= 6)
            if (
                _PAGE_NUMBER.match(line.text.strip())
                or len(seen) >= threshold
                or (window >= 3 and len(line.text) <= 90)
            ):
                line.role = "running"
                removed += 1
    return removed


def _body_size(lines: list[Line]) -> float:
    weights: Counter[float] = Counter()
    for line in lines:
        if line.role == "body":
            weights[round(line.size)] += len(line.text)
    if not weights:
        return 10.0
    return float(weights.most_common(1)[0][0])


def _mark_notes(pages: dict[int, list[Line]], heights: dict[int, float], body: float) -> None:
    for number, lines in pages.items():
        height = heights[number]
        in_note = False
        for line in sorted(lines, key=lambda ln: ln.y0):
            if line.role != "body":
                continue
            small = line.size <= body * 0.88
            if small and line.y0 >= height * FOOT_ZONE and _NOTE_START.match(line.text):
                line.role = "note"
                in_note = True
            elif in_note and small and line.y0 >= height * FOOT_ZONE:
                line.role = "note"
            else:
                in_note = False


def _order(lines: list[Line], width: float) -> list[Line]:
    """Reading order: one column top to bottom, or left column then right."""
    body = [ln for ln in lines if ln.role in {"body", "note"}]
    if not body:
        return []
    left = [ln for ln in body if ln.x1 <= width * 0.53]
    right = [ln for ln in body if ln.x0 >= width * 0.47]
    spanning = [ln for ln in body if ln not in left and ln not in right]
    two_columns = len(left) >= 4 and len(right) >= 4 and len(spanning) <= max(2, len(body) * 0.2)
    if not two_columns:
        return sorted(body, key=lambda ln: (ln.role == "note", round(ln.y0, 0), ln.x0))
    column_top = min(ln.y0 for ln in left + right)
    for ln in left:
        ln.column = 1
    for ln in right:
        ln.column = 2
    above = sorted([ln for ln in spanning if ln.y0 < column_top], key=lambda ln: ln.y0)
    below = sorted([ln for ln in spanning if ln.y0 >= column_top], key=lambda ln: ln.y0)
    ordered = above + sorted(left, key=lambda ln: ln.y0) + sorted(right, key=lambda ln: ln.y0) + below
    notes = [ln for ln in ordered if ln.role == "note"]
    return [ln for ln in ordered if ln.role != "note"] + notes


def _heading_levels(lines: list[Line], body: float) -> dict[float, int]:
    sizes = sorted({round(ln.size) for ln in lines if ln.role == "body" and ln.size >= body * 1.18}, reverse=True)
    return {size: index + 1 for index, size in enumerate(sizes[:4])}


def _is_heading(line: Line, body: float, levels: dict[float, int]) -> int:
    words = len(line.text.split())
    if round(line.size) in levels and words <= 20:
        return levels[round(line.size)]
    if UNIT_HEADING.match(line.text) and line.size >= body * 0.98 and words <= 14:
        return 1 if not levels else min(levels.values())
    if (
        line.bold
        and line.size >= body * 0.97
        and words <= 12
        and not _SENTENCE_END.search(line.text.rstrip("\u201d\""))
        and not _BULLET.match(line.text)
    ):
        return len(levels) + 1
    return 0


@dataclass
class _Para:
    kind: str
    lines: list[Line]
    level: int = 0


def _tables_and_images(document, pymupdf, page, number: int, assets: list[Asset], report: Counter) -> tuple[list[tuple[float, Block]], list[tuple[float, float, float, float]]]:
    placed: list[tuple[float, Block]] = []
    boxes: list[tuple[float, float, float, float]] = []
    try:
        finder = page.find_tables()
        tables = list(getattr(finder, "tables", []) or [])
    except Exception:
        tables = []
    for table in tables:
        try:
            rows = [[(cell or "").replace("\n", " ").strip() for cell in row] for row in table.extract()]
        except Exception:
            continue
        rows = [row for row in rows if any(row)]
        if len(rows) < 2 or max(len(r) for r in rows) < 2:
            continue
        x0, y0, x1, y1 = table.bbox
        boxes.append((x0, y0, x1, y1))
        placed.append((y0, Block(type="table", rows=rows)))
        report["tables"] += 1
    if len(assets) >= MAX_IMAGES:
        return placed, boxes
    try:
        images = page.get_images(full=True)
    except Exception:
        images = []
    for info in images:
        xref = info[0]
        width, height = info[2], info[3]
        if width < MIN_IMAGE_PX or height < MIN_IMAGE_PX or len(assets) >= MAX_IMAGES:
            continue
        try:
            rects = page.get_image_rects(xref)
            extracted = document.extract_image(xref)
        except Exception:
            continue
        data = extracted.get("image") if extracted else None
        if not data or len(data) > MAX_IMAGE_BYTES:
            continue
        ext = str(extracted.get("ext") or "png").lower()
        mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "gif": "image/gif"}.get(ext)
        if mime is None:
            try:
                pix = pymupdf.Pixmap(document, xref)
                if pix.n - pix.alpha >= 4:
                    pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
                data, mime, ext = pix.tobytes("png"), "image/png", "png"
            except Exception:
                continue
        key = f"pdf-{number}-{xref}.{ext}"
        assets.append(Asset(key=key, mime=mime, data=data))
        top = rects[0].y0 if rects else 0.0
        placed.append((top, Block(type="image", src=key)))
        report["images"] += 1
    return placed, boxes


def _note_groups(lines: list[Line]) -> list[list[Line]]:
    groups: list[list[Line]] = []
    for line in lines:
        if _NOTE_START.match(line.text) or not groups:
            groups.append([line])
        else:
            groups[-1].append(line)
    return groups


def _inside(line: Line, box: tuple[float, float, float, float]) -> bool:
    x0, y0, x1, y1 = box
    cx, cy = (line.x0 + line.x1) / 2, (line.y0 + line.y1) / 2
    return x0 - 1 <= cx <= x1 + 1 and y0 - 1 <= cy <= y1 + 1


def extract(path: Path, *, max_pages: int = 3000) -> Extracted:
    pymupdf, document = _open(path)
    report: Counter = Counter()
    assets: list[Asset] = []
    pages: dict[int, list[Line]] = {}
    heights: dict[int, float] = {}
    widths: dict[int, float] = {}
    extras: dict[int, list[tuple[float, Block]]] = {}
    title = ""
    try:
        title = str((document.metadata or {}).get("title") or "").strip()
        for page in document:
            number = page.number + 1
            if number > max_pages:
                break
            heights[number] = page.rect.height
            widths[number] = page.rect.width
            lines = _lines_of(page, number)
            placed, boxes = _tables_and_images(document, pymupdf, page, number, assets, report)
            for line in lines:
                if any(_inside(line, box) for box in boxes):
                    line.role = "table"
            pages[number] = lines
            extras[number] = placed
    finally:
        document.close()

    if not any(ln.text for lines in pages.values() for ln in lines):
        raise PdfReadError(f"{path.name} has no extractable text. A scanned book needs OCR first (ocrmypdf).")

    report["running_lines_removed"] = _mark_running(pages, heights)
    everything = [ln for lines in pages.values() for ln in lines]
    body = _body_size(everything)
    _mark_notes(pages, heights, body)
    levels = _heading_levels(everything, body)
    report["two_column_pages"] = 0

    blocks: list[Block] = []
    block_pages: list[int] = []
    open_para: _Para | None = None
    open_page = 0
    hyphens = 0

    deferred: list[tuple[int, Block]] = []

    def emit_deferred() -> None:
        for page_number, note in deferred:
            blocks.append(note)
            block_pages.append(page_number)
        deferred.clear()

    def flush() -> None:
        nonlocal open_para, hyphens
        if open_para is None:
            return
        para = open_para
        open_para = None
        notes = [n for ln in para.lines for n in ln.notes]
        if para.kind == "heading":
            text = " ".join(ln.text for ln in para.lines)
            blocks.append(Block(type="heading", text=text, level=para.level))
        elif para.kind == "list":
            items: list[list[str]] = []
            for ln in para.lines:
                if _BULLET.match(ln.text) or not items:
                    items.append([_BULLET.sub("", ln.text)])
                else:
                    items[-1].append(ln.text)
            joined = []
            for item in items:
                text, count = join_lines(item)
                hyphens += count
                joined.append(text)
            ordered = bool(re.match(r"^\(?\d", para.lines[0].text))
            blocks.append(Block(type="list", items=joined, ordered=ordered, notes=notes))
        else:
            text, count = join_lines([ln.text for ln in para.lines])
            hyphens += count
            blocks.append(Block(type=para.kind, text=text, notes=notes))
        block_pages.append(open_page)
        if para.kind != "heading":
            emit_deferred()

    for number in sorted(pages):
        width = widths[number]
        ordered = _order(pages[number], width)
        if any(ln.column for ln in ordered):
            report["two_column_pages"] += 1
        # Tables and images are placed by their top edge among the lines.
        pending = sorted(extras.get(number, []), key=lambda pair: pair[0])
        gaps = [
            b.y0 - a.y1
            for a, b in itertools.pairwise(ordered)
            if a.role == b.role == "body" and a.column == b.column and -body < b.y0 - a.y1 < body * 3
        ]
        leading = statistics.median(gaps) if gaps else body * 0.3
        columns: dict[int, tuple[float, float]] = {}
        for column in {ln.column for ln in ordered}:
            members = [ln for ln in ordered if ln.column == column and ln.role == "body"]
            if members:
                columns[column] = (min(ln.x0 for ln in members), max(ln.x1 for ln in members))
        prev: Line | None = None
        note_lines: list[Line] = []
        for line in ordered:
            while pending and line.role == "body" and pending[0][0] < line.y0 and (prev is None or pending[0][0] > prev.y0):
                flush()
                blocks.append(pending.pop(0)[1])
                block_pages.append(number)
            left, right = columns.get(line.column, (line.x0, line.x1))
            if line.role == "note":
                note_lines.append(line)
                continue

            level = _is_heading(line, body, levels)
            same_page = prev is not None and prev.page == line.page
            gap = (line.y0 - prev.y1) if same_page and prev is not None and prev.column == line.column else 0.0
            if level:
                if (
                    open_para is not None
                    and open_para.kind == "heading"
                    and open_para.level == level
                    and same_page
                    and gap < body * 1.2
                ):
                    open_para.lines.append(line)
                else:
                    flush()
                    open_para = _Para("heading", [line], level)
                    open_page = number
                prev = line
                continue

            bullet = bool(_BULLET.match(line.text))
            new_para = open_para is None or open_para.kind == "heading"
            if not new_para and open_para is not None:
                last = open_para.lines[-1]
                indent = line.x0 - left
                last_short = last.x1 < right - (right - left) * 0.12
                ends = bool(_SENTENCE_END.search(last.text))
                if not same_page or prev is None or prev.column != line.column:
                    # A new page or column continues the paragraph unless the
                    # last line plainly finished it.
                    new_para = ends and (last_short or line.text[:1].isupper())
                    if last.text.endswith("-") and line.text[:1].islower():
                        new_para = False
                elif gap > leading + body * 0.55:
                    new_para = True
                elif bullet:
                    new_para = open_para.kind != "list"
                elif open_para.kind == "list":
                    new_para = indent < body * 0.8 and ends
                elif ends and (indent > body * 0.9 or last_short):
                    new_para = True
            if new_para or (bullet and open_para is not None and open_para.kind != "list"):
                flush()
                kind = "list" if bullet else "paragraph"
                if kind == "paragraph" and line.italic is False and line.x0 - left > (right - left) * 0.08 and line.x1 < right - (right - left) * 0.08:
                    kind = "quote"
                open_para = _Para(kind, [line])
                open_page = number
            else:
                assert open_para is not None
                if open_para.kind == "quote" and line.x0 - left < (right - left) * 0.05:
                    open_para.kind = "paragraph"
                open_para.lines.append(line)
            prev = line
        while pending:
            flush()
            blocks.append(pending.pop(0)[1])
            block_pages.append(number)
        # Footnotes follow the paragraph that cites them, which may run on
        # to the next page; until it closes they wait.
        for group in _note_groups(note_lines):
            text, count = join_lines([ln.text for ln in group])
            hyphens += count
            match = _NOTE_START.match(text)
            marker = match.group(1) if match else ""
            note_text = text[match.end(1):].lstrip(".) ").strip() if match else text
            deferred.append((number, Block(type="note", text=note_text, marker=marker)))
        if open_para is None or open_para.kind == "heading":
            flush()
            emit_deferred()
    flush()
    emit_deferred()
    report["hyphens_joined"] = hyphens
    report["pages"] = len(pages)
    report["body_font_size"] = body
    report["format"] = "pdf"
    return Extracted(blocks=blocks, assets=assets, report=dict(report), pages=block_pages, title=title)

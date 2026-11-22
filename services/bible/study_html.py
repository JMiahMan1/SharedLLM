"""Structure from e-book markup: EPUB, and MOBI/AZW3 converted to it.

The older importer flattened every spine document to text, which kept the
words and lost everything a reader needs: headings became sentences, lists
became run-on paragraphs, footnotes were spliced into the sentence that cites
them, and a publisher's ``<p class="ct">`` chapter title read as body text.

This reader walks the markup once and keeps the structure the book declares:
``h1``-``h6`` and the usual heading classes, paragraphs, block quotes, lists,
tables, images (kept as assets), and footnotes (``epub:type`` noteref /
footnote / endnote, or the classes publishers use), which become note blocks
instead of text in the middle of a sentence. Styling spans, page-break
markers, navigation and scripts are dropped. A spine document that opens with
a heading starts a new section; one that does not (a chapter split across
files for size) continues the section before it.
"""
from __future__ import annotations

import io
import posixpath
import re
import shutil
import subprocess
import tempfile
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote

from services.bible.study_blocks import Asset, Block, Extracted, join_lines

MAX_IMAGES = 80
MAX_IMAGE_BYTES = 2 * 1024 * 1024

_SKIP = frozenset({"script", "style", "head", "title", "nav", "svg", "math", "noscript", "template"})
_BLOCK = frozenset({
    "p", "div", "section", "article", "header", "footer", "main", "figure", "figcaption",
    "dd", "dt", "dl", "address", "center", "pre", "hr",
})
_HEADING_CLASS = re.compile(
    r"^(?:h([1-6])|heading-?([1-6])?|chapter-?title|chaptertitle|chapter-?head|ct|cn|title|book-?title|"
    r"part-?title|subtitle|sub-?head(?:ing)?|subhead([1-6])?|ah|bh|ch|dh|section-?title|lesson-?title)$",
    re.IGNORECASE,
)
_NOTE_TYPES = {"footnote", "endnote", "rearnote", "note", "footnotes", "endnotes", "rearnotes"}
_NOTE_CLASS = re.compile(r"^(?:foot-?notes?|end-?notes?|fn|fnote|note|notes|footnote-?text)$", re.IGNORECASE)
_MARKER = re.compile(r"^\s*\[?(\d{1,3}|[*\u2020\u2021\u00a7a-z])[\].)]?\s+")


class HtmlReadError(ValueError):
    pass


def _class_level(classes: list[str]) -> int:
    for token in classes:
        match = _HEADING_CLASS.match(token)
        if not match:
            continue
        digit = next((g for g in match.groups() if g), None)
        if digit:
            return int(digit)
        lowered = token.lower()
        if lowered in {"ct", "cn", "title", "booktitle", "book-title", "chapter-title", "chaptertitle",
                       "chapterhead", "chapter-head", "part-title", "parttitle", "lesson-title", "lessontitle"}:
            return 1
        if lowered in {"subtitle", "ah", "section-title", "sectiontitle"}:
            return 2
        return 3
    return 0


class _Markup(HTMLParser):
    def __init__(self, resolve_image) -> None:
        super().__init__(convert_charrefs=True)
        self.resolve_image = resolve_image
        self.blocks: list[Block] = []
        self.skip = 0
        self.drop = 0  # page-break markers and note-reference text
        self.stack: list[tuple[str, dict]] = []
        self.buffer: list[str] = []
        self.kind = "paragraph"
        self.level = 0
        self.notes: list[str] = []
        self.list_items: list[str] | None = None
        self.list_ordered = False
        self.list_depth = 0
        self.item: list[str] | None = None
        self.table: list[list[str]] | None = None
        self.row: list[str] | None = None
        self.cell: list[str] | None = None
        self.quote = 0
        self.note = 0
        self.note_marker = ""
        self.bold_only = True
        self.bold = 0
        self.hyphens = 0
        self.dropped_markers = 0

    # -- helpers ---------------------------------------------------------

    def _attrs(self, attrs) -> dict:
        return {key.lower(): (value or "") for key, value in attrs}

    def _flush(self) -> None:
        text = "".join(self.buffer)
        self.buffer = []
        kind, level, notes = self.kind, self.level, self.notes
        bold_only = self.bold_only
        self.kind, self.level, self.notes, self.bold_only = "paragraph", 0, [], True
        if self.quote and kind == "paragraph":
            kind = "quote"
        if self.note and kind != "heading":
            kind = "note"
        lines = text.split("\n")
        if kind == "quote":
            joined = "\n".join(re.sub(r"\s+", " ", line).strip() for line in lines if line.strip())
        else:
            joined, count = join_lines([re.sub(r"\s+", " ", line) for line in lines])
            self.hyphens += count
        joined = joined.strip()
        if not joined:
            return
        if kind == "paragraph" and bold_only and len(joined.split()) <= 12 and not re.search(r"[.,;:!?]$", joined):
            kind, level = "heading", 5
        if kind == "note":
            marker = self.note_marker
            match = _MARKER.match(joined)
            if match and not marker:
                marker = match.group(1)
                joined = joined[match.end():]
            elif match and match.group(1) == marker:
                joined = joined[match.end():]
            self.note_marker = ""
            self.blocks.append(Block(type="note", text=joined.strip(), marker=marker))
            return
        self.blocks.append(Block(type=kind, text=joined, level=level, notes=notes))

    def _flush_list(self) -> None:
        if self.item is not None:
            self._end_item()
        items = [item for item in (self.list_items or []) if item]
        if items:
            self.blocks.append(Block(type="list", items=items, ordered=self.list_ordered))
        self.list_items = None

    def _end_item(self) -> None:
        if self.item is None:
            return
        text, count = join_lines([re.sub(r"\s+", " ", "".join(self.item))])
        self.hyphens += count
        if self.list_items is not None and text.strip():
            self.list_items.append(text.strip())
        self.item = None

    # -- parser ----------------------------------------------------------

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        a = self._attrs(attrs)
        if tag in {"br"}:
            self._text("\n")
            return
        if tag in {"img", "image"}:
            src = a.get("src") or a.get("xlink:href") or a.get("href") or ""
            key = self.resolve_image(src) if src else ""
            if key or a.get("alt"):
                if self.buffer and "".join(self.buffer).strip():
                    self._flush()
                self.blocks.append(Block(type="image", src=key, text=a.get("alt", "").strip()))
            return
        if tag in {"meta", "link", "input", "col", "wbr", "source"}:
            return
        self.stack.append((tag, a))
        if self.skip or tag in _SKIP:
            self.skip += 1
            return
        epub_type = set((a.get("epub:type") or a.get("role") or "").lower().replace("doc-", "").split())
        classes = (a.get("class") or "").split()
        if "pagebreak" in epub_type or "page-break" in classes or "pagenum" in classes or "page-number" in classes:
            self.drop += 1
            return
        if tag in {"a", "span", "sup"} and ("noteref" in epub_type or "footnote-ref" in classes or "noteref" in classes):
            self.drop += 1
            self.dropped_markers += 1
            self.stack[-1] = (tag, {**a, "_marker": "1"})
            return
        if tag == "sup" and not self.drop:
            self.stack[-1] = (tag, {**a, "_sup": "1"})
        if tag in {"b", "strong"}:
            self.bold += 1
        if epub_type & _NOTE_TYPES or (tag in {"aside", "div", "section", "p", "li"} and any(_NOTE_CLASS.match(c) for c in classes)):
            if self.buffer and "".join(self.buffer).strip():
                self._flush()
            self.note += 1
            self.stack[-1] = (tag, {**a, "_note": "1"})
            if tag == "li" and self.list_items is not None:
                return
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._flush()
            self.kind, self.level = "heading", int(tag[1])
            return
        if tag == "blockquote":
            self._flush()
            self.quote += 1
            return
        if tag in {"ul", "ol"}:
            if self.list_items is None:
                self._flush()
                self.list_items = []
                self.list_ordered = tag == "ol"
            else:
                self._end_item()
            self.list_depth += 1
            return
        if tag == "li":
            if self.note:
                self._flush()
                return
            if self.list_items is None:
                self._flush()
                self.list_items = []
            self._end_item()
            self.item = []
            return
        if tag == "table":
            self._flush()
            self.table = []
            return
        if tag == "tr" and self.table is not None:
            self.row = []
            return
        if tag in {"td", "th"} and self.row is not None:
            self.cell = []
            return
        if tag in _BLOCK and self.item is None and self.cell is None:
            self._flush()
            level = _class_level(classes)
            if level:
                self.kind, self.level = "heading", level

    def handle_endtag(self, tag):
        tag = tag.lower()
        if not self.stack:
            return
        # Tolerate unclosed tags: pop to the nearest matching one.
        index = len(self.stack) - 1
        while index >= 0 and self.stack[index][0] != tag:
            index -= 1
        if index < 0:
            return
        while len(self.stack) > index:
            open_tag, a = self.stack.pop()
            self._close(open_tag, a)

    def _close(self, tag: str, a: dict) -> None:
        if self.skip:
            self.skip -= 1
            return
        classes = (a.get("class") or "").split()
        epub_type = set((a.get("epub:type") or a.get("role") or "").lower().replace("doc-", "").split())
        if "pagebreak" in epub_type or "page-break" in classes or "pagenum" in classes or "page-number" in classes:
            self.drop = max(0, self.drop - 1)
            return
        if a.get("_marker"):
            self.drop = max(0, self.drop - 1)
            return
        if tag in {"b", "strong"}:
            self.bold = max(0, self.bold - 1)
        if a.get("_note"):
            if tag != "li" or self.list_items is None:
                self._flush()
            self.note = max(0, self.note - 1)
            return
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._flush()
            return
        if tag == "blockquote":
            self._flush()
            self.quote = max(0, self.quote - 1)
            return
        if tag == "li":
            if self.note:
                self._flush()
                return
            self._end_item()
            return
        if tag in {"ul", "ol"}:
            self.list_depth = max(0, self.list_depth - 1)
            if self.list_depth == 0:
                self._flush_list()
            return
        if tag in {"td", "th"} and self.row is not None and self.cell is not None:
            self.row.append(re.sub(r"\s+", " ", "".join(self.cell)).strip())
            self.cell = None
            return
        if tag == "tr" and self.table is not None and self.row is not None:
            if any(self.row):
                self.table.append(self.row)
            self.row = None
            return
        if tag == "table" and self.table is not None:
            rows = self.table
            self.table = None
            if len(rows) >= 1 and max((len(r) for r in rows), default=0) >= 2:
                self.blocks.append(Block(type="table", rows=rows))
            elif rows:
                for row in rows:
                    self.blocks.append(Block(type="paragraph", text=" ".join(row)))
            return
        if tag in _BLOCK and self.item is None and self.cell is None:
            self._flush()

    def handle_data(self, data):
        if self.skip:
            return
        if self.drop:
            # The text of a note reference is the marker the paragraph cites.
            if data.strip() and any(a.get("_marker") for _t, a in self.stack):
                self.notes.append(data.strip()[:4])
            return
        open_sup = next((a for t, a in reversed(self.stack) if t == "sup"), None)
        if open_sup is not None and open_sup.get("_sup") and data.strip() and len(data.strip()) <= 3 and not self.note:
            # A bare superscript marker ("\u00b9", "a") in running prose.
            stripped = data.strip()
            if stripped.isdigit() or (len(stripped) == 1 and stripped.isalpha()) or stripped in "*\u2020\u2021":
                self.notes.append(stripped)
                self.dropped_markers += 1
                return
        if self.note and not self.note_marker and not "".join(self.buffer).strip():
            match = re.match(r"^\s*\[?(\d{1,3}|[*\u2020\u2021])[\].)]?\s*$", data)
            if match:
                self.note_marker = match.group(1)
                return
        self._text(data)

    def _text(self, data: str) -> None:
        if self.skip or self.drop:
            return
        if self.cell is not None:
            self.cell.append(data)
        elif self.item is not None:
            self.item.append(data)
        else:
            if data.strip() and not self.bold:
                self.bold_only = False
            self.buffer.append(data)

    def close(self):
        super().close()
        while self.stack:
            tag, a = self.stack.pop()
            self._close(tag, a)
        self._flush()
        if self.list_items is not None:
            self._flush_list()


def parse_markup(markup: str, resolve_image=lambda src: "") -> tuple[list[Block], dict]:
    parser = _Markup(resolve_image)
    parser.feed(markup)
    parser.close()
    blocks = [b for b in parser.blocks if b.text or b.items or b.rows or b.src]
    return blocks, {"hyphens_joined": parser.hyphens, "note_markers_lifted": parser.dropped_markers}


def _mime(name: str) -> str | None:
    ext = name.rsplit(".", 1)[-1].lower()
    return {
        "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "gif": "image/gif",
        "webp": "image/webp", "svg": "image/svg+xml",
    }.get(ext)


def extract_epub_bytes(data: bytes) -> Extracted:
    from services.common.epub_text import EpubTextError, _match_member, _spine

    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, OSError) as exc:
        raise HtmlReadError(f"not a readable EPUB: {exc}") from exc
    assets: list[Asset] = []
    seen_assets: dict[str, str] = {}
    blocks: list[Block] = []
    breaks: list[int] = []
    report = {"format": "epub", "hyphens_joined": 0, "note_markers_lifted": 0, "documents": 0, "images": 0}
    title = ""
    with archive:
        if "META-INF/encryption.xml" in archive.namelist():
            names = archive.read("META-INF/encryption.xml").decode("utf-8", "replace")
            if "EncryptedData" in names and "font" not in names.lower():
                raise HtmlReadError("This e-book is DRM protected, so its text cannot be read. Import a DRM-free copy.")
        try:
            documents = _spine(archive)
        except EpubTextError as exc:
            raise HtmlReadError(str(exc)) from exc
        try:
            from services.common.epub_text import _rootfile

            opf = archive.read(_rootfile(archive)).decode("utf-8", "replace")
            match = re.search(r"<dc:title[^>]*>(.*?)</dc:title>", opf, re.S)
            title = re.sub(r"<[^>]+>", "", match.group(1)).strip() if match else ""
        except Exception:
            title = ""
        for name, _ident, _hint in documents:
            base = posixpath.dirname(name)

            def resolve(src: str, base: str = base) -> str:
                if src.startswith("data:") or len(assets) >= MAX_IMAGES:
                    return ""
                path = posixpath.normpath(posixpath.join(base, unquote(src.split("#")[0])))
                if path in seen_assets:
                    return seen_assets[path]
                member = _match_member(archive, path)
                mime = _mime(path)
                if member is None or mime is None:
                    return ""
                content = archive.read(member)
                if len(content) > MAX_IMAGE_BYTES:
                    return ""
                key = "epub-" + re.sub(r"[^A-Za-z0-9._-]+", "-", path)[-80:]
                assets.append(Asset(key=key, mime=mime, data=content))
                seen_assets[path] = key
                report["images"] += 1
                return key

            markup = archive.read(name).decode("utf-8", "replace")
            doc_blocks, stats = parse_markup(markup, resolve)
            report["documents"] += 1
            report["hyphens_joined"] += stats["hyphens_joined"]
            report["note_markers_lifted"] += stats["note_markers_lifted"]
            if not doc_blocks:
                continue
            first = next((b for b in doc_blocks if b.type != "image"), None)
            if first is not None and first.type == "heading" and blocks:
                breaks.append(len(blocks))
            blocks.extend(doc_blocks)
    if not blocks:
        raise HtmlReadError("the e-book yielded no readable text")
    return Extracted(blocks=blocks, assets=assets, report=report, breaks=breaks, title=title)


def extract_html_file(path: Path) -> Extracted:
    """A single HTML book (what MOBI 7 unpacks to), images resolved beside it."""
    assets: list[Asset] = []
    folder = path.parent

    def resolve(src: str) -> str:
        if len(assets) >= MAX_IMAGES or src.startswith("data:"):
            return ""
        target = (folder / unquote(src.split("#")[0])).resolve()
        mime = _mime(target.name)
        if mime is None or not target.is_file() or not str(target).startswith(str(folder.resolve())):
            return ""
        content = target.read_bytes()
        if len(content) > MAX_IMAGE_BYTES:
            return ""
        key = "html-" + re.sub(r"[^A-Za-z0-9._-]+", "-", target.name)[-80:]
        assets.append(Asset(key=key, mime=mime, data=content))
        return key

    markup = path.read_text("utf-8", errors="replace")
    # MOBI 7 marks page breaks with <mbp:pagebreak/>; nothing else needs it.
    markup = re.sub(r"<mbp:pagebreak\s*/?>", "", markup, flags=re.IGNORECASE)
    blocks, stats = parse_markup(markup, resolve)
    if not blocks:
        raise HtmlReadError(f"{path.name} yielded no readable text")
    return Extracted(blocks=blocks, assets=assets, report={"format": "html", **stats})


def _convert_with_calibre(path: Path, workdir: Path) -> Path | None:
    tool = shutil.which("ebook-convert")
    if not tool:
        return None
    target = workdir / (path.stem + ".epub")
    try:
        subprocess.run([tool, str(path), str(target)], check=True, capture_output=True, timeout=600)
    except (subprocess.SubprocessError, OSError) as exc:
        raise HtmlReadError(f"Calibre could not convert {path.name}: {exc}") from exc
    return target if target.exists() else None


def extract_kindle(path: Path) -> Extracted:
    """MOBI, AZW and AZW3: unpack to EPUB (or HTML for old MOBI) and read that.

    Uses the ``mobi`` package (KindleUnpack) when it is installed and Calibre's
    ``ebook-convert`` when it is on the path. A DRM-protected Kindle file can be
    read by neither, and says so.
    """
    with tempfile.TemporaryDirectory(prefix="study-kindle-") as tmp:
        workdir = Path(tmp)
        unpacked: Path | None = None
        error = ""
        try:
            import mobi  # type: ignore[import-not-found]

            folder, filepath = mobi.extract(str(path))
            unpacked = Path(filepath)
            # mobi extracts to its own temp folder; move it under ours so it is cleaned up.
            moved = workdir / "unpacked"
            shutil.move(folder, moved)
            unpacked = moved / unpacked.relative_to(folder)
        except ImportError:
            error = "the mobi package is not installed"
        except Exception as exc:
            text = str(exc)
            if "drm" in text.lower() or "encrypt" in text.lower():
                raise HtmlReadError(
                    f"{path.name} is DRM protected, so its text cannot be read. Import a DRM-free copy."
                ) from exc
            error = text or exc.__class__.__name__
        if unpacked is None or not unpacked.exists():
            unpacked = _convert_with_calibre(path, workdir)
        if unpacked is None:
            raise HtmlReadError(
                f"{path.name} could not be unpacked ({error or 'no converter'}). The bible service needs the "
                "`mobi` package or Calibre's ebook-convert to read Kindle books."
            )
        extracted = (
            extract_epub_bytes(unpacked.read_bytes())
            if unpacked.suffix.lower() == ".epub"
            else extract_html_file(unpacked)
        )
        extracted.report["format"] = path.suffix.lower().lstrip(".")
        return extracted

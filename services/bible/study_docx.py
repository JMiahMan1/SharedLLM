"""Structure from a Word document (.docx), with the standard library only.

A .docx is a zip of WordprocessingML. Its structure is explicit, which is why
plain-text extraction is such a waste of it: every paragraph names its style
(``Heading1``, ``Title``, ``Quote``, ``ListParagraph``), list membership is
``w:numPr``, footnotes live in ``footnotes.xml`` and are cited by
``w:footnoteReference``, tables are ``w:tbl`` and pictures are ``a:blip``
relationships. Custom styles are resolved through ``styles.xml`` (their name
and outline level), so a study written with a house style called "Lesson
Title" based on Heading 1 still reads as a heading.
"""
from __future__ import annotations

import posixpath
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from services.bible.study_blocks import Asset, Block, Extracted

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
MAX_IMAGES = 80
MAX_IMAGE_BYTES = 2 * 1024 * 1024


class DocxReadError(ValueError):
    pass


def _styles(archive: zipfile.ZipFile) -> dict[str, dict]:
    try:
        root = ET.fromstring(archive.read("word/styles.xml"))
    except (KeyError, ET.ParseError):
        return {}
    styles: dict[str, dict] = {}
    for style in root.iter(f"{W}style"):
        sid = style.get(f"{W}styleId") or ""
        name_node = style.find(f"{W}name")
        based = style.find(f"{W}basedOn")
        outline = style.find(f"{W}pPr/{W}outlineLvl")
        styles[sid] = {
            "name": (name_node.get(f"{W}val") if name_node is not None else sid) or sid,
            "based": based.get(f"{W}val") if based is not None else "",
            "outline": int(outline.get(f"{W}val")) if outline is not None and outline.get(f"{W}val", "").isdigit() else None,
        }
    return styles


def _style_kind(style_id: str, styles: dict[str, dict]) -> tuple[str, int]:
    """What a paragraph style means: (kind, heading level)."""
    seen = set()
    current = style_id
    while current and current not in seen:
        seen.add(current)
        info = styles.get(current, {"name": current, "based": "", "outline": None})
        name = str(info.get("name") or current).lower().replace(" ", "")
        if info.get("outline") is not None and info["outline"] < 6:
            return "heading", int(info["outline"]) + 1
        match = re.match(r"^heading([1-6])$", name) or re.match(r"^heading([1-6])$", current.lower())
        if match:
            return "heading", int(match.group(1))
        if name in {"title"}:
            return "heading", 1
        if name in {"subtitle"}:
            return "heading", 2
        if name in {"quote", "intensequote", "blocktext", "blockquote"}:
            return "quote", 0
        if name.startswith("listparagraph") or name.startswith("listbullet") or name.startswith("listnumber"):
            return "list", 0
        if name in {"footnotetext", "endnotetext"}:
            return "note", 0
        current = str(info.get("based") or "")
    return "paragraph", 0


def _rels(archive: zipfile.ZipFile) -> dict[str, str]:
    try:
        root = ET.fromstring(archive.read("word/_rels/document.xml.rels"))
    except (KeyError, ET.ParseError):
        return {}
    return {rel.get("Id", ""): rel.get("Target", "") for rel in root.iter(f"{REL}Relationship")}


def _run_text(node) -> tuple[str, list[str], list[str], bool]:
    """A paragraph's text, footnote ids it cites, image rel ids, and whether it is all bold."""
    parts: list[str] = []
    notes: list[str] = []
    images: list[str] = []
    bold_chars = total = 0
    for run in node.iter(f"{W}r"):
        props = run.find(f"{W}rPr")
        bold = props is not None and props.find(f"{W}b") is not None and props.find(f"{W}b").get(f"{W}val", "true") not in {"0", "false"}
        vertical = props.find(f"{W}vertAlign") if props is not None else None
        superscript = vertical is not None and vertical.get(f"{W}val") == "superscript"
        for child in run:
            tag = child.tag
            if tag == f"{W}t":
                text = child.text or ""
                if superscript and text.strip().isdigit():
                    notes.append(text.strip())
                    continue
                parts.append(text)
                total += len(text.strip())
                if bold:
                    bold_chars += len(text.strip())
            elif tag == f"{W}tab":
                parts.append(" ")
            elif tag in {f"{W}br", f"{W}cr"}:
                parts.append("\n")
            elif tag == f"{W}noBreakHyphen":
                parts.append("-")
            elif tag == f"{W}softHyphen":
                continue
            elif tag in {f"{W}footnoteReference", f"{W}endnoteReference"}:
                notes.append(child.get(f"{W}id", ""))
        for blip in run.iter(f"{A}blip"):
            rid = blip.get(f"{R}embed")
            if rid:
                images.append(rid)
    text = "".join(parts)
    return text, notes, images, total > 0 and bold_chars / total > 0.8


def _notes(archive: zipfile.ZipFile, name: str) -> dict[str, str]:
    try:
        root = ET.fromstring(archive.read(name))
    except (KeyError, ET.ParseError):
        return {}
    out: dict[str, str] = {}
    for note in list(root):
        nid = note.get(f"{W}id", "")
        kind = note.get(f"{W}type", "")
        if kind in {"separator", "continuationSeparator", "continuationNotice"} or nid in {"-1", "0"}:
            continue
        texts = [_run_text(p)[0].strip() for p in note.iter(f"{W}p")]
        out[nid] = " ".join(t for t in texts if t)
    return out


def extract(path: Path) -> Extracted:
    try:
        archive = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise DocxReadError(f"{path.name} is not a readable .docx: {exc}") from exc
    with archive:
        try:
            root = ET.fromstring(archive.read("word/document.xml"))
        except (KeyError, ET.ParseError) as exc:
            raise DocxReadError(f"{path.name} has no readable document body: {exc}") from exc
        styles = _styles(archive)
        rels = _rels(archive)
        footnotes = _notes(archive, "word/footnotes.xml")
        endnotes = _notes(archive, "word/endnotes.xml")
        body = root.find(f"{W}body")
        if body is None:
            raise DocxReadError(f"{path.name} has no document body")
        blocks: list[Block] = []
        assets: list[Asset] = []
        cited: list[str] = []
        pending_list: Block | None = None

        def close_list() -> None:
            nonlocal pending_list
            if pending_list is not None and pending_list.items:
                blocks.append(pending_list)
            pending_list = None

        def image_block(rid: str) -> Block | None:
            target = rels.get(rid, "")
            if not target or len(assets) >= MAX_IMAGES:
                return None
            member = posixpath.normpath(posixpath.join("word", target))
            ext = member.rsplit(".", 1)[-1].lower()
            mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif"}.get(ext)
            if mime is None:
                return None
            try:
                data = archive.read(member)
            except KeyError:
                return None
            if len(data) > MAX_IMAGE_BYTES:
                return None
            key = "docx-" + re.sub(r"[^A-Za-z0-9._-]+", "-", posixpath.basename(member))
            assets.append(Asset(key=key, mime=mime, data=data))
            return Block(type="image", src=key)

        for node in body:
            if node.tag == f"{W}tbl":
                close_list()
                rows = []
                for row in node.iter(f"{W}tr"):
                    cells = []
                    for cell in row.findall(f"{W}tc"):
                        cells.append(" ".join(_run_text(p)[0].strip() for p in cell.iter(f"{W}p")).strip())
                    if any(cells):
                        rows.append(cells)
                if rows:
                    blocks.append(Block(type="table", rows=rows))
                continue
            if node.tag != f"{W}p":
                continue
            props = node.find(f"{W}pPr")
            style_node = props.find(f"{W}pStyle") if props is not None else None
            style = style_node.get(f"{W}val", "") if style_node is not None else ""
            kind, level = _style_kind(style, styles)
            numbered = props is not None and props.find(f"{W}numPr") is not None
            outline = props.find(f"{W}outlineLvl") if props is not None else None
            if outline is not None and (outline.get(f"{W}val") or "").isdigit() and int(outline.get(f"{W}val")) < 6:
                kind, level = "heading", int(outline.get(f"{W}val")) + 1
            text, notes, images, all_bold = _run_text(node)
            cited.extend(notes)
            for rid in images:
                block = image_block(rid)
                if block is not None:
                    close_list()
                    blocks.append(block)
            lines = [line.strip() for line in text.split("\n")]
            text = " ".join(line for line in lines if line) if kind != "quote" else "\n".join(line for line in lines if line)
            if not text.strip():
                continue
            if (kind == "list" or numbered) and kind != "heading":
                if pending_list is None:
                    num = props.find(f"{W}numPr/{W}numId") if props is not None else None
                    pending_list = Block(type="list", ordered=bool(num is not None and style.lower().find("bullet") < 0 and _looks_numbered(archive, num)))
                pending_list.items.append(text)
                pending_list.notes.extend(notes)
                continue
            close_list()
            if kind == "paragraph" and all_bold and len(text.split()) <= 12 and not re.search(r"[.,;:!?]$", text):
                kind, level = "heading", 5
            blocks.append(Block(type=kind, text=text, level=level, notes=notes))
        close_list()
        for nid in cited:
            note_text = footnotes.get(nid) or endnotes.get(nid)
            if note_text:
                blocks.append(Block(type="note", text=note_text, marker=nid))
    if not any(b.text or b.items or b.rows for b in blocks):
        raise DocxReadError(f"{path.name} has no text")
    return Extracted(blocks=_place_notes(blocks), assets=assets, report={"format": "docx", "styles": len(styles)})


def _place_notes(blocks: list[Block]) -> list[Block]:
    """Move each footnote to just after the first block that cites it."""
    notes = {b.marker: b for b in blocks if b.type == "note" and b.marker}
    if not notes:
        return blocks
    out: list[Block] = []
    placed: set[str] = set()
    body = [b for b in blocks if not (b.type == "note" and b.marker in notes)]
    for block in body:
        out.append(block)
        for marker in block.notes:
            if marker in notes and marker not in placed:
                out.append(notes[marker])
                placed.add(marker)
    out.extend(note for marker, note in notes.items() if marker not in placed)
    # Number the notes as a reader sees them, 1, 2, 3, in order of first citation.
    renumber: dict[str, str] = {}
    for block in out:
        if block.type == "note" and block.marker:
            renumber[block.marker] = str(len(renumber) + 1)
    for block in out:
        if block.type == "note" and block.marker in renumber:
            block.marker = renumber[block.marker]
        block.notes = [renumber.get(n, n) for n in block.notes]
    return out


def _looks_numbered(archive: zipfile.ZipFile, num) -> bool:
    try:
        root = ET.fromstring(archive.read("word/numbering.xml"))
    except (KeyError, ET.ParseError):
        return False
    num_id = num.get(f"{W}val")
    abstract = None
    for node in root.iter(f"{W}num"):
        if node.get(f"{W}numId") == num_id:
            ref = node.find(f"{W}abstractNumId")
            abstract = ref.get(f"{W}val") if ref is not None else None
    if abstract is None:
        return False
    for node in root.iter(f"{W}abstractNum"):
        if node.get(f"{W}abstractNumId") == abstract:
            fmt = node.find(f"{W}lvl/{W}numFmt")
            return fmt is not None and fmt.get(f"{W}val") not in {"bullet", "none"}
    return False

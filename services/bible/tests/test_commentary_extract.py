# services/bible/tests/test_commentary_extract.py
"""Commentary extraction: section titles become notes when no verse anchors exist.

The real case is Tom Holland's *Romans: The Divine Marriage* in the family's
Calibre shelf: converted from a word processor, it has no canonical verse
anchors, so the anchor harvester yields an empty 66-book skeleton and zero
notes. Its structure is chapter headings ("Romans 1"), section titles ending
in a passage reference ("... (1:1-4)"), and shaded paragraphs quoting the
scripture under discussion -- which is what this mode reads instead.
"""
from __future__ import annotations

import zipfile
from pathlib import Path

from services.bible import epub_import
from services.bible.books import BOOKS
from services.bible.epub_import import extract

OPF = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>{title}</dc:title></metadata>
  <manifest><item id="d1" href="part1.html" media-type="application/xhtml+xml"/></manifest>
  <spine><itemref idref="d1"/></spine>
</package>
"""


def build(path: Path, body: str, *, title: str = "A Commentary") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container version="1.0" '
            'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="OEBPS/content.opf" '
            'media-type="application/oebps-package+xml"/></rootfiles></container>',
        )
        archive.writestr("OEBPS/content.opf", OPF.format(title=title))
        archive.writestr("OEBPS/part1.html", f"<html><body>{body}</body></html>")
    return path


ROMANS = next(i for i, entry in enumerate(BOOKS, start=1) if entry["name"] == "Romans")

STRUCTURED = """
<h1>Introduction</h1>
<p>Unanchored preface prose that names no passage.</p>
<h1>Romans 1</h1>
<p style="text-transform:uppercase">The Messiah King and His Servant (1:1-4)</p>
<p style="background-color:#f2f2f2">Paul, a servant of Christ Jesus, called to be an apostle.</p>
<p>First the argument begins with obedience of faith.</p>
<h3>Subpoint</h3>
<p>More commentary under the same section.</p>
<p style="text-transform:uppercase">The Messiah King and His People (1:5-7)</p>
<p style="background-color:#f2f2f2">Through him and for his name's sake we received grace.</p>
<p>Second section prose about grace and apostleship.</p>
<h1>Romans 2</h1>
<p>Loose prose after a chapter change with no section title.</p>
"""


def test_a_commentary_without_verse_anchors_is_read_for_its_section_notes(tmp_path: Path) -> None:
    result = extract(str(build(tmp_path / "c.epub", STRUCTURED)))
    assert result["mode"] == "commentary"
    assert result["verse_count"] == 0
    assert len(result["notes"]) == 2
    first = result["notes"][0]
    assert (first.book, first.chapter, first.verse) == (ROMANS, 1, 1)
    assert first.kind == "commentary"
    assert "The Messiah King and His Servant (1:1-4)" in first.body
    assert "First the argument begins" in first.body
    assert "Subpoint" in first.body
    second = result["notes"][1]
    assert (second.book, second.chapter, second.verse) == (ROMANS, 1, 5)
    assert "Second section prose" in second.body


def test_shaded_scripture_is_not_filed_as_commentary(tmp_path: Path) -> None:
    result = extract(str(build(tmp_path / "s.epub", STRUCTURED)))
    bodies = "\n".join(note.body for note in result["notes"])
    assert "Paul, a servant of Christ Jesus" not in bodies
    assert "we received grace" not in bodies


def test_prose_before_the_first_section_is_counted_not_guessed(tmp_path: Path) -> None:
    result = extract(str(build(tmp_path / "i.epub", STRUCTURED)))
    bodies = "\n".join(note.body for note in result["notes"])
    assert "Unanchored preface prose" not in bodies
    assert "Loose prose after a chapter change" not in bodies
    warnings = " ".join(result["warnings"])
    assert "2 paragraphs carry no passage heading" in warnings
    assert "were not filed as notes" in warnings


def test_repeated_passage_titles_are_kept_as_separate_ordinals(tmp_path: Path) -> None:
    body = """
    <h1>Romans 1</h1>
    <p>From one author (1:1)</p><p>First treatment.</p>
    <p>From another hand (1:1)</p><p>Second treatment.</p>
    """
    result = extract(str(build(tmp_path / "r.epub", body)))
    assert len(result["notes"]) == 2
    assert [note.ordinal for note in result["notes"]] == [0, 1]
    assert all((note.chapter, note.verse) == (1, 1) for note in result["notes"])


def test_an_anchored_source_is_never_read_as_a_commentary(tmp_path: Path) -> None:
    body = '<h1>Genesis</h1><p class="sl1" id="v010010001">In the beginning.</p>'
    result = extract(str(build(tmp_path / "a.epub", body)))
    assert result["mode"] == "verse"
    assert result["verse_count"] == 1
    assert result["flat"][(1, 1, 1)] == "In the beginning."


def test_a_file_with_neither_anchors_nor_sections_is_not_claimed_as_a_commentary(
    tmp_path: Path,
) -> None:
    result = extract(str(build(tmp_path / "p.epub", "<p>Just prose with no structure at all.</p>")))
    assert result.get("mode") != "commentary"
    assert result["notes"] == []


def test_report_lines_name_the_unanchored_paragraphs(tmp_path: Path) -> None:
    result = extract(str(build(tmp_path / "w.epub", STRUCTURED)))
    lines = epub_import.report_lines(result)
    assert any("were not filed as notes" in line for line in lines)

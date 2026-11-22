"""Made-up study books in every format the importer reads.

Everything is generated here, so no copyrighted text is checked in: the prose is
placeholder wording, and the scripture references are only references.
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

PROSE = (
    "This placeholder paragraph wraps over several lines so the importer has to join them "
    "back into one paragraph, and it mentions Romans 8:28 and John 3:16 along the way."
)


def pdf_book(path: Path, *, pages: int = 6, two_columns: bool = False) -> Path:
    """A study guide PDF with every layout problem a real one has.

    Running head and page number on every page, a word split at a line end, a
    paragraph that runs over a page break, lesson headings in a bigger bold
    face, a discussion question list, and a footnote in small type.
    """
    import pymupdf

    document = pymupdf.open()
    for number in range(1, pages + 1):
        page = document.new_page(width=420, height=595)
        page.insert_text((40, 30), "A Made-up Study Guide", fontsize=8)
        page.insert_text((360, 575), str(number), fontsize=8)
        y = 72.0
        if number in (1, 1 + pages // 2):
            lesson = 1 if number == 1 else 2
            page.insert_text((40, y), f"Lesson {lesson}: Walking in the Light", fontsize=16, fontname="hebo")
            y += 30
        if two_columns and number == 2:
            for row in range(8):
                page.insert_text((40, y + row * 13), f"Left column line {row + 1} of the page.", fontsize=9)
                page.insert_text((225, y + row * 13), f"Right column line {row + 1} here.", fontsize=9)
            page.insert_text((40, 520), f"1 A footnote for page {number}.", fontsize=7)
            continue
        lines = [
            f"Page {number} opens a paragraph that wraps across a",
            "few lines, and the word redemp-",
            "tion is split across the break, see Romans 8:28 and John 3:16.",
            "",
            "A second paragraph begins after a gap. It speaks of the cove-",
            "nant and runs on to the",
        ]
        for text in lines:
            if not text:
                y += 10
                continue
            page.insert_text((40, y), text, fontsize=10)
            y += 13
        # The last line of the page ends mid-sentence; the next page continues it.
        page.insert_text((40, y), "foot of the page and", fontsize=10)
        y += 13
        if number in (pages // 2, pages):
            page.insert_text((40, y + 14), "Questions for Discussion", fontsize=10, fontname="hebo")
            page.insert_text((40, y + 30), "1. What does walking in the light mean to you?", fontsize=10)
            page.insert_text((40, y + 43), "2. How does Psalm 119:105 help?", fontsize=10)
        page.insert_text((40, 520), f"1 A footnote for page {number}.", fontsize=7)
    path.write_bytes(document.tobytes())
    document.close()
    return path


def _zip(entries: dict[str, bytes | str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return buffer.getvalue()


# A one-pixel PNG.
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082"
)


def epub_book(path: Path) -> Path:
    """An EPUB with the markup habits publishers have."""
    chapter1 = (
        '<html xmlns:epub="http://www.idpf.org/2007/ops"><head><style>p{margin:0}</style></head><body>'
        '<p class="ct">Lesson 1</p><p class="ah">The Vine and the Branches</p>'
        '<p><span class="calibre1">Abide in me, the study says, with a ref to </span>'
        '<span class="calibre2">John 15:5</span>.<a epub:type="noteref" href="#fn1"><sup>1</sup></a></p>'
        '<span epub:type="pagebreak" title="12">12</span>'
        "<p>Ligatures like \ufb01rst and \ufb02ow and a mis-decoded God\u00e2\u20ac\u2122s should read cleanly.</p>"
        "<blockquote><p>A quoted line of placeholder text.</p></blockquote>"
        "<ul><li>First point to remember</li><li>Second point to remember</li></ul>"
        "<table><tr><th>Day</th><th>Reading</th></tr><tr><td>1</td><td>Psalm 1</td></tr></table>"
        '<img src="../images/vine.png" alt="A vine"/>'
        "<h2>Questions</h2><ol><li>What does it mean to abide?</li><li>Where do you see fruit?</li></ol>"
        '<aside epub:type="footnote" id="fn1"><p>1. A footnote about the vine.</p></aside>'
        "</body></html>"
    )
    chapter2 = (
        "<html><body><h1>Lesson 2</h1><h2>The Good Shepherd</h2>"
        "<p>Second lesson placeholder text about the shepherd in John 10:11.</p></body></html>"
    )
    chapter2b = "<html><body><p>This file continues lesson two after a split.</p></body></html>"
    opf = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>A Made-up EPUB Study</dc:title></metadata>
  <manifest>
    <item id="c1" href="text/c1.xhtml" media-type="application/xhtml+xml"/>
    <item id="c2" href="text/c2.xhtml" media-type="application/xhtml+xml"/>
    <item id="c2b" href="text/c2b.xhtml" media-type="application/xhtml+xml"/>
    <item id="img" href="images/vine.png" media-type="image/png"/>
  </manifest>
  <spine><itemref idref="c1"/><itemref idref="c2"/><itemref idref="c2b"/></spine>
</package>"""
    path.write_bytes(
        _zip(
            {
                "mimetype": "application/epub+zip",
                "META-INF/container.xml": (
                    '<?xml version="1.0"?><container version="1.0" '
                    'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
                    '<rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
                    "</rootfiles></container>"
                ),
                "OEBPS/content.opf": opf,
                "OEBPS/text/c1.xhtml": chapter1,
                "OEBPS/text/c2.xhtml": chapter2,
                "OEBPS/text/c2b.xhtml": chapter2b,
                "OEBPS/images/vine.png": PNG,
            }
        )
    )
    return path


_W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def _p(text: str, style: str = "", numbered: bool = False, footnote: str = "", bold: bool = False) -> str:
    props = ""
    if style or numbered:
        props = "<w:pPr>"
        if style:
            props += f'<w:pStyle w:val="{style}"/>'
        if numbered:
            props += '<w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr>'
        props += "</w:pPr>"
    run_props = "<w:rPr><w:b/></w:rPr>" if bold else ""
    note = (
        f'<w:r><w:rPr><w:vertAlign w:val="superscript"/></w:rPr><w:footnoteReference w:id="{footnote}"/></w:r>'
        if footnote
        else ""
    )
    return f'<w:p>{props}<w:r>{run_props}<w:t xml:space="preserve">{text}</w:t></w:r>{note}</w:p>'


def docx_book(path: Path) -> Path:
    body = "".join(
        [
            _p("A Made-up Word Study", "Title"),
            _p("Lesson 1: Grace", "LessonTitle"),
            _p("Placeholder prose about grace with a reference to Ephesians 2:8-9.", footnote="2"),
            _p("A quoted line in the house quote style.", "Quote"),
            _p("First numbered point", numbered=True),
            _p("Second numbered point", numbered=True),
            _p("Questions for Reflection", "Heading2"),
            _p("What does grace mean to you?", numbered=True),
            _p("Lesson 2: Faith", "LessonTitle"),
            _p("Placeholder prose about faith in Hebrews 11:1.", footnote="3"),
            "<w:tbl><w:tr><w:tc>" + _p("Week") + "</w:tc><w:tc>" + _p("Passage") + "</w:tc></w:tr>"
            "<w:tr><w:tc>" + _p("1") + "</w:tc><w:tc>" + _p("Psalm 23") + "</w:tc></w:tr></w:tbl>",
        ]
    )
    document = f'<?xml version="1.0"?><w:document {_W}><w:body>{body}</w:body></w:document>'
    styles = (
        f'<?xml version="1.0"?><w:styles {_W}>'
        '<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/></w:style>'
        '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/>'
        '<w:pPr><w:outlineLvl w:val="0"/></w:pPr></w:style>'
        '<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/>'
        '<w:pPr><w:outlineLvl w:val="1"/></w:pPr></w:style>'
        '<w:style w:type="paragraph" w:styleId="LessonTitle"><w:name w:val="Lesson Title"/>'
        '<w:basedOn w:val="Heading1"/></w:style>'
        '<w:style w:type="paragraph" w:styleId="Quote"><w:name w:val="Quote"/></w:style>'
        "</w:styles>"
    )
    numbering = (
        f'<?xml version="1.0"?><w:numbering {_W}>'
        '<w:abstractNum w:abstractNumId="0"><w:lvl w:ilvl="0"><w:numFmt w:val="decimal"/></w:lvl></w:abstractNum>'
        '<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num></w:numbering>'
    )
    footnotes = (
        f'<?xml version="1.0"?><w:footnotes {_W}>'
        '<w:footnote w:type="separator" w:id="-1"><w:p><w:r><w:t>-</w:t></w:r></w:p></w:footnote>'
        '<w:footnote w:id="2"><w:p><w:r><w:t>A note on grace.</w:t></w:r></w:p></w:footnote>'
        '<w:footnote w:id="3"><w:p><w:r><w:t>A note on faith.</w:t></w:r></w:p></w:footnote>'
        "</w:footnotes>"
    )
    path.write_bytes(
        _zip(
            {
                "[Content_Types].xml": "<Types/>",
                "word/document.xml": document,
                "word/styles.xml": styles,
                "word/numbering.xml": numbering,
                "word/footnotes.xml": footnotes,
            }
        )
    )
    return path

import io
import zipfile

import pytest

from services.common.epub_text import (
    Chapter,
    EpubTextError,
    read_chapters,
    read_text,
)

CONTAINER = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""


def _opf(items: list[tuple], spine: list[str]) -> str:
    rows = []
    for row in items:
        ident, href, props = row[0], row[1], row[2]
        media = row[3] if len(row) > 3 else "application/xhtml+xml"
        rows.append(f'<item id="{ident}" href="{href}" media-type="{media}"{props}/>')
    manifest = "".join(rows)
    itemrefs = "".join(f'<itemref idref="{idref}"/>' for idref in spine)
    return (
        '<?xml version="1.0"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        '<dc:title>Test Book</dc:title>'
        '</metadata>'
        "<manifest>" + manifest + "</manifest>"
        "<spine>" + itemrefs + "</spine>"
        "</package>"
    )


def build_epub(
    documents: dict[str, str],
    items: list[tuple[str, str, str]],
    spine: list[str],
    opf_path: str = "OEBPS/content.opf",
) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml", CONTAINER)
        archive.writestr(opf_path, _opf(items, spine))
        for name, markup in documents.items():
            archive.writestr(name, markup)
    return buffer.getvalue()


def doc(body: str) -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>ignored</title></head>'
        f"<body>{body}</body></html>"
    )


def simple_book() -> bytes:
    return build_epub(
        {
            "OEBPS/ch1.xhtml": doc("<h1>Chapter One</h1><p>Alpha text.</p><p>Beta text.</p>"),
            "OEBPS/ch2.xhtml": doc("<h1>Chapter Two</h1><p>Gamma text.</p>"),
        },
        [("c1", "ch1.xhtml", ""), ("c2", "ch2.xhtml", "")],
        ["c1", "c2"],
    )


def test_reads_spine_in_reading_order_not_name_order():
    data = build_epub(
        {
            "OEBPS/zzz.xhtml": doc("<h1>Last Alphabetically</h1><p>Third.</p>"),
            "OEBPS/aaa.xhtml": doc("<h1>First Alphabetically</h1><p>First.</p>"),
            "OEBPS/mmm.xhtml": doc("<h1>Middle Alphabetically</h1><p>Second.</p>"),
        },
        [("z", "zzz.xhtml", ""), ("a", "aaa.xhtml", ""), ("m", "mmm.xhtml", "")],
        ["a", "m", "z"],
    )
    chapters = read_chapters(data)
    assert [c.label for c in chapters] == [
        "First Alphabetically",
        "Middle Alphabetically",
        "Last Alphabetically",
    ]
    assert [c.ordinal for c in chapters] == [1, 2, 3]


def test_paragraphs_become_blank_line_separated_blocks():
    chapters = read_chapters(simple_book())
    assert chapters[0].label == "Chapter One"
    assert chapters[0].text == "Alpha text.\n\nBeta text."


def test_script_and_style_text_is_dropped():
    data = build_epub(
        {"OEBPS/c.xhtml": doc("<h1>C</h1><style>p{color:red}</style><p>Kept.</p><script>alert(1)</script>")},
        [("c", "c.xhtml", "")],
        ["c"],
    )
    text = read_chapters(data)[0].text
    assert text == "Kept."
    assert "color:red" not in text
    assert "alert" not in text


def test_nav_document_is_excluded_when_real_spine_exists():
    data = build_epub(
        {
            "OEBPS/nav.xhtml": doc("<h1>Contents</h1><p>Chapter One</p>"),
            "OEBPS/c1.xhtml": doc("<h1>Chapter One</h1><p>Alpha.</p>"),
        },
        [("nav", "nav.xhtml", ' properties="nav"'), ("c1", "c1.xhtml", "")],
        ["nav", "c1"],
    )
    labels = [c.label for c in read_chapters(data)]
    assert labels == ["Chapter One"]


def test_nav_document_kept_when_it_is_all_there_is():
    data = build_epub(
        {"OEBPS/nav.xhtml": doc("<h1>Contents</h1><p>Only page.</p>")},
        [("nav", "nav.xhtml", ' properties="nav"')],
        ["nav"],
    )
    chapters = read_chapters(data)
    assert [c.label for c in chapters] == ["Contents"]
    assert chapters[0].text == "Only page."


def test_label_falls_back_to_manifest_id_when_no_heading():
    data = build_epub(
        {"OEBPS/frontmatter.xhtml": doc("<p>No heading here.</p>")},
        [("fm", "frontmatter.xhtml", "")],
        ["fm"],
    )
    chapter = read_chapters(data)[0]
    assert chapter.label == "fm"
    assert chapter.text == "No heading here."


def test_label_falls_back_to_member_name_when_manifest_id_is_absent():
    data = build_epub(
        {"OEBPS/only.xhtml": doc("<p>Body.</p>")},
        [],
        [],
    )
    chapters = read_chapters(data)
    assert chapters[0].label == "OEBPS/only.xhtml"
    assert chapters[0].text == "Body."


def test_ordinals_number_the_returned_sections_contiguously():
    data = build_epub(
        {
            "OEBPS/c1.xhtml": doc("<p>Real content.</p>"),
            "OEBPS/c2.xhtml": doc("<div><span>   </span></div>"),
            "OEBPS/c3.xhtml": doc("<p>More content.</p>"),
        },
        [("c1", "c1.xhtml", ""), ("c2", "c2.xhtml", ""), ("c3", "c3.xhtml", "")],
        ["c1", "c2", "c3"],
    )
    chapters = read_chapters(data)
    assert [c.text for c in chapters] == ["Real content.", "More content."]
    assert [c.ordinal for c in chapters] == [1, 2]


def test_ordinals_are_contiguous_when_one_spine_document_yields_many_sections():
    data = build_epub(
        {
            "OEBPS/c1.xhtml": doc(
                "<h1>Book</h1>"
                "<p>Title page matter that belongs to the book, not to a section.</p>"
                "<h2>First</h2><p>Alpha body text long enough to be a section.</p>"
                "<h2>Second</h2><p>Beta body text long enough to be a section.</p>"
                "<h2>Third</h2><p>Gamma body text long enough to be a section.</p>"
            ),
        },
        [("c1", "c1.xhtml", "")],
        ["c1"],
    )
    chapters = read_chapters(data)
    assert [c.ordinal for c in chapters] == [1, 2, 3, 4]
    assert [c.label for c in chapters] == ["Book", "First", "Second", "Third"]


def test_a_heading_with_no_prose_under_it_does_not_become_a_section():
    """A title immediately followed by the first section heading names nothing.

    Emitting it would add a chapter whose entire content is its own label, which
    retrieves as a duplicate of the book title.
    """
    data = build_epub(
        {
            "OEBPS/c1.xhtml": doc(
                "<h1>Book</h1>"
                "<h2>First</h2><p>Alpha body text long enough to be a section.</p>"
                "<h2>Second</h2><p>Beta body text long enough to be a section.</p>"
            ),
        },
        [("c1", "c1.xhtml", "")],
        ["c1"],
    )
    chapters = read_chapters(data)
    assert [c.label for c in chapters] == ["First", "Second"]
    assert [c.ordinal for c in chapters] == [1, 2]


def test_a_lone_heading_names_a_document_that_has_no_structure_to_split_on():
    data = build_epub(
        {
            "OEBPS/c1.xhtml": doc(
                "<h1>Whole Book</h1><p>The only body text in the document.</p>"
            ),
        },
        [("c1", "c1.xhtml", "")],
        ["c1"],
    )
    chapters = read_chapters(data)
    assert [c.label for c in chapters] == ["Whole Book"]


def test_opf_href_outside_opf_directory_is_resolved_relative_to_it():
    data = build_epub(
        {
            "OEBPS/text/c1.xhtml": doc("<h1>Nested</h1><p>Text.</p>"),
        },
        [("c1", "text/c1.xhtml", "")],
        ["c1"],
    )
    chapters = read_chapters(data)
    assert chapters[0].label == "Nested"
    assert chapters[0].text == "Text."


def test_member_lookup_tolerates_case_differences():
    data = build_epub(
        {"OEBPS/Chapter1.XHTML": doc("<h1>Upper</h1><p>Text.</p>")},
        [("c1", "chapter1.xhtml", "")],
        ["c1"],
    )
    assert read_chapters(data)[0].label == "Upper"


def test_non_html_manifest_items_are_ignored():
    data = build_epub(
        {
            "OEBPS/c1.xhtml": doc("<h1>Real</h1><p>Text.</p>"),
            "OEBPS/cover.jpg": b"\xff\xd8\xff",
        },
        [("img", "cover.jpg", "", "image/jpeg"), ("c1", "c1.xhtml", "")],
        ["img", "c1"],
    )
    assert [c.label for c in read_chapters(data)] == ["Real"]


def test_manifest_item_with_no_media_type_is_judged_on_its_suffix():
    """A malformed package that omits media-type must not admit cover art.

    Trusting the absence of a type would let a jpeg through the manifest and then
    decode its bytes as prose, which is how binary noise reaches the index.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "book.opf",
            '<?xml version="1.0"?>'
            '<package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
            "<manifest>"
            '<item id="img" href="cover.jpg"/>'
            '<item id="c1" href="c1.xhtml"/>'
            "</manifest>"
            "<spine>"
            '<itemref idref="img"/>'
            '<itemref idref="c1"/>'
            "</spine>"
            "</package>",
        )
        archive.writestr("cover.jpg", b"\xff\xd8\xff\xff")
        archive.writestr("c1.xhtml", doc("<h1>Real</h1><p>Text.</p>"))
    assert [c.label for c in read_chapters(buffer.getvalue())] == ["Real"]


def test_opf_is_found_without_container_xml():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("book.opf", _opf([("c1", "c1.xhtml", "")], ["c1"]))
        archive.writestr("c1.xhtml", doc("<h1>No Container</h1><p>Text.</p>"))
    chapters = read_chapters(buffer.getvalue())
    assert chapters[0].label == "No Container"


def test_rejects_non_zip_bytes():
    with pytest.raises(EpubTextError, match="not a readable epub"):
        read_chapters(b"this is not a zip file")


def test_rejects_zip_without_any_package_document():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("loose.txt", "hello")
    with pytest.raises(EpubTextError, match="no OPF package document"):
        read_chapters(buffer.getvalue())


def test_rejects_corrupt_package_document():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("book.opf", "<package><manifest>")
        archive.writestr("c1.xhtml", doc("<p>Text.</p>"))
    with pytest.raises(EpubTextError, match="unreadable"):
        read_chapters(buffer.getvalue())


def test_spine_referencing_a_missing_document_raises():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("book.opf", _opf([("c1", "c1.xhtml", "")], ["c1"]))
    with pytest.raises(EpubTextError, match="missing from the archive"):
        read_chapters(buffer.getvalue())


def test_entities_are_decoded():
    data = build_epub(
        {"OEBPS/c.xhtml": doc("<p>Faith &amp; works &mdash; justified.</p>")},
        [("c", "c.xhtml", "")],
        ["c"],
    )
    assert read_chapters(data)[0].text == "Faith & works — justified."


def test_inline_markup_does_not_split_words():
    data = build_epub(
        {"OEBPS/c.xhtml": doc("<p>un<em>bel</em>ievable</p>")},
        [("c", "c.xhtml", "")],
        ["c"],
    )
    assert read_chapters(data)[0].text == "unbelievable"


def test_read_text_labels_each_chapter():
    text = read_text(simple_book())
    assert text == (
        "Chapter One\n\nAlpha text.\n\nBeta text."
        "\n\n"
        "Chapter Two\n\nGamma text."
    )


def test_chapter_is_frozen():
    chapter = Chapter(ordinal=1, label="L", text="T")
    with pytest.raises(Exception):
        chapter.label = "other"


def test_br_inside_a_paragraph_forces_a_break():
    data = build_epub(
        {"OEBPS/c.xhtml": doc("<p>Line one<br/>Line two</p>")},
        [("c", "c.xhtml", "")],
        ["c"],
    )
    assert read_chapters(data)[0].text == "Line one\n\nLine two"


def test_list_items_become_separate_blocks():
    data = build_epub(
        {"OEBPS/c.xhtml": doc("<ul><li>One</li><li>Two</li></ul>")},
        [("c", "c.xhtml", "")],
        ["c"],
    )
    assert read_chapters(data)[0].text == "One\n\nTwo"


def test_archive_with_no_readable_prose_is_refused_rather_than_returned_empty():
    """A book with no words must be visible, not quietly indexed as having none.

    Returning an empty list would let a broken archive pass as a legitimately
    blank book and disappear from the index with nothing recorded, so the caller
    gets an error it can report and skip.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("book.opf", _opf([("c", "c.xhtml", "")], ["c"]))
        archive.writestr("c.xhtml", doc("<head><title>only a title</title></head><body></body>"))
    with pytest.raises(EpubTextError, match="no readable prose"):
        read_chapters(buffer.getvalue())


def test_a_section_label_is_never_left_empty():
    data = build_epub(
        {"OEBPS/c.xhtml": doc("<p>Body text with no heading at all in it.</p>")},
        [("c", "c.xhtml", "")],
        ["c"],
    )
    assert read_chapters(data)[0].label == "c"
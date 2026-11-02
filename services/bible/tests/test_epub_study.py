"""Study apparatus beyond linked commentary: placed by id or by structure."""
from __future__ import annotations

from services.bible import epub_study

LONG = "This paragraph is long enough to read as prose rather than as a label or a link, by design. "


def _notes(documents: dict[str, str], *, structured: bool = False) -> set[tuple[int, int, int, str, str]]:
    extras = epub_study.harvest(list(documents.items()), structured=structured)
    return set(extras.notes)


def test_footnotes_cross_references_and_articles_are_placed_by_their_ids():
    notes = _notes({
        # NIV Zondervan: the letter is its own element, the verse is in the id.
        "niv.html": (
            '<p class="en" id="x01001001a"><span class="ennum"><a href="t.html#rx">a</a></span>'
            '<a href="t.html#v19102025">Ps 102:25</a>; Pr 8:23</p>'
            '<p class="fn" id="fn19003000a"><span class="fnnum">1</span>Or <em>a psalm title</em></p>'
            '<p class="fn" id="fn01001002a"><span class="fnnum">1</span> Or <em>empty</em></p>'
        ),
        # ESV: the id sits on a link inside the paragraph.
        "esv.html": (
            '<p class="note"><a id="f43001004.1" href="t.html#b">[1]</a> '
            '<span class="footnote-ref">1:4</span> Or <em>was not any thing made</em></p>'
            '<p class="crossref"><span><a href="t.html#v43001001">1:1</a></span> '
            '<span><a id="c43001001.a" href="t.html#v43001001">a</a></span> Gen. 1:1; 1 John 1:1</p>'
        ),
        # NKJV Apply the Word: a sidebar is an article; its navigation is not.
        "nkjv.html": (
            '<div class="sidebar" id="sb-01001001-or0003"><p class="sbh">God the Creator</p>'
            f'<p class="sb">{LONG}</p><p class="sbo"><a href="i.html">Return to Index of Articles</a></p></div>'
            '<div class="sidebar" id="sbmap01012009-0048"><p class="sbh">Abrams Travels</p><p><img src="m.jpg"/></p></div>'
            f'<div class="book" id="bk01"><p class="bk">Genesis</p><p>{LONG}</p></div>'
        ),
    })
    assert (1, 1, 1, "crossref", "Ps 102:25; Pr 8:23") in notes
    assert (1, 1, 2, "footnote", "Or empty") in notes
    assert (43, 1, 4, "footnote", "Or was not any thing made") in notes
    assert (43, 1, 1, "crossref", "Gen. 1:1; 1 John 1:1") in notes
    assert (1, 1, 1, "article", f"God the Creator\n\n{LONG.strip()}") in notes
    assert (1, 1, 0, "introduction", f"Genesis\n\n{LONG.strip()}") in notes
    # A psalm title's note belongs to the chapter; a map has nothing to read.
    assert (19, 3, 0, "footnote", "Or a psalm title") in notes
    assert not any(kind == "article" and body.startswith("Abram") for *_, kind, body in notes)


def test_a_generated_layout_is_read_by_its_structure():
    documents = {
        "intro.xhtml": f"<body><p>John</p><p>Who is the author? {LONG}</p><p>When was it written? {LONG}</p></body>",
        "john1.xhtml": (
            '<body><p class="k"><a href="nav.xhtml">John</a> <a href="nav.xhtml">1</a> '
            '<a href="notes.xhtml#n1" epub:type="noteref">*</a></p>'
            f'<div class="box"><p class="l">listen to the Word</p><p class="r">Listen to John 1. {LONG}</p></div>'
            '<p><span class="v">1</span>In the beginning the Word already existed.</p>'
            '<div class="m"><p><a href="article.xhtml">Jesus is God</a></p></div>'
            '<p><span class="v">2</span>He existed in the beginning with God.</p></body>'
        ),
        "article.xhtml": f"<body><p>Jesus is God 1:1-18</p><p>PRAY {LONG}</p><p>OBSERVE {LONG}</p></body>",
        "profile.xhtml": (
            f"<body><p>Frederick Douglass</p><p>{LONG}</p><p>{LONG}</p>"
            '<p><a href="john1.xhtml#a9">Return to scripture text</a></p></body>'
        ),
        "notes.xhtml": (
            '<body><p>John 1:1-51 NLT Text Notes</p>'
            '<aside epub:type="endnote">*<a href="john1.xhtml#a1">John 1:4</a> Or <span>and nothing</span></aside>'
            '<aside epub:type="endnote">*<a href="john1.xhtml#a2">Jn 1:5b</a> Or understood.</aside>'
            '<aside epub:type="endnote">*<a href="john1.xhtml#a3">Jhn 1:9</a> Or the true light.</aside></body>'
        ),
    }
    notes = _notes(documents, structured=True)
    assert (43, 1, 0, "introduction", f"John\n\nWho is the author? {LONG.strip()}\n\nWhen was it written? {LONG.strip()}") in notes
    assert (43, 1, 0, "commentary", f"listen to the Word\n\nListen to John 1. {LONG.strip()}") in notes
    assert (43, 1, 1, "article", f"Jesus is God 1:1-18\n\nPRAY {LONG.strip()}\n\nOBSERVE {LONG.strip()}") in notes
    # A profile that names no passage is filed on the chapter it returns to.
    assert (43, 1, 0, "article", f"Frederick Douglass\n\n{LONG.strip()}\n\n{LONG.strip()}") in notes
    assert (43, 1, 4, "footnote", "Or and nothing") in notes
    assert (43, 1, 5, "footnote", "Or understood.") in notes
    # An abbreviation the index does not know is placed by the chapter it links to.
    assert (43, 1, 9, "footnote", "Or the true light.") in notes
    # The page of notes is not an article, and nothing is placed twice.
    assert not any(body.startswith("John 1:1-51") for *_, body in notes)
    assert len(notes) == len(epub_study.harvest(list(documents.items()), structured=True).notes)

"""A retrieved chunk must reach the model with enough detail to be cited.

Retrieval returns the stored metadata, but the context renderer used to emit
only the chunk text for every collection except Raven's own lessons. So a
chapter of a book was searchable through the API and arrived in the prompt as
anonymous prose: the model could echo it but had no way to say where it came
from. These tests pin the locator rendering that fixes that.
"""
from services.gateway.orchestrator import _as_meta_dict, _citation_suffix


def test_chapter_locator_is_rendered():
    suffix = _citation_suffix({"title": "Moby Dick", "chapter": 7, "path": "/Books/moby.epub"})
    assert "Moby Dick" in suffix
    assert "chapter 7" in suffix


def test_page_locator_is_rendered():
    assert "page 218" in _citation_suffix({"title": "Anna Karenina", "page": 218})


def test_document_without_chapter_falls_back_to_chunk_index():
    suffix = _citation_suffix({"name": "notes.md", "chunk_index": 3})
    assert "notes.md" in suffix
    assert "chunk 3" in suffix


def test_metadata_skeleton_row_gets_no_chunk_index_noise():
    """Skeleton rows carry chunk_index 0 and are not real quotations."""
    assert _citation_suffix({"name": "a.md", "chunk_index": 0, "is_metadata": True}) == ""


def test_absent_metadata_yields_no_suffix():
    assert _citation_suffix({}) == ""
    assert _citation_suffix(None) == ""


def test_skeleton_content_marker_is_not_treated_as_a_title():
    suffix = _citation_suffix({"name": "File/Folder: b.md", "chunk_index": 1})
    assert "File/Folder" not in suffix


def test_stringified_chapter_number_is_restored():
    """``/rag/sync/files`` flattens non-scalar metadata through ``str()``."""
    meta = _as_meta_dict({"title": "Emma", "chapter": "12", "page": "181"})
    assert meta["chapter"] == 12
    assert meta["page"] == 181
    assert "chapter 12" in _citation_suffix(meta)


def test_real_prose_metadata_is_not_mangled():
    assert _citation_suffix({"note": '"quoted" and then some'}) == ""


def test_nested_metadata_dict_is_parsed_back():
    meta = _as_meta_dict('{"title": "Persuasion", "chapter": 16}')
    assert meta["title"] == "Persuasion"


def test_non_dict_metadata_degrades_to_empty():
    assert _as_meta_dict("just some text") == {}
    assert _as_meta_dict(None) == {}


def test_locator_is_bounded_even_with_a_long_path():
    """The locator is charged against the prompt budget, so it stays compact."""
    suffix = _citation_suffix({"title": "T", "chunk_index": 1, "path": "/x" * 500})
    assert len(suffix) < 200

def test_a_citation_names_the_author_so_a_question_about_one_can_be_answered():
    """A passage labelled only by title cannot answer a question about an author.

    Observed live: "What did Macduff say about trusting God in poverty?"
    retrieved passages from *Memories of Bethany*, which is by John R. Macduff,
    but the citation read "Source: Memories of Bethany, XIV." The model had no
    way to connect them and reported that nothing matched.
    """
    assert _citation_suffix({"title": "Memories of Bethany", "author": "John R. Macduff", "chapter": "XIV."}) == \
        " (Source: Memories of Bethany by John R. Macduff, chapter XIV.)"


def test_an_author_without_a_title_still_attributes():
    assert _citation_suffix({"author": "John R. Macduff", "chapter": "XIV."}) == \
        " (Source: by John R. Macduff, chapter XIV.)"


def test_a_blank_author_adds_nothing_rather_than_a_stray_by():
    assert _citation_suffix({"title": "A Book", "author": "   ", "chapter": "1"}) == \
        " (Source: A Book, chapter 1)"

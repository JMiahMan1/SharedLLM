"""Formats we cannot actually read must not be handed to the text chunker.

A `.pdf` or `.docx` is a compressed container, not text. Both were classified
``full_text``, which sends them down the only path that produces content:
``provider.get_content`` reads the response as text. For these formats that is
a byte stream decoded as characters — mojibake — and it gets chunked, embedded,
and stored like a real passage. Every search hit against it is then confidently
wrong, and there is nothing downstream that can tell a real chunk from noise.

The honest state until an extractor exists is metadata-only, which is what
these tests pin. An absent extractor must be a visible restriction, never a
silently wrong answer.
"""
from services.storage.indexer import FILE_RULES, chunk_text
from services.storage.models import StorageEntry
from services.storage.indexer import build_content_index


def entry(path, content_type="application/octet-stream"):
    return StorageEntry(
        path=path,
        name=path.rsplit("/", 1)[-1],
        is_dir=False,
        size=1024,
        mtime="Mon, 01 Jan 2026 00:00:00 GMT",
        content_type=content_type,
    )


def caps(path, content_type="application/octet-stream"):
    items = build_content_index([entry(path, content_type)])
    return items[0].extractable_capabilities, items[0].restrictions


def test_pdf_is_not_offered_to_the_text_chunker():
    assert "full_text" not in FILE_RULES[".pdf"]["capabilities"]


def test_docx_is_not_offered_to_the_text_chunker():
    assert "full_text" not in FILE_RULES[".docx"]["capabilities"]


def test_pdf_surfaces_why_it_has_no_text():
    _, restrictions = caps("/docs/report.pdf", "application/pdf")
    assert "no_text_extractor" in restrictions


def test_docx_surfaces_why_it_has_no_text():
    _, restrictions = caps("/docs/memo.docx")
    assert "no_text_extractor" in restrictions


def test_missing_extractor_does_not_claim_a_tool_that_does_not_exist():
    """The rules advertised a ``pdf_parser`` that was never implemented."""
    for ext in (".pdf", ".docx"):
        assert FILE_RULES[ext]["tools"] == []


def test_plain_text_formats_still_index_fully():
    for ext in (".txt", ".md", ".csv"):
        assert "full_text" in FILE_RULES[ext]["capabilities"]


def test_text_documents_are_unaffected_end_to_end():
    items = build_content_index([entry("/notes/todo.md", "text/markdown")])
    assert items[0].extractable_capabilities == list(FILE_RULES[".md"]["capabilities"])


def test_chunking_mojibake_is_exactly_the_failure_being_prevented():
    """Documents why a byte stream must not reach ``chunk_text``.

    Not an assertion about desired behaviour — a characterisation of the
    corruption that classifying these formats as ``full_text`` produced.
    """
    pdf_bytes = b"%PDF-1.4\n\x00\x01\x02\xff\xfe garbage"
    decoded = pdf_bytes.decode("utf-8", errors="replace")
    assert chunk_text(decoded), "a byte stream always yields chunks"
    assert "�" in decoded, "and they contain replacement characters"


def test_unknown_binary_is_still_metadata_only():
    capabilities, restrictions = caps("/media/clip.mkv", "video/x-matroska")
    assert "full_text" not in capabilities
    assert "binary_only" in restrictions
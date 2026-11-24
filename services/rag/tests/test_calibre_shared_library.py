"""The family's Calibre library is one shelf, searchable by everyone.

Storage files it under ``default`` (``storage.main.SHARED_LIBRARY_OWNER``), which
every search already includes. ``/rag/owners`` lets the crawl find the copies an
older crawl filed under one person's account, and each hit says whether it was kept
as a keyword phrase match, so a caller re-checking the similarity floor can tell
such a hit from one under the floor.
"""
import asyncio

from services.rag import main as rag_main
from services.rag.schemas import IngestRequest, SearchRequest
from services.rag.store import NumpyVecAdapter
from services.rag.tests.test_rag_search import _install


def _put(user, content, collection="calibre_files", **meta):
    return asyncio.run(rag_main.ingest(IngestRequest(
        user_id=user, content=content, collection_name=collection, metadata=meta,
    )))


def _search(query, user, **kw):
    return asyncio.run(rag_main.search(SearchRequest(
        query=query, user_id=user, k=5, collection_name="calibre_files", **kw,
    ))).results


def test_a_book_filed_under_default_is_found_by_every_household_member(tmp_path):
    _install(tmp_path, NumpyVecAdapter)
    _put("default", "Wild at Heart: the masculine heart and adventure", title="Wild at Heart",
         author="John Eldredge")
    for person in ("rivera", "admin", "sam"):
        hits = _search("masculine heart adventure", person)
        assert [h.metadata["title"] for h in hits] == ["Wild at Heart"], person


def test_owners_lists_who_holds_rows_in_a_collection(tmp_path):
    _install(tmp_path, NumpyVecAdapter)
    _put("default", "a passage", title="A")
    _put("default", "another passage", title="A")
    _put("admin", "Memories of Bethany passage", title="Memories of Bethany")
    _put("rivera", "a note", collection="nextcloud_files")

    owners = asyncio.run(rag_main.get_collection_owners("calibre_files"))["owners"]
    assert owners == {"default": 2, "admin": 1}


def test_each_hit_says_whether_it_was_kept_as_a_phrase_match(tmp_path, monkeypatch):
    _install(tmp_path, NumpyVecAdapter)
    _put("default", "garage door opener garage", title="G")
    _put("default", "porch light lamp", title="P")
    garage = next(r["id"] for r in rag_main._conn().execute(
        "SELECT id FROM rag_items WHERE content LIKE 'garage%'").fetchall())
    monkeypatch.setattr(rag_main, "_bm25_search", lambda *a, **k: [(garage, -1.0)])

    hits = {h.content: h for h in _search("garage door", "default")}
    assert hits["garage door opener garage"].phrase_match is True
    assert hits["porch light lamp"].phrase_match is False


def test_stats_count_the_shared_library_for_every_user(tmp_path):
    _install(tmp_path, NumpyVecAdapter)
    _put("default", "a passage", title="A", path="/Books/A (1).txt")
    stats = asyncio.run(rag_main.get_stats(user_id="rivera"))
    assert stats["breakdown"]["calibre_files"]["chunks"] == 1

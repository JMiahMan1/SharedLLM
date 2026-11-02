"""Topics: books tagged in Calibre, and files kept under Resources/<Topic>/."""
import asyncio

from services.rag import main as rag_main
from services.rag import topics
from services.rag.schemas import IngestRequest, SearchRequest
from services.rag.store import NumpyVecAdapter
from services.rag.tests.test_rag_search import _install


def test_a_topic_comes_from_a_tag_or_a_resources_folder():
    assert topics.item_topics({"tags": "Lent; jarvis:Fasting; Orthodox"}) == {"lent", "fasting", "orthodox"}
    assert topics.item_topics({"tags": ["Prayer"]}) == {"prayer"}
    assert topics.item_topics({"path": "/Family/Resources/Fasting/schmemann.pdf"}) == {"fasting"}
    assert topics.matches({"tags": "fasting"}, ["jarvis:fasting"])
    assert not topics.matches({"tags": "cooking"}, ["fasting"])
    assert topics.matches({}, None)


def _add(user, content, collection, meta):
    return asyncio.run(rag_main.ingest(IngestRequest(user_id=user, content=content, collection_name=collection, metadata=meta)))


def test_search_and_listing_keep_to_the_topic(tmp_path):
    _install(tmp_path, NumpyVecAdapter)
    _add("dad", "Great Lent by Schmemann: fasting and prayer before Pascha", "calibre_files",
         {"title": "Great Lent", "author": "Alexander Schmemann", "calibre_id": 7, "tags": "Lent; fasting", "is_metadata": True, "path": "Schmemann/Great Lent (7).txt"})
    _add("dad", "A cookbook about fasting from sugar for a diet", "calibre_files",
         {"title": "Sugar Fast Diet", "author": "Someone", "calibre_id": 8, "tags": "diet", "is_metadata": True, "path": "x/Sugar (8).txt"})
    _add("mom", "Her private notes on fasting", "nextcloud_files",
         {"name": "notes.md", "path": "/Resources/Fasting/notes.md", "is_metadata": True})

    hits = asyncio.run(rag_main.search(SearchRequest(query="fasting", user_id="dad", k=5, collection_name="calibre_files", tags=["fasting"]))).results
    assert [h.metadata["title"] for h in hits] == ["Great Lent"]

    listing = asyncio.run(rag_main.list_tagged_resources(tag="jarvis:fasting", user_id="jeremiah", limit=50))
    # The household library is listed for anyone; another person's files are not.
    assert [b["title"] for b in listing["books"]] == ["Great Lent"]
    assert listing["documents"] == []
    mine = asyncio.run(rag_main.list_tagged_resources(tag="fasting", user_id="mom", limit=50))
    assert [d["name"] for d in mine["documents"]] == ["notes.md"]


def test_a_tag_change_in_calibre_reaches_the_books_rows(tmp_path):
    _install(tmp_path, NumpyVecAdapter)
    _add("dad", "Great Lent chapter one", "calibre_files", {"title": "Great Lent", "calibre_id": 7, "path": "a.txt", "is_metadata": True})
    _add("dad", "Another book", "calibre_files", {"title": "Other", "calibre_id": 8, "path": "b.txt", "is_metadata": True})
    out = asyncio.run(rag_main.set_calibre_book_tags({"calibre_id": 7, "tags": "Lent; fasting"}))
    assert out["rows"] == 1
    listing = asyncio.run(rag_main.list_tagged_resources(tag="fasting", user_id="x", limit=50))
    assert [b["title"] for b in listing["books"]] == ["Great Lent"]

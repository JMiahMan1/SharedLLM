"""The librarian's routes: browse by tag, audit, and tag changes that reach the index."""
from fastapi.testclient import TestClient

from services.storage import library_api
from services.storage.main import app

client = TestClient(app)
H = {"X-Internal-Secret": "test-secret"}
LIB = {"library_path": "/Books/Text"}


def _book(i, title, author, tags=(), formats=("EPUB",), series=""):
    return {"id": i, "title": title, "sort": title, "authors": [author], "tags": list(tags), "series": series,
            "series_index": None, "formats": list(formats), "path": f"{author}/{title} ({i})"}


CATALOG = {
    1: _book(1, "Great Lent", "Alexander Schmemann", ["Lent", "fasting"]),
    2: _book(2, "Great Lent", "Alexander Schmemann", [], ["PDF"]),
    3: _book(3, "The Lenten Spring (Annotated)", "Thomas Hopko", ["jarvis:fasting"]),
    4: _book(4, "The Lenten Spring", "Thomas Hopko"),
    5: _book(5, "Works of John Wesley - Vol. 02", "John Wesley", formats=()),
    6: _book(6, "Works of John Wesley - Vol. 08", "John Wesley", ["Wesley"]),
    10: _book(10, "Henry of Guise; (Vol. I of 3)", "G. P. R. James"),
    11: _book(11, "Henry of Guise; (Vol. II of 3)", "G. P. R. James"),
}


def _fake(monkeypatch):
    async def catalog(path):
        return CATALOG

    monkeypatch.setattr(library_api, "_catalog", catalog)


def test_books_are_found_by_topic_words_and_untagged(monkeypatch):
    _fake(monkeypatch)
    by_tag = client.post("/calibre/books", json={**LIB, "tag": "fasting"}, headers=H).json()
    assert {b["id"] for b in by_tag["books"]} == {1, 3}
    words = client.post("/calibre/books", json={**LIB, "q": "lent schmemann"}, headers=H).json()
    assert {b["id"] for b in words["books"]} == {1, 2}
    untagged = client.post("/calibre/books", json={**LIB, "untagged": True}, headers=H).json()
    assert {b["id"] for b in untagged["books"]} == {2, 4, 5, 10, 11}
    assert client.post("/calibre/books", json=LIB).status_code in (401, 403)


def test_tags_are_counted(monkeypatch):
    _fake(monkeypatch)
    tags = client.post("/calibre/tags", json=LIB, headers=H).json()["tags"]
    assert {"name": "fasting", "books": 1} in tags


def test_the_audit_names_copies_editions_and_gaps_but_never_two_volumes():
    report = library_api.audit(list(CATALOG.values()))
    assert len(report["copies"]) == 1
    copy = report["copies"][0]
    # The readable, tagged copy is the one to keep.
    assert copy["keep"]["id"] == 1 and [b["id"] for b in copy["others"]] == [2]
    assert len(report["editions"]) == 1 and {report["editions"][0]["keep"]["id"], report["editions"][0]["others"][0]["id"]} == {3, 4}
    assert report["no_file"]["count"] == 1 and report["unreadable"]["count"] == 1
    # Numbered volumes are different books, in or out of parentheses.
    assert not any("Wesley" in g["keep"]["title"] or "Guise" in g["keep"]["title"] for g in report["copies"] + report["editions"])


def test_a_tag_change_is_written_and_sent_to_the_index(monkeypatch):
    patched = []

    class Writer:
        def __init__(self, client, path):
            pass

        async def set_tags(self, book_id, add, remove):
            return {"book_id": book_id, "tags": ["Lent", "fasting"], "backup": "/b", "attempts": 1}

    async def rag(book_id, tags):
        patched.append((book_id, tags))

    closed = []

    class Prov:
        client = object()

        async def close(self):
            closed.append(True)

    monkeypatch.setattr(library_api, "LibraryWriter", Writer)
    monkeypatch.setattr(library_api, "_provider", lambda path: Prov())
    monkeypatch.setattr(library_api, "_rag_tags", rag)
    body = client.post("/calibre/books/1/tags", json={**LIB, "add": ["fasting"]}, headers=H).json()
    assert closed == [True]  # the request's Nextcloud session is closed, not left to the GC
    assert body["tags"] == ["Lent", "fasting"] and patched == [(1, ["Lent", "fasting"])]
    assert client.post("/calibre/books/1/tags", json=LIB, headers=H).status_code == 422

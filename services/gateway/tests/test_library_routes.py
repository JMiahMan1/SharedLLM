"""The family library through the gateway: anyone signed in browses, only admins change it."""
from fastapi.testclient import TestClient

from services.gateway import main


def _setup(monkeypatch, ident):
    calls = []

    async def acting(request):
        return ident

    async def setting(key, default=""):
        return "/Books/Text" if key == "calibre_library_path" else default

    async def storage(method, path, *, json_body=None, data=None, timeout=60.0):
        calls.append((method, path, json_body))
        return {"status": "SUCCESS", "tags": ["fasting"], "books": [], "total": 0}

    monkeypatch.setattr(main, "_acting_identity", acting)
    monkeypatch.setattr(main, "fetch_global_setting", setting)
    monkeypatch.setattr(main, "_storage_library", storage)
    return calls


def test_anyone_signed_in_browses_and_audits(monkeypatch):
    calls = _setup(monkeypatch, {"user": "kid", "is_admin": False})
    client = TestClient(main.app)
    assert client.get("/api/library/books", params={"tag": "fasting"}).status_code == 200
    assert client.get("/api/library/audit").status_code == 200
    assert calls[0][2]["tag"] == "fasting" and calls[0][2]["library_path"] == "/Books/Text"


def test_only_an_admin_changes_tags_or_adds_books(monkeypatch):
    calls = _setup(monkeypatch, {"user": "kid", "is_admin": False})
    client = TestClient(main.app)
    assert client.post("/api/library/books/7/tags", json={"add": ["fasting"]}).status_code == 403
    assert client.post("/api/library/books", files={"file": ("a.epub", b"x")}).status_code == 403
    assert calls == []
    calls = _setup(monkeypatch, {"user": "dad", "is_admin": True})
    assert TestClient(main.app).post("/api/library/books/7/tags", json={"add": ["fasting"]}).status_code == 200
    assert calls[0][1] == "/calibre/books/7/tags" and calls[0][2]["add"] == ["fasting"]


def test_signed_out_is_refused(monkeypatch):
    _setup(monkeypatch, None)
    assert TestClient(main.app).get("/api/library/books").status_code == 401


def test_inspecting_a_book_is_for_admins(monkeypatch):
    calls = _setup(monkeypatch, {"user": "kid", "is_admin": False})
    assert TestClient(main.app).post("/api/library/books/inspect", files={"file": ("a.epub", b"x")}).status_code == 403
    calls = _setup(monkeypatch, {"user": "dad", "is_admin": True})
    assert TestClient(main.app).post("/api/library/books/inspect", files={"file": ("a.epub", b"x")}).status_code == 200
    assert calls[0][1] == "/calibre/books/inspect"


def test_filling_in_books_is_for_admins_and_a_dry_run_unless_said_otherwise(monkeypatch):
    calls = _setup(monkeypatch, {"user": "kid", "is_admin": False})
    assert TestClient(main.app).post("/api/library/enrich", json={}).status_code == 403
    assert calls == []
    calls = _setup(monkeypatch, {"user": "dad", "is_admin": True})
    client = TestClient(main.app)
    assert client.post("/api/library/enrich", json={}).status_code == 200
    assert calls[0][1] == "/calibre/books/enrich"
    assert calls[0][2]["dry_run"] is True and calls[0][2]["limit"] == 10
    assert client.post("/api/library/enrich", json={"dry_run": False, "limit": 500}).status_code == 200
    assert calls[1][2]["dry_run"] is False and calls[1][2]["limit"] == 200
    assert client.post("/api/library/enrich", json={"limit": "lots"}).status_code == 422


def _form_fields(monkeypatch, ident):
    """Like _setup, but keeps the form fields sent to storage (a multipart add or inspect)."""
    sent: list[dict] = []
    _setup(monkeypatch, ident)

    async def storage(method, path, *, json_body=None, data=None, timeout=60.0):
        fields = {opts.get("name"): value for opts, _headers, value in getattr(data, "_fields", [])}
        sent.append({"path": path, "json": json_body, "form": fields})
        return {"status": "SUCCESS"}

    monkeypatch.setattr(main, "_storage_library", storage)
    return sent


def test_the_book_catalogues_used_are_the_asking_persons_integrations(monkeypatch):
    """Storage resolves Google Books / Open Library for the person the gateway names."""
    sent = _form_fields(monkeypatch, {"user": "dad", "is_admin": True})
    client = TestClient(main.app)
    assert client.post("/api/library/books", files={"file": ("a.epub", b"x")}).status_code == 200
    assert client.post("/api/library/books/inspect", files={"file": ("a.epub", b"x")}).status_code == 200
    assert client.post("/api/library/enrich", json={}).status_code == 200
    assert [s["form"].get("user") for s in sent[:2]] == ["dad", "dad"]
    assert sent[2]["json"]["user"] == "dad"
    # A user field in the request cannot name someone else.
    assert client.post("/api/library/enrich", json={"user": "mom"}).status_code == 200
    assert sent[3]["json"]["user"] == "dad"


def test_anyone_signed_in_sees_which_catalogues_apply_to_them(monkeypatch):
    calls = _setup(monkeypatch, {"user": "kid", "is_admin": False})
    assert TestClient(main.app).get("/api/library/catalogues").status_code == 200
    assert calls == [("POST", "/calibre/catalogues", {"user": "kid"})]
    _setup(monkeypatch, None)
    assert TestClient(main.app).get("/api/library/catalogues").status_code == 401

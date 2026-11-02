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

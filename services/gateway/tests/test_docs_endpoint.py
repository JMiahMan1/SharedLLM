"""``/api/docs`` (list) and ``/api/docs/{name}`` back the Docs page and the
help tooltips. Any non-empty key used to be accepted; now the caller must be
a real signed-in user (or an internal service)."""
from fastapi.testclient import TestClient

from services.gateway import main as gateway_main
from services.gateway.main import app


def _as(monkeypatch, user):
    async def _uid(_request):
        return user

    monkeypatch.setattr(gateway_main, "_user_id_from_request", _uid)


def test_unverified_key_is_refused(monkeypatch):
    _as(monkeypatch, None)
    client = TestClient(app)
    assert client.get("/api/docs", headers={"Authorization": "Bearer junk"}).status_code == 401
    assert client.get("/api/docs/README.md", headers={"Authorization": "Bearer junk"}).status_code == 401


def test_lists_real_docs_with_titles(monkeypatch):
    _as(monkeypatch, "michele")
    body = TestClient(app).get("/api/docs").json()
    names = [d["name"] for d in body["docs"]]
    assert names[0] == "README.md"
    assert "INDEX.md" in names
    index = next(d for d in body["docs"] if d["name"] == "INDEX.md")
    assert index["title"] == "SharedLLM Documentation Index"
    client = TestClient(app)
    for name in ("README.md", "INDEX.md", "INTEGRATIONS.md"):
        assert name in names
        assert client.get(f"/api/docs/{name}").status_code == 200, name


def test_traversal_still_blocked(monkeypatch):
    _as(monkeypatch, "michele")
    assert TestClient(app).get("/api/docs/..%2F.env").status_code in (403, 404)

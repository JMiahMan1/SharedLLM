"""Bibles tagged in the library: admins see and install them, nobody else."""
from fastapi.testclient import TestClient

from services.gateway import main


def _setup(monkeypatch, ident):
    calls = []

    async def acting(request):
        return ident

    async def bible(method, path, *, label, params=None, json=None, timeout=None):
        calls.append((method, path, json))
        return {"books": [], "count": 0}

    class Resp:
        status = 200

        async def json(self, content_type=None):
            return {"status": "succeeded", "message": "ok", "log": []}

    async def call(method, path, *, params=None, json=None, timeout=None):
        calls.append((method, path, json))
        return Resp()

    async def response(resp):
        return await resp.json()

    monkeypatch.setattr(main, "_acting_identity", acting)
    monkeypatch.setattr(main, "_bible_json", bible)
    monkeypatch.setattr(main, "_bible_call", call)
    monkeypatch.setattr(main, "_bible_import_response", response)
    return calls


def test_tagged_bibles_are_for_admins(monkeypatch):
    calls = _setup(monkeypatch, {"user": "kid", "is_admin": False})
    client = TestClient(main.app)
    assert client.get("/api/bible/admin/library/tagged").status_code == 403
    assert client.post("/api/bible/admin/library/tagged/5/install", json={}).status_code == 403
    assert calls == []
    calls = _setup(monkeypatch, {"user": "dad", "is_admin": True})
    client = TestClient(main.app)
    assert client.get("/api/bible/admin/library/tagged").status_code == 200
    assert client.post("/api/bible/admin/library/tagged/5/install", json={"code": "nkjv"}).status_code == 200
    assert client.post("/api/bible/admin/library/tagged/sync").status_code == 200
    assert calls[1] == ("POST", "/admin/library/tagged/5/install", {"code": "nkjv"})

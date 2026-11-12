"""GET /api/resources: the family's books on a topic, and passages that answer a question."""
from fastapi.testclient import TestClient

from services.gateway import main


class _Resp:
    def __init__(self, status, body):
        self.status = status
        self._body = body

    async def json(self):
        return self._body


class _Client:
    def __init__(self):
        self.calls = []

    async def get(self, url, params=None, **kw):
        self.calls.append(("GET", url, params))
        return _Resp(200, {"books": [{"title": "Great Lent", "author": "Alexander Schmemann"}], "documents": []})

    async def post(self, url, json=None, **kw):
        self.calls.append(("POST", url, json))
        return _Resp(200, {"results": [{"content": "Lent is a journey", "metadata": {"title": "Great Lent", "chapter": "1"}}]})


def _setup(monkeypatch, user="alex"):
    client = _Client()

    async def ident(request):
        return {"user": user, "nextcloud_user": "alex"} if user else {}

    monkeypatch.setattr(main, "_resolve_identity_from_request", ident)
    monkeypatch.setattr(main, "get_http_client", lambda: client)
    return client


def test_resources_list_the_topic_and_search_only_within_it(monkeypatch):
    client = _setup(monkeypatch)
    body = TestClient(main.app).get("/api/resources", params={"topic": "jarvis:Fasting", "q": "why fast before Pascha"}).json()
    assert body["topic"] == "fasting" and body["books"][0]["title"] == "Great Lent"
    assert body["passages"][0]["title"] == "Great Lent"
    listing = next(c for c in client.calls if c[0] == "GET")
    assert listing[2]["tag"] == "fasting" and listing[2]["user_id"] == "alex"
    search = next(c for c in client.calls if c[0] == "POST")
    assert search[2]["tags"] == ["fasting"]


def test_resources_need_a_signed_in_caller_and_a_plain_topic(monkeypatch):
    _setup(monkeypatch, user="")
    assert TestClient(main.app).get("/api/resources", params={"topic": "fasting"}).status_code == 401
    _setup(monkeypatch)
    assert TestClient(main.app).get("/api/resources", params={"topic": "../etc"}).status_code == 400

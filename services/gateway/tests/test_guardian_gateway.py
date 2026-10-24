"""The parents' guardian routes: identity's visibility rule first, then telemetry."""
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient

from services.gateway import main as gateway_main
from services.gateway.main import app


@pytest.fixture
def wired(monkeypatch):
    calls: list[dict] = []
    allowed = {"value": True}

    async def _who(_request):
        return {"user": "dad"}

    class _Resp:
        def __init__(self, body):
            self.status, self._body = 200, body

        async def json(self, content_type=None):
            return self._body

    class _Client:
        async def get(self, url, **kw):
            calls.append({"url": url, **kw})
            if "may-view-raw" in url:
                return _Resp({"allowed": allowed["value"]})
            return _Resp({"status": "SUCCESS", "recap": {"has_data": False}})

    @asynccontextmanager
    async def fake():
        yield _Client()

    async def _proxy(resp):
        return resp._body

    monkeypatch.setattr(gateway_main, "_resolve_identity_from_request", _who)
    monkeypatch.setattr(gateway_main, "borrow_http_client", fake)
    monkeypatch.setattr(gateway_main, "_proxy_json_response", _proxy)
    return TestClient(app), calls, allowed


def test_a_parent_reads_the_recap(wired):
    client, calls, _ = wired
    r = client.get("/api/guardian/recap/kid", params={"day": "2026-10-21"})
    assert r.status_code == 200
    assert calls[0]["params"] == {"viewer": "dad", "subject": "kid"}
    assert calls[1]["url"].endswith("/api/telemetry/recap/kid") and calls[1]["params"] == {"day": "2026-10-21"}


def test_anyone_else_is_refused_before_telemetry_is_asked(wired):
    client, calls, allowed = wired
    allowed["value"] = False
    for path in ("/api/guardian/recap/kid", "/api/guardian/concerns/kid", "/api/guardian/activity/kid"):
        assert client.get(path).status_code == 403
    assert all("telemetry" not in c["url"] for c in calls)


def test_bad_day_is_refused(wired):
    client, _, _ = wired
    assert client.get("/api/guardian/recap/kid", params={"day": "sunday"}).status_code == 422


@pytest.fixture
def talk(monkeypatch):
    sent: list[dict] = []

    async def _who(_request):
        return {"user": "kid"}

    class _Resp:
        status = 200

        async def json(self, content_type=None):
            return {"parents": ["dad", "mom"], "display_name": "Emma"}

    class _Client:
        async def get(self, url, **kw):
            return _Resp()

    @asynccontextmanager
    async def fake():
        yield _Client()

    async def fake_send(payload, **kw):
        sent.append(payload)
        return True

    monkeypatch.setattr(gateway_main, "_resolve_identity_from_request", _who)
    monkeypatch.setattr(gateway_main, "borrow_http_client", fake)
    monkeypatch.setattr(gateway_main._notify, "send", fake_send)
    return TestClient(app), sent


def test_i_need_to_talk_reaches_only_the_parent_picked(talk):
    client, sent = talk
    r = client.post("/api/guardian/talk", json={"to": ["mom", "stranger"], "note": "after dinner?"})
    assert r.status_code == 200 and r.json()["told"] == ["mom"]
    assert [n["user"] for n in sent] == ["mom"]
    assert sent[0]["title"] == "Emma would like to talk" and sent[0]["body"] == "after dinner?"
    assert client.post("/api/guardian/talk", json={"to": ["stranger"]}).status_code == 422


def test_i_need_to_talk_defaults_to_both_parents(talk):
    client, sent = talk
    assert client.post("/api/guardian/talk", json={}).json()["told"] == ["dad", "mom"]
    assert client.get("/api/guardian/my-parents").json()["parents"] == ["dad", "mom"]


def test_guidance_follows_the_childs_week(monkeypatch):
    async def _who(_request):
        return {"user": "dad"}

    class _Resp:
        status = 200

        def __init__(self, body):
            self._body = body

        async def json(self, content_type=None):
            return self._body

    class _Client:
        async def get(self, url, **kw):
            if "may-view-raw" in url:
                return _Resp({"allowed": True})
            if "guard/parents" in url:
                return _Resp({"parents": ["dad"], "display_name": "Emma"})
            return _Resp({"recap": {"week": {"start": "a", "end": "b"}, "concerns": [{"category": "bullying"}]}})

    @asynccontextmanager
    async def fake():
        yield _Client()

    monkeypatch.setattr(gateway_main, "_resolve_identity_from_request", _who)
    monkeypatch.setattr(gateway_main, "borrow_http_client", fake)
    body = TestClient(app).get("/api/guardian/guidance/kid").json()
    assert body["prompts"][0]["signal"] == "bullying" and "Emma" in body["prompts"][0]["prayer"]


def test_a_childs_hard_mornings_tell_the_parents_gently(talk, monkeypatch):
    client, sent = talk
    recent = {"checkins": [{"day": f"2026-10-2{d}", "mood": 1} for d in range(3)]}

    async def fake_health(method, path, request, *, params=None, body=None):
        return recent if method == "GET" else {"day": "2026-10-23", "mood": 1}

    monkeypatch.setattr(gateway_main, "_health_json", fake_health)
    assert client.put("/api/health/checkins/2026-10-23", json={"mood": 1}).status_code == 200
    assert [n["user"] for n in sent] == ["dad", "mom"]
    assert sent[0]["title"] == "Emma has had a few hard days" and "mood" not in sent[0]["body"]
    assert sent[0]["once"].startswith("burden:kid:")

    sent.clear()
    recent["checkins"] = [{"day": "2026-10-23", "mood": 4}]
    client.put("/api/health/checkins/2026-10-24", json={"mood": 4})
    assert sent == []


def test_a_parent_asking_about_their_childs_week_gets_the_recap(monkeypatch):
    import asyncio

    class _Resp:
        status = 200

        def __init__(self, body):
            self._body = body

        async def json(self, content_type=None):
            return self._body

    class _Client:
        async def get(self, url, **kw):
            if url.endswith("/guardian/children"):
                return _Resp({"children": [
                    {"username": "emma", "display_name": "Emma", "parents": ["dad", "mom"]},
                    {"username": "neighbour", "display_name": "Zoe", "parents": ["other"]},
                ]})
            return _Resp({"recap": {"has_data": False}})

    @asynccontextmanager
    async def fake():
        yield _Client()

    monkeypatch.setattr(gateway_main, "borrow_http_client", fake)
    answer = asyncio.run(gateway_main._child_week_answer("How was Emma's week?", "dad"))
    assert answer == "Nothing has been reported from Emma's phone yet this week."
    assert asyncio.run(gateway_main._child_week_answer("How was Zoe's week?", "dad")) is None  # not his child
    assert asyncio.run(gateway_main._child_week_answer("turn on the lights", "dad")) is None


def test_note_routes_refuse_an_empty_body_cleanly():
    client = TestClient(app)
    for path in ("/api/communication/notes/versions", "/api/communication/notes/share"):
        assert client.post(path, content=b"").status_code == 422

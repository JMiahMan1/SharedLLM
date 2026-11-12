"""The parents' guardian routes: identity's visibility rule first, then telemetry."""
import base64
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient

from services.gateway import main as gateway_main
from services.gateway.main import app

PNG_1X1 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


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
    for path in ("/api/guardian/recap/kid", "/api/guardian/concerns/kid", "/api/guardian/concerns/kid/export", "/api/guardian/activity/kid"):
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
    client, _sent = talk
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
                    {"username": "emma", "display_name": "Emma", "parents": ["dad", "mom"], "device_kinds": ["tablet"]},
                    {"username": "neighbour", "display_name": "Zoe", "parents": ["other"]},
                ]})
            return _Resp({"recap": {"has_data": False}})

    @asynccontextmanager
    async def fake():
        yield _Client()

    monkeypatch.setattr(gateway_main, "borrow_http_client", fake)
    answer = asyncio.run(gateway_main._child_week_answer("How was Emma's week?", "dad"))
    assert answer == "Nothing has been reported from Emma's tablet yet this week."  # what she carries
    assert asyncio.run(gateway_main._child_week_answer("How was Zoe's week?", "dad")) is None  # not his child
    assert asyncio.run(gateway_main._child_week_answer("turn on the lights", "dad")) is None


def test_note_routes_refuse_an_empty_body_cleanly():
    client = TestClient(app)
    for path in ("/api/communication/notes/versions", "/api/communication/notes/share"):
        assert client.post(path, content=b"").status_code == 422


@pytest.fixture
def export_wired(monkeypatch):
    state = {
        "allowed": True,
        "is_admin": False,
        "parents": ["dad"],
        "concerns": [
            {
                "id": "c1",
                "at": "2026-11-10T21:49:26Z",
                "category": "bullying",
                "label": "Bullying",
                "level": "high",
                "item": "Unknown",
                "feature": "sms",
                "action": "message",
                "excerpt": "you are worthless",
                "context": "a longer piece of surrounding context",
                "file_name": "photo.png",
                "has_image": True,
                "review": {"verdict": "confirmed", "reason": "directed at a classmate"},
            }
        ],
    }
    calls: list[dict] = []

    async def _who(_request):
        return {"user": "dad", "is_admin": state["is_admin"]}

    class _Resp:
        status = 200

        def __init__(self, body):
            self._body = body

        async def json(self, content_type=None):
            return self._body

    class _Client:
        async def get(self, url, **kw):
            calls.append({"url": url, **kw})
            if "may-view-raw" in url:
                return _Resp({"allowed": state["allowed"], "technical": state["is_admin"],
                              "comms": state.get("comms", True)})
            if "guard/parents" in url:
                return _Resp({"parents": state["parents"], "display_name": "Kid"})
            if url.endswith("/image"):
                return _Resp({"status": "SUCCESS", "file_name": "photo.png", "image_b64": PNG_1X1})
            if "/api/telemetry/concerns/" in url:
                return _Resp({"status": "SUCCESS", "concerns": state["concerns"]})
            return _Resp({"status": "SUCCESS"})

        async def post(self, url, **kw):
            calls.append({"url": url, **kw})
            return _Resp({"status": "SUCCESS", "concern": state["concerns"][0]})

    @asynccontextmanager
    async def fake():
        yield _Client()

    monkeypatch.setattr(gateway_main, "_resolve_identity_from_request", _who)
    monkeypatch.setattr(gateway_main, "borrow_http_client", fake)
    return TestClient(app), calls, state


def test_a_parent_can_export_the_whole_concern_list(export_wired):
    client, calls, _ = export_wired
    r = client.get("/api/guardian/concerns/kid/export")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert r.headers["content-disposition"].startswith('attachment; filename="jarvis-concerns-kid-')
    assert r.headers["content-disposition"].endswith('.csv"')
    assert r.headers["cache-control"] == "no-store"
    lines = r.text.splitlines()
    assert lines[0] == (
        "id,at,category,label,level,item,feature,action,excerpt,context,file_name,has_image,"
        "review_verdict,review_reason,human_verdict,human_by,human_at,human_note"
    )
    assert "bullying" in r.text and "confirmed" in r.text
    assert "a longer piece of surrounding context" in r.text
    assert "yes" in lines[1]
    telemetry = [c for c in calls if "/api/telemetry/concerns/" in c["url"]]
    assert telemetry and telemetry[0]["params"] == {"limit": 300}


def test_an_admin_can_export_without_being_a_parent(export_wired):
    client, _calls, state = export_wired
    state["is_admin"] = True
    state["parents"] = []
    r = client.get("/api/guardian/concerns/kid/export", params={"format": "json"})
    assert r.status_code == 200
    body = r.json()
    assert body["child"] == "kid" and body["count"] == 1
    assert body["concerns"][0]["category"] == "bullying" and body["exported_at"]


def test_a_non_parent_cannot_export_even_when_raw_access_is_allowed(export_wired):
    client, calls, state = export_wired
    state["parents"] = ["mom"]  # dad passes identity's raw rule, but is not this child's parent
    r = client.get("/api/guardian/concerns/kid/export")
    assert r.status_code == 403
    assert all("/api/telemetry/concerns/" not in c["url"] for c in calls)


def test_export_refuses_a_bad_format(export_wired):
    client, calls, _ = export_wired
    assert client.get("/api/guardian/concerns/kid/export", params={"format": "pdf"}).status_code == 422
    assert calls == []


def test_export_neutralizes_spreadsheet_formulas(export_wired):
    client, _calls, state = export_wired
    state["concerns"][0]["excerpt"] = '=HYPERLINK("https://example.test")'
    r = client.get("/api/guardian/concerns/kid/export")
    assert r.status_code == 200
    assert "'=HYPERLINK" in r.text


def test_a_parent_can_download_a_concern_image_as_an_attachment(export_wired):
    client, calls, _ = export_wired
    r = client.get("/api/guardian/concerns/kid/c1/image")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.headers["content-disposition"] == 'attachment; filename="photo.png"'
    assert r.headers["cache-control"] == "no-store"
    assert r.content == base64.b64decode(PNG_1X1)
    assert any(c["url"].endswith("/api/telemetry/concerns/kid/c1/image") for c in calls)


def test_a_non_parent_cannot_download_a_concern_image(export_wired):
    client, calls, state = export_wired
    state["parents"] = ["mom"]
    assert client.get("/api/guardian/concerns/kid/c1/image").status_code == 403
    assert all(not c["url"].endswith("/image") for c in calls)


def test_a_parent_can_record_a_human_review(export_wired):
    client, calls, _ = export_wired
    r = client.post(
        "/api/guardian/concerns/kid/c1/review",
        json={"verdict": "confirmed", "note": "Talked to her."},
    )
    assert r.status_code == 200
    review_call = next(c for c in calls if c["url"].endswith("/c1/review"))
    assert review_call["json"] == {"verdict": "confirmed", "by": "dad", "note": "Talked to her."}


def test_a_bad_review_verdict_is_refused_before_telemetry(export_wired):
    client, calls, _ = export_wired
    assert client.post(
        "/api/guardian/concerns/kid/c1/review", json={"verdict": "maybe"}
    ).status_code == 422
    assert all(not c["url"].endswith("/review") for c in calls)


def test_without_comms_access_the_export_leaves_out_message_concerns(export_wired):
    """The export can never reach further than the Concerns card the viewer can read."""
    client, _calls, state = export_wired
    state["is_admin"], state["parents"], state["comms"] = True, [], False
    state["concerns"] = state["concerns"] + [{"id": "c2", "category": "violence", "feature": "browser"}]
    body = client.get("/api/guardian/concerns/kid/export", params={"format": "json"}).json()
    assert [c["id"] for c in body["concerns"]] == ["c2"] and body["count"] == 1


def test_without_comms_access_a_message_concern_cannot_be_reviewed_or_downloaded(export_wired):
    client, calls, state = export_wired
    state["is_admin"], state["parents"], state["comms"] = True, [], False
    review = client.post("/api/guardian/concerns/kid/c1/review", json={"verdict": "confirmed"})
    image = client.get("/api/guardian/concerns/kid/c1/image")
    assert review.status_code == 404 and image.status_code == 404
    assert not [c for c in calls if c["url"].endswith(("/review", "/image"))]


def test_concern_ids_are_escaped_into_the_telemetry_path(export_wired):
    client, calls, _state = export_wired
    client.post("/api/guardian/concerns/kid/a%3Fb/review", json={"verdict": "dismissed"})
    posted = [c["url"] for c in calls if c["url"].endswith("/review")]
    assert posted and posted[0].endswith("/concerns/kid/a%3Fb/review")

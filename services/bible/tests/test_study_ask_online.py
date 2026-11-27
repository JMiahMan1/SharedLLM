"""A Bible question while the local AI is offline may be answered online, carrying only public text.

The gateway refuses the question with its offline sentence; the bible service
then asks the gateway's online fallback with ``study_ask.online_payload``: the
reference, the verses' public text, the translation, the question and the
reading level. Nothing the household stores (notes, library passages, web
results, the reader's name) is in it. Without a provider the reader sees the
offline sentence as before. Names here are fake.
"""
from __future__ import annotations

import inspect
import json

import pytest
from fastapi.testclient import TestClient

from services.bible import study_ask, study_sources, verse_context

pytestmark = pytest.mark.unit

OFFLINE = "Jarvis's thinking brain (the local AI) is offline right now."
NOTE = "Answered by an online model while Jarvis's local AI is offline."
FIELDS = {"feature", "reference", "verses", "translation", "question", "reading_level"}
SECRETS = ("reader-alex-secret", "LIBRARY-SECRET", "WEB-SECRET", "RELATED-SECRET", "NOTE-SECRET")


@pytest.fixture
def offline_gateway(monkeypatch):
    """The gateway's local model is offline; every source a prompt could carry holds a secret."""

    async def refused(**_kw):
        raise study_ask.StudyAskOffline(OFFLINE)

    async def refused_stream(**_kw):
        raise study_ask.StudyAskOffline(OFFLINE)
        yield  # pragma: no cover

    async def sources(**_kw):
        return {
            "related": [{"reference": "Romans 8:28", "verses": [{"reference": "Romans 8:28", "text": "RELATED-SECRET"}]}],
            "library": [{"title": "LIBRARY-SECRET", "author": "A", "text": "LIBRARY-SECRET"}],
            "web": [{"title": "WEB-SECRET", "url": "https://example.test", "snippet": "WEB-SECRET"}],
        }

    monkeypatch.setattr(study_ask, "ask", refused)
    monkeypatch.setattr(study_ask, "ask_stream", refused_stream)
    monkeypatch.setattr(study_sources, "gather", sources)


@pytest.fixture
def online(monkeypatch):
    sent: list[dict] = []

    async def fake(payload, **_kw):
        sent.append(payload)
        return "In the beginning God made all things. See John 1:1.", NOTE

    monkeypatch.setattr(study_ask, "ask_online", fake)
    return sent


@pytest.fixture
def no_provider(monkeypatch):
    sent: list[dict] = []

    async def declined(payload, **_kw):
        sent.append(payload)
        return None

    monkeypatch.setattr(study_ask, "ask_online", declined)
    return sent


def _check_payload(payload: dict) -> None:
    assert set(payload) == FIELDS
    assert all(set(v) == {"reference", "text"} for v in payload["verses"])
    text = json.dumps(payload)
    for secret in SECRETS:
        assert secret not in text


def test_an_offline_answer_comes_from_online_with_the_note(loaded_client: TestClient, offline_gateway, online):
    resp = loaded_client.post("/study/ask?username=reader-alex-secret",
                              json={"ref": "Gen 1:1", "question": "What is being created?", "depth": "teen"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["answer"].startswith("In the beginning")
    assert body["online_note"] == NOTE
    assert body["references"] == ["John 1:1"]
    [payload] = online
    _check_payload(payload)
    assert payload["feature"] == "bible_study_ask"
    assert payload["reference"] == "Genesis 1:1"
    assert payload["translation"] == "kjv"
    assert payload["reading_level"] == "teen"
    assert payload["question"] == "What is being created?"
    assert payload["verses"][0]["reference"] == "Genesis 1:1" and payload["verses"][0]["text"]


def test_no_provider_shows_the_normal_offline_sentence(loaded_client: TestClient, offline_gateway, no_provider):
    resp = loaded_client.post("/study/ask?username=alex", json={"ref": "Gen 1:1", "question": "Why?"})
    assert resp.status_code == 503
    assert resp.json()["detail"] == OFFLINE
    assert len(no_provider) == 1


def test_the_stream_gives_the_online_answer_and_its_note(loaded_client: TestClient, offline_gateway, online):
    resp = loaded_client.post("/study/ask/stream?username=alex", json={"ref": "Gen 1:1", "question": "Why?"})
    events = [json.loads(line) for line in resp.text.splitlines() if line.strip()]
    assert [e["type"] for e in events] == ["start", "text", "done"]
    assert events[-1]["online_note"] == NOTE
    _check_payload(online[0])


def test_the_stream_without_a_provider_is_the_offline_error(loaded_client: TestClient, offline_gateway, no_provider):
    resp = loaded_client.post("/study/ask/stream?username=alex", json={"ref": "Gen 1:1", "question": "Why?"})
    events = [json.loads(line) for line in resp.text.splitlines() if line.strip()]
    assert events[-1] == {"type": "error", "detail": OFFLINE}


def test_a_local_answer_has_no_note_and_never_goes_online(loaded_client: TestClient, monkeypatch, no_provider):
    async def local(**_kw):
        return "From the local model."

    async def sources(**_kw):
        return {}

    monkeypatch.setattr(study_ask, "ask", local)
    monkeypatch.setattr(study_sources, "gather", sources)
    resp = loaded_client.post("/study/ask?username=alex", json={"ref": "Gen 1:1", "question": "Why?"})
    assert resp.status_code == 200 and resp.json()["online_note"] is None
    assert no_provider == []


def test_other_failures_do_not_go_online(loaded_client: TestClient, monkeypatch, no_provider):
    async def busy(**_kw):
        raise study_ask.StudyAskUnavailable("The model did not answer (HTTP 500).")

    async def sources(**_kw):
        return {}

    monkeypatch.setattr(study_ask, "ask", busy)
    monkeypatch.setattr(study_sources, "gather", sources)
    resp = loaded_client.post("/study/ask?username=alex", json={"ref": "Gen 1:1", "question": "Why?"})
    assert resp.status_code == 503 and no_provider == []


def test_the_payload_keeps_only_reference_and_text_of_each_verse():
    payload = study_ask.online_payload(
        reference="John 3:16",
        verses=[{"reference": "John 3:16", "text": "For God so loved the world.", "osis": "John",
                 "note": "NOTE-SECRET", "highlight": "NOTE-SECRET", "user": "reader-alex-secret"}],
        translation="kjv", question="Why?", depth="child",
    )
    _check_payload(payload)
    assert payload["verses"] == [{"reference": "John 3:16", "text": "For God so loved the world."}]


def test_the_payload_builder_has_no_way_in_for_personal_data():
    params = set(inspect.signature(study_ask.online_payload).parameters)
    assert params == {"reference", "verses", "translation", "question", "depth"}


def test_the_verse_of_the_day_note_never_goes_online():
    """Its note is cached as the household's own; an online answer must not become it."""
    assert "ask_online" not in inspect.getsource(verse_context)


class _Resp:
    def __init__(self, status: int, body: dict):
        self.status = status
        self._body = body

    async def json(self):
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Session:
    def __init__(self, resp: _Resp):
        self.resp = resp
        self.calls: list[tuple[str, dict, dict]] = []

    def post(self, url, json=None, headers=None):
        self.calls.append((url, json, headers))
        return self.resp

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


@pytest.mark.parametrize("status,body,expected", [
    (200, {"answer": "An answer.", "note": NOTE}, ("An answer.", NOTE)),
    (503, {"reason": "no_provider"}, None),
    (409, {"reason": "local_up"}, None),
    (200, {"answer": "", "note": NOTE}, None),
    (200, {"answer": "No note."}, None),
])
async def test_ask_online_reads_the_gateways_reply(monkeypatch, status, body, expected):
    session = _Session(_Resp(status, body))
    monkeypatch.setattr(study_ask.aiohttp, "ClientSession", lambda **kw: session)
    got = await study_ask.ask_online({"feature": "bible_study_ask"}, gateway_url="http://gw.test/",
                                     internal_secret="s")
    assert got == expected
    assert session.calls[0][0] == "http://gw.test/internal/cloud-fallback/bible-study"
    assert session.calls[0][2] == {"X-Internal-Secret": "s"}


async def test_ask_online_without_a_gateway_does_nothing():
    assert await study_ask.ask_online({}, gateway_url="", internal_secret="") is None


async def test_an_offline_refusal_is_its_own_kind():
    class _R:
        status = 503

        async def json(self):
            return {"message": OFFLINE, "offline": "local_ai"}

    class _Busy:
        status = 500

        async def json(self):
            return {"detail": "boom"}

    assert isinstance(await study_ask._refusal(_R()), study_ask.StudyAskOffline)
    busy = await study_ask._refusal(_Busy())
    assert isinstance(busy, study_ask.StudyAskUnavailable) and not isinstance(busy, study_ask.StudyAskOffline)


async def test_stream_offline_sse_chunk_raises_study_ask_offline(monkeypatch):
    """When the first streaming request trips the breaker mid-flight, the gateway emits an SSE
    chunk carrying offline='local_ai'. ask_stream must raise StudyAskOffline so online fallback runs."""

    class _StreamResp:
        status = 200

        def __init__(self):
            payload = {
                "choices": [{"delta": {"content": OFFLINE}, "finish_reason": "stop"}],
                "offline": "local_ai",
                "error_code": "local_ai_offline",
            }
            self.content = self._lines(payload)

        @staticmethod
        async def _lines(payload):
            yield f"data: {json.dumps(payload)}\n".encode()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    class _StreamSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def post(self, *a, **kw):
            return _StreamResp()

    monkeypatch.setattr(study_ask.aiohttp, "ClientSession", lambda **kw: _StreamSession())
    with pytest.raises(study_ask.StudyAskOffline):
        async for _ in study_ask.ask_stream(prompt="p", user="u", gateway_url="http://gw.test", internal_secret="s"):
            pass


def test_online_payload_clamps_long_reference_and_verse_text():
    long_ref = "1 Corinthians 13:1-13; " * 6
    long_text = "x" * 2500
    payload = study_ask.online_payload(
        reference=long_ref,
        verses=[{"reference": long_ref, "text": long_text}],
        translation="kjv",
        question="What does this mean?",
        depth="adult",
    )
    assert len(payload["reference"]) <= 80
    assert len(payload["verses"][0]["reference"]) <= 80
    assert len(payload["verses"][0]["text"]) <= 2000

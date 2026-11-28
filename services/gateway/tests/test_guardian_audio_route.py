"""POST /api/guardian/audio/scan: the phone's only way to have a voice note read.

The gateway is a thin, authenticated door: identity decides who may claim whose
phone, and the answer - including a 503 and its Retry-After - is passed through
untouched, because the phone keeps the clip exactly as long as it is told to.
"""
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient

from services.gateway import main as gateway_main
from services.gateway.main import app

AUDIO = "T2dnUw=="  # "OggS", enough to be carried through untouched
META = {"source": "mms", "direction": "in", "contact": "Leo", "message_key": "mms:9"}


@pytest.fixture
def wired(monkeypatch):
    acting = {"as": "dad", "allowed": True}
    calls: list[dict] = []
    answer = {"status": 200, "body": {"status": "SUCCESS", "scanned": True, "transcript": "hi"}, "retry": None}

    async def _who(_request):
        return {"user": acting["as"]}

    async def _may_view(_request, _user):
        if not acting["allowed"]:
            from fastapi import HTTPException

            raise HTTPException(status_code=403, detail="Forbidden")
        return True

    class _Resp:
        def __init__(self):
            self.status = answer["status"]
            self.headers = {"Retry-After": answer["retry"]} if answer["retry"] else {}

        async def json(self, content_type=None):
            return answer["body"]

    class _Client:
        async def post(self, url, **kw):
            calls.append({"url": url, **kw})
            return _Resp()

    @asynccontextmanager
    async def fake():
        yield _Client()

    monkeypatch.setattr(gateway_main, "_resolve_identity_from_request", _who)
    monkeypatch.setattr(gateway_main, "_raw_data_access", _may_view)
    monkeypatch.setattr(gateway_main, "borrow_http_client", fake)
    return TestClient(app), calls, answer, acting


def test_a_voice_note_is_proxied_with_its_message_context(wired):
    client, calls, _answer, _acting = wired
    response = client.post(
        "/api/guardian/audio/scan",
        json={"user": "kid", "audio_b64": AUDIO, "mime_type": "audio/ogg", "file_name": "voice.ogg", "metadata": META},
    )

    assert response.status_code == 200 and response.json()["transcript"] == "hi"
    sent = calls[0]
    assert sent["url"].endswith("/api/telemetry/audio/scan")
    assert sent["json"]["user"] == "kid"
    assert sent["json"]["metadata"]["contact"] == "Leo"
    assert sent["headers"]["X-Internal-Secret"]


def test_the_parents_phone_claiming_its_own_clip_needs_no_raw_access(wired):
    client, calls, _answer, _acting = wired
    client.post("/api/guardian/audio/scan", json={"audio_b64": AUDIO})

    # No "user" in the request: the caller's own name is used, and identity is
    # not asked whether they may see themselves.
    assert calls[0]["json"]["user"] == "dad"


def test_a_forbidden_user_is_refused_before_telemetry(wired):
    client, calls, _answer, acting = wired
    acting["allowed"] = False
    response = client.post("/api/guardian/audio/scan", json={"user": "kid", "audio_b64": AUDIO})

    assert response.status_code == 403
    assert calls == []


def test_a_busy_transcriber_keeps_its_retry_after(wired):
    client, _calls, answer, _acting = wired
    answer.update(
        status=503,
        body={"detail": "The voice message could not be read right now; keep it and try again later.",
              "scanned": False, "retry_after_s": 120},
        retry="120",
    )
    response = client.post("/api/guardian/audio/scan", json={"audio_b64": AUDIO})

    # The phone re-reads this header to decide when to try again; dropping it
    # would turn a paused transcriber into a lost clip.
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "120"
    assert response.json()["retry_after_s"] == 120

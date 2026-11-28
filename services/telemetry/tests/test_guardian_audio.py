"""Guardian: a recorded voice message in a text thread, read on this LAN.

The clip itself is never kept - not on disk, not in Redis - and the transcript
is filed as a text message so everything that already scans texts reads it.
Worth testing here and not only in the gateway: a 503 has to leave the phone
able to retry, and a deduplicated clip must not be swallowed by its own retry.
"""
import base64

import pytest
from fakes import FakeRedis
from fastapi.testclient import TestClient

from services.common import concern_review
from services.telemetry import guardian, llm
from services.telemetry.config import INTERNAL_SECRET as EXPECTED_SECRET
from services.telemetry.main import app

HEADERS = {"X-Internal-Secret": EXPECTED_SECRET}
KIDS = {
    "kid": {
        "username": "kid",
        "display_name": "Emma",
        "parents": ["dad", "mom"],
        "monitoring": "severe",
        "device_kinds": ["phone"],
    },
    "teen": {
        "username": "teen",
        "display_name": "Noah",
        "parents": ["dad"],
        "monitoring": "off",
        "device_kinds": ["phone"],
    },
}
AUDIO = base64.b64encode(b"OggS this is not really a voice note").decode()
META = {
    "source": "mms",
    "direction": "in",
    "contact": "Leo",
    "number": "+15550002",
    "message_text": "listen to this",
    "message_key": "mms:9",
    "sent_at": 1_760_000_000_000,
}


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


@pytest.fixture
def wired(monkeypatch):
    """A telemetry app with the transformers doubled: no network, no GPU."""
    fake = FakeRedis()
    sent: list[dict] = []
    calls: list[tuple] = []
    seen_second_pass: list[dict | None] = []
    state = {"transcript": "i want to die", "fail": False}

    async def _children(_session):
        return KIDS

    async def _deliver(body):
        sent.append(body)
        return {"status": "SUCCESS"}

    async def _no_model(_session):
        return {}

    async def _transcribe(_session, audio: bytes, mime: str, name: str):
        calls.append((audio, mime, name))
        if state["fail"]:
            raise guardian.AudioScanUnavailable("the transcriber is offline")
        return state["transcript"]

    real_second_pass = guardian.second_pass

    async def _spy_second_pass(session, event, found):
        seen_second_pass.extend(concern.get("message_context") for concern in found)
        return await real_second_pass(session, event, found)

    monkeypatch.setattr("services.telemetry.main._redis", lambda: fake)
    monkeypatch.setattr("services.telemetry.main.deliver_notice", _deliver)
    monkeypatch.setattr(guardian, "children", _children)
    monkeypatch.setattr(guardian, "_model_settings", _no_model)
    monkeypatch.setattr(guardian, "transcribe_audio", _transcribe)
    monkeypatch.setattr(guardian, "second_pass", _spy_second_pass)
    with TestClient(app) as client:
        yield client, fake, sent, calls, seen_second_pass, state


def _post(client, **overrides):
    body = {"user": "kid", "audio_b64": AUDIO, "mime_type": "audio/ogg", "file_name": "voice.ogg", "metadata": META}
    body.update(overrides)
    return client.post("/api/telemetry/audio/scan", headers=HEADERS, json=body)


def test_a_voice_note_reaches_the_parents_with_who_said_it(wired):
    client, _fake, sent, calls, seen_second_pass, _state = wired
    response = _post(client)

    assert response.status_code == 200
    body = response.json()
    assert body["scanned"] is True and body["transcript"] == "i want to die"
    assert body["concern"] is True and body["category"] == "self_harm"

    # The clip went to the transcriber as its own bytes and its own kind: a
    # Whisper run over ".ogg" bytes named ".m4a" is a wasted retry.
    assert calls == [(b"OggS this is not really a voice note", "audio/ogg", "voice.ogg")]

    # Both parents hear it, and the notice names the sender before it quotes.
    assert [n["user"] for n in sent] == ["dad", "mom"]
    assert "From Leo (MMS)." in sent[0]["body"]
    assert 'Message: "listen to this"' in sent[0]["body"]
    assert "want to die" in sent[0]["body"]

    # The second pass was given who said it: judging "a voice message" with no
    # sender and no accompanying words is judging half the exchange.
    assert seen_second_pass and seen_second_pass[0]["contact"] == "Leo"

    concerns = client.get("/api/telemetry/concerns/kid", headers=HEADERS).json()["concerns"]
    assert concerns[0]["category"] == "self_harm"
    assert concerns[0]["message_context"]["contact"] == "Leo"
    assert concerns[0]["message_context"]["direction"] == "in"


def test_the_transcript_lands_in_the_text_feed_as_a_spoken_message(wired):
    client, _fake, _sent, _calls, _seen, _state = wired
    _post(client)

    feed = client.get("/api/telemetry/activity/kid", headers=HEADERS).json()["events"]
    voice = [e for e in feed if e.get("action") == "voice_message"]
    assert len(voice) == 1
    event = voice[0]
    assert event["feature"] == "sms" and event["item"] == "Leo"
    assert event["detail"]["body"] == "i want to die"
    assert event["detail"]["audio"] is True
    assert event["detail"]["message_text"] == "listen to this"
    # The phone's millisecond stamp survives into the detail; the feed gets ISO.
    assert event["detail"]["at"] == 1_760_000_000_000
    assert event["at"].startswith("2025-")


@pytest.mark.parametrize("user", ["teen", "dad"])
def test_adults_and_monitoring_off_are_never_transcribed(wired, user):
    client, _fake, sent, calls, _seen, _state = wired
    response = _post(client, user=user)

    assert response.status_code == 200
    assert response.json() == {"status": "SUCCESS", "scanned": False, "reason": "not_a_monitored_child"}
    assert calls == [] and sent == []


def test_the_same_clip_is_read_once(wired):
    client, _fake, _sent, calls, _seen, _state = wired
    assert _post(client).json()["scanned"] is True
    again = _post(client)

    assert again.status_code == 200
    assert again.json()["reason"] == "already_scanned" and again.json()["scanned"] is False
    assert len(calls) == 1
    # Only one transcript in the feed: the retry did not double it.
    feed = client.get("/api/telemetry/activity/kid", headers=HEADERS).json()["events"]
    assert len([e for e in feed if e.get("action") == "voice_message"]) == 1


def test_a_transcriber_that_is_down_lets_the_retry_through(wired):
    client, fake, _sent, calls, _seen, state = wired
    state["fail"] = True
    first = _post(client)

    assert first.status_code == 503
    assert int(first.headers["Retry-After"]) > 0
    assert first.json()["scanned"] is False and first.json()["retry_after_s"] > 0
    # The dedupe key was cleared, or the phone's retry would be turned away as
    # a duplicate of a clip that was never read.
    assert not [key for key in fake.strings if key.startswith("tel:audio_seen:")]

    state["fail"] = False
    second = _post(client)
    assert second.status_code == 200 and second.json()["scanned"] is True
    assert len(calls) == 2


def test_a_clip_with_no_speech_is_not_a_message(wired):
    client, _fake, sent, _calls, _seen, state = wired
    state["transcript"] = "  [BLANK_AUDIO] "
    response = _post(client)

    assert response.status_code == 200
    assert response.json()["transcript"] == "" and response.json()["concern"] is False
    assert sent == []


@pytest.mark.parametrize(
    "overrides,fragment",
    [
        ({"user": ""}, "user is required"),
        ({"audio_b64": ""}, "audio_b64 is required"),
        ({"audio_b64": "not base64 at all!!"}, "is not base64 audio"),
        ({"audio_b64": _b64(b"x" * (guardian.MAX_AUDIO_BYTES + 1))}, "too large"),
    ],
)
def test_bad_audio_is_refused_before_anything_is_transcribed(wired, overrides, fragment):
    client, _fake, _sent, calls, _seen, _state = wired
    response = _post(client, **overrides)

    assert response.status_code == 422
    assert fragment in response.json()["detail"]
    assert calls == []


@pytest.mark.asyncio
async def test_a_picture_message_keeps_the_words_that_came_with_it(monkeypatch):
    """The photo path carries the same context the voice path does."""
    payloads: list[dict] = []

    async def _settings(_session):
        return {"vision_ocr_proxy_url": "http://vision.test", "vision_ocr_model": "vlm"}

    async def _chat(_session, url, payload, timeout_s, background=False):
        payloads.append(payload)
        return (
            '{"concern": true, "category": "drugs", "level": "high", '
            '"description": "a bag of pills on a desk", "reason": "drug paraphernalia"}'
        )

    monkeypatch.setattr(guardian, "_model_settings", _settings)
    monkeypatch.setattr(llm, "local_chat_content", _chat)

    concern = await guardian.scan_media_item(None, KIDS["kid"], "image", "photo.jpg", "AAAA", META)

    assert concern is not None
    assert concern["message_context"]["contact"] == "Leo"
    quoted = payloads[0]["messages"][1]["content"][0]["text"]
    assert "From Leo (MMS)" in quoted and "listen to this" in quoted
    assert "From Leo (MMS)" in concern["context"]

    notice = guardian._concern_notice("dad", KIDS["kid"], concern, "2026-11-27")
    assert "From Leo (MMS)." in notice["body"]
    assert 'Message: "listen to this"' in notice["body"]


@pytest.mark.asyncio
async def test_a_picture_sent_by_the_child_is_not_called_incoming(monkeypatch):
    async def _settings(_session):
        return {"vision_ocr_proxy_url": "http://vision.test"}

    async def _chat(_session, url, payload, timeout_s, background=False):
        return '{"concern": true, "category": "violence", "level": "severe", "description": "a knife", "reason": "weapon"}'

    monkeypatch.setattr(guardian, "_model_settings", _settings)
    monkeypatch.setattr(llm, "local_chat_content", _chat)

    sent_out = {**META, "direction": "out"}
    concern = await guardian.scan_media_item(None, KIDS["kid"], "image", "photo.jpg", "AAAA", sent_out)

    assert concern["message_context"]["direction"] == "out"
    summary = concern_review.message_summary(concern["message_context"], with_text=False)
    assert summary == "Sent to Leo (MMS)"
    assert "Sent to Leo (MMS)." in guardian._concern_notice("dad", KIDS["kid"], concern, "2026-11-27")["body"]

"""The phone as a voice client: hearing, speaking, and what it may claim.

The Echo's turn is internal to the house - HomeHub holds the secret and builds
the context. A phone is signed in as a person instead, so it gets its own three
verbs: listen, chat (with its own context) and speak (docs/VOICE_PHONE.md).
"""
import pytest
from starlette.requests import Request

from services.gateway import main as gateway_main


class _Resp:
    def __init__(self, status, payload):
        self.status, self.payload = status, payload

    async def json(self, content_type=None):
        return self.payload


class _Client:
    def __init__(self, status=200, payload=None):
        self.status, self.payload, self.calls = status, payload if payload is not None else {}, []

    async def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return _Resp(self.status, self.payload)


class _Ctx:
    def __init__(self, client):
        self.client = client

    async def __aenter__(self):
        return self.client

    async def __aexit__(self, *_exc):
        return False


def _request(headers=None):
    scope = {
        "type": "http", "method": "POST", "path": "/api/chat", "raw_path": b"/api/chat",
        "query_string": b"",
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
    }
    return Request(scope)


def test_a_signed_in_phone_gets_its_room():
    context = gateway_main._voice_context_from(
        _request({"Authorization": "Bearer someones-key"}),
        {"voice_context": {"source": "android", "area": "Office", "conversation_id": "c1"}},
    )
    assert context == {"area": "Office", "conversation_id": "c1"}


def test_a_phone_cannot_move_a_timer_into_another_room():
    context = gateway_main._voice_context_from(
        _request({"Authorization": "Bearer someones-key"}),
        {"voice_context": {"source": "android", "area": "Office",
                           "satellite": "assist_satellite.office_echo",
                           "media_player": "media_player.kitchen"}},
    )
    assert context == {"area": "Office"}


def test_a_keyless_caller_claiming_a_phone_room_is_ignored():
    """The old rule stands: no key, no context - so no room to act in."""
    context = gateway_main._voice_context_from(
        _request(), {"voice_context": {"source": "android", "area": "Office"}})
    assert context is None


def test_the_hub_still_gets_its_whole_context():
    context = gateway_main._voice_context_from(
        _request({"X-Internal-Secret": gateway_main.INTERNAL_SECRET}),
        {"voice_context": {"satellite": "assist_satellite.office_echo", "area": "Office"}},
    )
    assert context["satellite"] == "assist_satellite.office_echo"
    assert context["area"] == "Office"


def test_speaking_a_line_returns_audio_in_the_persons_own_voice(client):
    client_stub = _Client(200, {"status": "SUCCESS", "detail": {"audio_base64": "UklGRg=="}})

    async def voice_for(speaker, satellite):
        return "af_heart"

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(gateway_main, "borrow_http_client", lambda: _Ctx(client_stub))
        mp.setattr(gateway_main, "_voice_for_reply", voice_for)
        resp = client.post("/api/voice/speak", json={"text": "One moment."})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["audio_b64"] == "UklGRg==" and body["voice"] == "af_heart"
    url, sent = client_stub.calls[0]
    assert url.endswith("/execute/tts")
    assert sent["json"]["text"] == "One moment."
    assert sent["json"]["voice"] == "af_heart"
    assert sent["headers"]["X-Internal-Secret"] == gateway_main.INTERNAL_SECRET


def test_a_reply_that_cannot_be_spoken_is_said_to_have_failed(client):
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(gateway_main, "borrow_http_client", lambda: _Ctx(_Client(200, {"detail": {}})))
        mp.setattr(gateway_main, "_voice_for_reply", lambda *a: _none())
        resp = client.post("/api/voice/speak", json={"text": "Hello"})
    assert resp.status_code == 502
    assert "unavailable" in resp.json()["detail"].lower()


def test_an_empty_line_is_refused_before_anything_is_synthesised(client):
    resp = client.post("/api/voice/speak", json={"text": "   "})
    assert resp.status_code == 400


def test_a_recording_is_heard_and_answered_as_text(client):
    client_stub = _Client(200, {"status": "SUCCESS", "detail": {"text": "  turn the hall lamp on "}})
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(gateway_main, "shared_http_client", lambda: _Ctx(client_stub))
        resp = client.post("/api/voice/listen", json={"audio_b64": "UklGRg==", "mime": "audio/wav"})

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"text": "turn the hall lamp on"}
    url, sent = client_stub.calls[0]
    assert url.endswith("/execute/stt/transcribe")
    assert sent["headers"]["X-Internal-Secret"] == gateway_main.INTERNAL_SECRET


def test_something_that_is_not_a_recording_is_refused(client):
    assert client.post("/api/voice/listen", json={"audio_b64": ""}).status_code == 400
    assert client.post("/api/voice/listen", json={"audio_b64": "not base64!!"}).status_code == 400


def test_hearing_unavailable_is_reported_rather_than_answered_as_silence(client):
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(gateway_main, "shared_http_client", lambda: _Ctx(_Client(501, {})))
        resp = client.post("/api/voice/listen", json={"audio_b64": "UklGRg=="})
    assert resp.status_code == 502
    assert "unavailable" in resp.json()["detail"].lower()


async def _none():
    return None

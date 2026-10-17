"""The Wyoming speech-to-text proxy: passes Home Assistant's audio through to
the real speech-to-text unchanged and records who spoke under the transcript."""
import asyncio
import json

import numpy as np
import pytest

from services.common import voice_learning, voice_speaker
from services.execution import wyoming_stt
from services.execution.ha_client import authorize_action


def _event(kind: str, data: dict | None = None, payload: bytes = b"") -> bytes:
    body = json.dumps(data or {}).encode()
    header = {"type": kind, "data_length": len(body)}
    if payload:
        header["payload_length"] = len(payload)
    return json.dumps(header).encode() + b"\n" + body + payload


class _FakeRedis:
    def __init__(self):
        self.values = {}

    async def set(self, key, value, ex=None):
        self.values[key] = (value, ex)

    async def get(self, key):
        found = self.values.get(key)
        return found[0] if found else None

    async def decr(self, key):
        left = int(await self.get(key) or 0) - 1
        self.values[key] = (str(left), None)
        return left

    async def delete(self, key):
        return 1 if self.values.pop(key, None) is not None else 0


async def _run(identify_result, monkeypatch, learning_for=None):
    """One utterance through the proxy; returns (what upstream got, what HA got, redis)."""
    received = []

    async def upstream(reader, writer):
        while (event := await wyoming_stt.read_event(reader)) is not None:
            received.append(event.raw)
            if event.type == "audio-stop":
                writer.write(_event("transcript", {"text": "Turn on the kitchen lights."}))
                await writer.drain()

    server = await asyncio.start_server(upstream, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    monkeypatch.setattr(wyoming_stt, "UPSTREAM", f"127.0.0.1:{port}")

    async def fake_identify(samples):
        assert samples.dtype == np.float32 and len(samples) == 16000  # 1 s at 16 kHz
        return identify_result

    monkeypatch.setattr(wyoming_stt, "identify", fake_identify)
    redis = _FakeRedis()
    if learning_for:
        redis.values[voice_learning.key(learning_for)] = (voice_learning.encode(), None)
    proxy = await asyncio.start_server(lambda r, w: wyoming_stt._handle(r, w, redis), "127.0.0.1", 0)
    reader, writer = await asyncio.open_connection("127.0.0.1", proxy.sockets[0].getsockname()[1])

    pcm = (np.sin(np.arange(16000) / 10) * 8000).astype("<i2").tobytes()
    sent = [
        _event("transcribe", {"language": "en"}),
        _event("audio-start", {"rate": 16000, "width": 2, "channels": 1}),
        _event("audio-chunk", {"rate": 16000, "width": 2, "channels": 1}, pcm),
        _event("audio-stop"),
    ]
    for raw in sent:
        writer.write(raw)
    await writer.drain()
    reply = await asyncio.wait_for(wyoming_stt.read_event(reader), 5)
    writer.close()
    proxy.close()
    server.close()
    return sent, received, reply, redis


@pytest.mark.asyncio
async def test_audio_passes_through_and_the_speaker_is_recorded(monkeypatch):
    sent, received, reply, redis = await _run(
        {"matched": True, "matched_username": "michele", "score": 0.91}, monkeypatch
    )
    assert received == sent  # byte for byte
    assert reply.type == "transcript" and reply.data["text"] == "Turn on the kitchen lights."
    value, ttl = redis.values[voice_speaker.key("turn on the kitchen lights")]
    assert voice_speaker.decode(value) == "michele" and ttl == voice_speaker.TTL_SECONDS


@pytest.mark.asyncio
async def test_an_unsure_match_records_nobody(monkeypatch):
    _, _, reply, redis = await _run({"matched": False, "score": 0.7}, monkeypatch)
    assert reply.type == "transcript"
    assert redis.values == {}


@pytest.mark.asyncio
async def test_a_learning_session_is_offered_the_next_match(monkeypatch):
    learned = asyncio.Event()

    async def fake_learn(redis, verdict):
        learned.set()

    monkeypatch.setattr(wyoming_stt, "_learn", fake_learn)
    await _run(
        {
            "matched": True,
            "matched_username": "michele",
            "score": 0.91,
            "embedding": [0.01] * 256,
            "speech_seconds": 2.8,
        },
        monkeypatch,
        learning_for="michele",
    )
    await asyncio.wait_for(learned.wait(), 2)


class _LearnRedis:
    def __init__(self, value="2"):
        self.value = value
        self.deleted = False

    async def get(self, _key):
        return self.value

    async def decr(self, _key):
        self.value = str(int(self.value) - 1)
        return int(self.value)

    async def delete(self, _key):
        self.deleted = True


class _FakeResponse:
    status = 200

    async def json(self, content_type=None):
        return {"status": "STORED"}


@pytest.mark.asyncio
async def test_learning_posts_the_signature_and_spends_a_sample(monkeypatch):
    calls = {}

    class FakeSession:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, json=None, headers=None, timeout=None):
            calls["url"] = url
            calls["json"] = json
            return _FakeResponse()

    monkeypatch.setattr(wyoming_stt.aiohttp, "ClientSession", FakeSession)
    redis = _LearnRedis()
    await wyoming_stt._learn(redis, {
        "matched_username": "michele",
        "score": 0.91,
        "embedding": [0.01] * 256,
        "speech_seconds": 2.8,
    })

    assert calls["url"].endswith("/api/voice/learn")
    assert calls["json"]["username"] == "michele"
    assert calls["json"]["model"] == wyoming_stt.voiceprint.MODEL
    assert redis.value == "1" and not redis.deleted


@pytest.mark.asyncio
async def test_an_unsure_match_is_never_learned(monkeypatch):
    class NoSession:
        def __init__(self, *args, **kwargs):
            pytest.fail("must not call Identity")

    monkeypatch.setattr(wyoming_stt.aiohttp, "ClientSession", NoSession)
    redis = _LearnRedis()
    await wyoming_stt._learn(redis, {
        "matched_username": "michele",
        "score": 0.7,
        "embedding": [0.01] * 256,
    })

    assert redis.value == "2"


def test_transcript_and_chat_text_meet_despite_punctuation_and_case():
    assert voice_speaker.key("Turn on the kitchen lights.") == voice_speaker.key("turn on  the kitchen lights")
    assert voice_speaker.key("  ...  ") is None


def test_pcm_is_converted_to_16k_mono():
    stereo_8k = np.zeros(8000 * 2, dtype="<i2").tobytes()
    assert len(wyoming_stt.to_samples(stereo_8k, 8000, 2, 2)) == 16000


def test_a_voice_never_opens_the_house_even_an_admins():
    admin_by_voice = {"user": "jeremiah", "is_admin": True, "identified_by": "voice"}
    assert not authorize_action(admin_by_voice, "lock", "unlock")
    assert not authorize_action(admin_by_voice, "cover", "open")
    assert authorize_action(admin_by_voice, "light", "turn_on")
    assert authorize_action({"user": "jeremiah", "is_admin": True}, "lock", "unlock")

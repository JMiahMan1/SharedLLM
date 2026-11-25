"""The Wyoming speech-to-text proxy: passes Home Assistant's audio through to
the real speech-to-text unchanged and records who spoke under the transcript."""
import asyncio
import json
import logging

import numpy as np
import pytest

from services.common import voice_enroll, voice_learning, voice_speaker
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


async def _run(identify_result, monkeypatch, learning_for=None, dead_first=False):
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
    upstreams = [f"127.0.0.1:{port}"]
    if dead_first:
        # A port nothing listens on: the proxy must move on to the next server.
        dead = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
        dead_port = dead.sockets[0].getsockname()[1]
        dead.close()
        await dead.wait_closed()
        upstreams.insert(0, f"127.0.0.1:{dead_port}")
    monkeypatch.setattr(wyoming_stt, "UPSTREAMS", upstreams)

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
        {"matched": True, "matched_username": "sam", "score": 0.91}, monkeypatch
    )
    assert received == sent  # byte for byte
    assert reply.type == "transcript" and reply.data["text"] == "Turn on the kitchen lights."
    value, ttl = redis.values[voice_speaker.key("turn on the kitchen lights")]
    assert voice_speaker.decode(value) == "sam" and ttl == voice_speaker.TTL_SECONDS


@pytest.mark.asyncio
async def test_an_unreachable_first_server_falls_back_to_the_next(monkeypatch, caplog):
    sent, received, reply, _ = await _run(
        {"matched": False, "score": 0.1}, monkeypatch, dead_first=True
    )
    assert received == sent
    assert reply.type == "transcript" and reply.data["text"] == "Turn on the kitchen lights."
    assert any("unreachable" in r.getMessage() and r.levelname == "WARNING" for r in caplog.records)


@pytest.mark.asyncio
async def test_an_unsure_match_records_nobody(monkeypatch):
    """Nobody is named, but the two best scores and the speech length are kept
    for the gateway's decision line; a command this far from anyone is no candidate."""
    verdict = {"matched": False, "score": 0.5, "threshold": 0.8, "margin": 0.2, "speech_seconds": 1.9,
               "candidates": [{"username": "sam", "score": 0.5}, {"username": "alex", "score": 0.3}],
               "embedding": [0.01] * 256}
    _, _, reply, redis = await _run(verdict, monkeypatch)
    assert reply.type == "transcript"
    value, _ttl = redis.values[voice_speaker.key("turn on the kitchen lights")]
    assert voice_speaker.decode(value) is None
    record = voice_speaker.decode_record(value)
    assert record["top"] == [{"username": "sam", "score": 0.5}, {"username": "alex", "score": 0.3}]
    assert record["speech_seconds"] == 1.9 and "embedding" not in value
    assert voice_speaker.candidate_key("turn on the kitchen lights") not in redis.values


@pytest.mark.asyncio
async def test_a_close_but_unrecognised_command_keeps_its_signature_briefly_for_a_candidate(monkeypatch):
    verdict = {"matched": False, "score": 0.66, "speech_seconds": 2.2, "model": "resemblyzer-ge2e-256",
               "candidates": [{"username": "sam", "score": 0.66}], "embedding": [0.01] * 256}
    _, _, _, redis = await _run(verdict, monkeypatch)
    value, ttl = redis.values[voice_speaker.candidate_key("turn on the kitchen lights")]
    assert ttl == voice_speaker.TTL_SECONDS
    assert voice_enroll.decode_take(value)["embedding"] == [0.01] * 256


@pytest.mark.asyncio
async def test_nothing_is_recorded_when_nobody_is_enrolled(monkeypatch):
    _, _, _, redis = await _run({"matched": False, "nobody_enrolled": True, "embedding": [0.01] * 256}, monkeypatch)
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
            "matched_username": "sam",
            "score": 0.91,
            "embedding": [0.01] * 256,
            "speech_seconds": 2.8,
        },
        monkeypatch,
        learning_for="sam",
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
        "matched_username": "sam",
        "score": 0.91,
        "embedding": [0.01] * 256,
        "speech_seconds": 2.8,
    })

    assert calls["url"].endswith("/api/voice/learn")
    assert calls["json"]["username"] == "sam"
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
        "matched_username": "sam",
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
    admin_by_voice = {"user": "alex", "is_admin": True, "identified_by": "voice"}
    assert not authorize_action(admin_by_voice, "lock", "unlock")
    assert not authorize_action(admin_by_voice, "cover", "open")
    assert authorize_action(admin_by_voice, "light", "turn_on")
    assert authorize_action({"user": "alex", "is_admin": True}, "lock", "unlock")
    # A satellite assigned to someone proves no more than a voice does.
    by_device = {"user": "alex", "is_admin": True, "identified_by": "device"}
    assert not authorize_action(by_device, "lock", "unlock")
    assert authorize_action(by_device, "light", "turn_on")


@pytest.mark.asyncio
async def test_a_take_is_kept_only_while_a_teaching_session_is_open(monkeypatch):
    verdict = {"matched": False, "score": 0.4, "embedding": [0.02] * 256, "speech_seconds": 3.0,
               "model": wyoming_stt.voiceprint.MODEL}
    _, _, _, redis = await _run(dict(verdict), monkeypatch)
    assert not [k for k in redis.values if k.startswith("voice:enroll")]

    original = _FakeRedis.__init__

    def with_session(self):
        original(self)
        self.values[voice_enroll.OPEN_KEY] = ("1", None)

    monkeypatch.setattr(_FakeRedis, "__init__", with_session)
    _, _, _, redis = await _run(dict(verdict), monkeypatch)
    raw, ttl = redis.values[voice_enroll.take_key("Turn on the kitchen lights.")]
    take = voice_enroll.decode_take(raw)
    assert take["embedding"] == [0.02] * 256 and take["model"] == wyoming_stt.voiceprint.MODEL
    assert ttl == voice_enroll.TAKE_TTL_SECONDS


@pytest.mark.asyncio
async def test_every_outcome_is_logged_at_info_without_the_signature(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="execution.wyoming_stt")
    await _run({"matched": False, "score": 0.71, "threshold": 0.8, "margin": 0.2,
                "candidates": [{"username": "sam", "score": 0.71}], "embedding": [0.123456] * 256,
                "speech_seconds": 2.4, "embed_ms": 410, "total_ms": 480}, monkeypatch)
    lines = [r.getMessage() for r in caplog.records if r.name == "execution.wyoming_stt"]
    assert any("not recognised: best sam 0.71 (threshold 0.8" in line and "embed 410 ms" in line for line in lines)
    assert not any("0.123456" in line for line in lines)


def test_the_logger_shows_info_even_when_the_root_does_not():
    assert wyoming_stt.log.getEffectiveLevel() <= logging.INFO


def test_describe_names_who_matched_and_how_long_it_took():
    line = wyoming_stt.describe({"matched": True, "matched_username": "sam", "score": 0.9, "threshold": 0.8,
                                 "margin": 0.2, "speech_seconds": 3.0, "embed_ms": 500, "total_ms": 560})
    assert line.startswith("recognised sam (score 0.9") and "total 560 ms" in line
    assert "nobody has enrolled" in wyoming_stt.describe({"matched": False, "nobody_enrolled": True})
    assert "no signature" in wyoming_stt.describe(None)


class _IdentityAnswer:
    def __init__(self, status, body=None):
        self.status = status
        self.body = body

    def raise_for_status(self):
        if self.status >= 400:
            raise RuntimeError(f"HTTP {self.status}")

    async def json(self):
        return self.body


def _identity_answering(monkeypatch, answer):
    class FakeSession:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, json=None, headers=None, timeout=None):
            return answer

    monkeypatch.setattr(wyoming_stt.aiohttp, "ClientSession", FakeSession)
    monkeypatch.setattr(wyoming_stt.voiceprint, "embed_samples", lambda samples, minimum: {
        "embedding": [0.01] * 256, "speech_seconds": 2.0, "model": wyoming_stt.voiceprint.MODEL})


@pytest.mark.asyncio
async def test_identify_keeps_the_signature_when_nobody_is_enrolled(monkeypatch):
    _identity_answering(monkeypatch, _IdentityAnswer(404))
    verdict = await wyoming_stt.identify(np.zeros(16000, dtype=np.float32))
    assert verdict["matched"] is False and verdict["nobody_enrolled"] is True
    assert verdict["embedding"] == [0.01] * 256 and verdict["total_ms"] >= 0


@pytest.mark.asyncio
async def test_identify_keeps_the_signature_when_identity_fails(monkeypatch):
    _identity_answering(monkeypatch, _IdentityAnswer(500))
    verdict = await wyoming_stt.identify(np.zeros(16000, dtype=np.float32))
    assert verdict["matched"] is False and "500" in verdict["error"]
    assert verdict["embedding"] == [0.01] * 256


@pytest.mark.asyncio
async def test_identify_passes_identitys_verdict_on(monkeypatch):
    _identity_answering(monkeypatch, _IdentityAnswer(200, {"matched": True, "matched_username": "sam", "score": 0.9}))
    verdict = await wyoming_stt.identify(np.zeros(16000, dtype=np.float32))
    assert verdict["matched_username"] == "sam" and verdict["speech_seconds"] == 2.0


# -- a voice nobody enrolled ------------------------------------------------------------------
_FAR = {"matched": False, "score": 0.41, "threshold": 0.8, "margin": 0.1, "speech_seconds": 2.6, "snr_db": 24.0,
        "model": "resemblyzer-ge2e-256", "embedding": [0.01] * 256,
        "candidates": [{"username": "sam", "score": 0.41}, {"username": "alex", "score": 0.31}]}


@pytest.mark.asyncio
async def test_a_voice_far_from_everyone_is_kept_briefly_as_a_possible_new_voice(monkeypatch):
    _, _, _, redis = await _run(dict(_FAR), monkeypatch)
    value, ttl = redis.values[voice_speaker.new_voice_key("turn on the kitchen lights")]
    assert ttl == voice_speaker.TTL_SECONDS and voice_enroll.decode_take(value)["embedding"] == [0.01] * 256


@pytest.mark.parametrize("change", [
    {"score": 0.86, "candidates": [{"username": "sam", "score": 0.86}, {"username": "alex", "score": 0.84}]},
    {"score": 0.6, "candidates": [{"username": "sam", "score": 0.6}]},  # could be sam, far from the mic
    {"speech_seconds": 1.4},
    {"snr_db": 6.0},
    {"matched": True, "matched_username": "sam"},
])
@pytest.mark.asyncio
async def test_unsure_close_short_or_noisy_commands_are_not_new_voices(monkeypatch, change):
    _, _, _, redis = await _run({**_FAR, **change}, monkeypatch)
    assert voice_speaker.new_voice_key("turn on the kitchen lights") not in redis.values


def test_the_new_voice_rule_needs_every_part():
    assert voice_speaker.looks_new(_FAR)
    assert not voice_speaker.looks_new({**_FAR, "threshold": 0.55})  # within NEW_VOICE_GAP of the threshold
    assert not voice_speaker.looks_new({**_FAR, "candidates": []})
    assert not voice_speaker.looks_new({**_FAR, "nobody_enrolled": True})
    assert not voice_speaker.looks_new({**_FAR, "embedding": None})
    assert voice_speaker.looks_new({k: v for k, v in _FAR.items() if k != "snr_db"})  # not measured: not held against it


@pytest.mark.asyncio
async def test_identify_reports_how_clean_the_speech_was(monkeypatch):
    _identity_answering(monkeypatch, _IdentityAnswer(200, {"matched": False, "score": 0.3}))
    t = np.arange(32000) / 16000
    samples = np.concatenate([np.zeros(8000), 0.3 * np.sin(2 * np.pi * 200 * t), np.zeros(8000)]).astype(np.float32)
    samples += np.random.default_rng(0).standard_normal(len(samples)).astype(np.float32) * 1e-4
    verdict = await wyoming_stt.identify(samples)
    assert verdict["snr_db"] is not None and verdict["snr_db"] > 40

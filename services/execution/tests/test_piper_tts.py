"""Piper voices over Wyoming: describe, synthesize, and the errors that say what is wrong."""
import asyncio
import io
import json
import wave

import pytest

from services.execution import piper_tts


def _event(kind, data=None, payload=b""):
    header = {"type": kind}
    body = json.dumps(data).encode() if data else b""
    if body:
        header["data_length"] = len(body)
    if payload:
        header["payload_length"] = len(payload)
    return json.dumps(header).encode() + b"\n" + body + payload


async def _serve(handler):
    async def on_connect(reader, writer):
        line = await reader.readline()
        header = json.loads(line)
        data = json.loads(await reader.readexactly(header["data_length"])) if header.get("data_length") else {}
        for chunk in handler(header["type"], data):
            writer.write(chunk)
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(on_connect, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


@pytest.fixture
def piper_at(monkeypatch):
    def point(port):
        monkeypatch.setattr(piper_tts, "SERVER", f"127.0.0.1:{port}")
    return point


def test_a_piper_voice_is_named_with_its_prefix():
    assert piper_tts.is_piper_voice("piper:en_US-test-medium")
    assert not piper_tts.is_piper_voice("af_heart")
    assert not piper_tts.is_piper_voice(None)
    assert piper_tts.voice_name("piper:en_US-test-medium") == "en_US-test-medium"


async def test_installed_voices_are_listed_with_the_prefix(piper_at):
    info = {"tts": [{"name": "piper", "voices": [
        {"name": "en_US-test-medium", "installed": True},
        {"name": "en_GB-other-low", "installed": False},
    ]}]}
    server, port = await _serve(lambda kind, data: [_event("info", info)] if kind == "describe" else [])
    piper_at(port)
    async with server:
        assert await piper_tts.list_voices() == ["piper:en_US-test-medium"]


async def test_synthesize_returns_a_wav_of_the_pcm_piper_sent(piper_at):
    seen = {}

    def handler(kind, data):
        seen.update(kind=kind, data=data)
        fmt = {"rate": 22050, "width": 2, "channels": 1}
        return [_event("audio-start", fmt), _event("audio-chunk", fmt, b"\x01\x00" * 100),
                _event("audio-chunk", fmt, b"\x02\x00" * 50), _event("audio-stop")]

    server, port = await _serve(handler)
    piper_at(port)
    async with server:
        audio = await piper_tts.synthesize("Good evening.", "piper:en_US-test-medium")
    assert seen == {"kind": "synthesize", "data": {"text": "Good evening.", "voice": {"name": "en_US-test-medium"}}}
    with wave.open(io.BytesIO(audio)) as wav:
        assert (wav.getframerate(), wav.getsampwidth(), wav.getnchannels(), wav.getnframes()) == (22050, 2, 1, 150)


async def test_an_error_from_piper_is_reported_with_its_words(piper_at):
    server, port = await _serve(lambda kind, data: [_event("error", {"text": "voice not found"})])
    piper_at(port)
    async with server:
        with pytest.raises(piper_tts.PiperError, match="voice not found"):
            await piper_tts.synthesize("Hi", "piper:missing")


async def test_unset_server_says_which_setting_is_missing(monkeypatch):
    monkeypatch.setattr(piper_tts, "SERVER", "")
    with pytest.raises(piper_tts.PiperError, match="PIPER_TTS"):
        await piper_tts.synthesize("Hi", "piper:en_US-test-medium")


async def test_a_server_that_is_down_is_named(piper_at):
    server, port = await _serve(lambda kind, data: [])
    server.close()
    await server.wait_closed()
    piper_at(port)
    with pytest.raises(piper_tts.PiperError, match="did not answer"):
        await piper_tts.list_voices()


async def test_a_piper_default_voice_speaks_through_piper_and_a_storybook_stays_kokoro(monkeypatch):
    from services import config
    from services.execution import tts

    monkeypatch.setattr(config, "DEFAULT_TTS_VOICE", "piper:en_US-test-medium")
    spoken, generated = [], []

    async def fake_piper(text, voice):
        spoken.append((text, voice))
        return b"PIPER"

    class FakeEngine:
        def _normalize_text(self, text):
            return text

        async def generate(self, text, voice, storybook=False):
            generated.append((voice, storybook))
            return b"KOKORO"

    monkeypatch.setattr(piper_tts, "synthesize", fake_piper)
    monkeypatch.setattr(tts, "get_tts_engine", lambda: FakeEngine())
    assert await tts.text_to_speech("Hello...500ms there") == b"PIPER"
    assert spoken[0][1] == "piper:en_US-test-medium" and "500ms" not in spoken[0][0]
    assert await tts.text_to_speech("Once upon a time", storybook=True) == b"KOKORO"
    assert generated == [(tts.KOKORO_FALLBACK_NARRATOR, True)]
    assert await tts.text_to_speech("Hi", voice="af_heart") == b"KOKORO"

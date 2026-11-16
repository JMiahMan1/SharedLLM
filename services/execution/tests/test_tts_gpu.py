"""Kokoro on alpaca's graphics card: used when set, and a reply it cannot take is spoken here."""
import base64

import pytest
from aiohttp import web

from services.execution import tts


class _Engine:
    def __init__(self):
        self.local = []

    def _mark_structure_pauses(self, text):
        return text.replace("Title. ", "Title." + tts._PAUSE_MARK + " ")

    def _normalize_text(self, text):
        return text

    async def generate(self, text, voice, storybook=False):
        self.local.append((text, voice, storybook))
        return b"LOCAL"


async def _audio_server(pick_port, handler):
    app = web.Application()
    app.router.add_post("/api/tts", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    port = pick_port()
    await web.TCPSite(runner, "127.0.0.1", port).start()
    return runner, f"http://127.0.0.1:{port}"


@pytest.fixture
def engine(monkeypatch):
    e = _Engine()
    monkeypatch.setattr(tts, "get_tts_engine", lambda: e)
    return e


@pytest.fixture
def unused_port():
    import socket

    def pick():
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        return port
    return pick


async def test_a_reply_is_spoken_on_the_card_with_its_pauses(engine, monkeypatch, unused_port):
    seen = {}

    async def handler(request):
        seen.update(await request.json())
        return web.json_response({"audio_b64": base64.b64encode(b"GPU").decode()})

    runner, url = await _audio_server(unused_port, handler)
    monkeypatch.setattr(tts, "KOKORO_GPU_URL", url)
    try:
        assert await tts.text_to_speech("Title. Good evening.", voice="bm_fable,bm_george") == b"GPU"
    finally:
        await runner.cleanup()
    assert seen["voice"] == "bm_fable,bm_george" and seen["normalize"] is False
    assert "\n\n" in seen["text"] and tts._PAUSE_MARK not in seen["text"]
    assert seen["paragraph_pause_s"] == tts.PAUSE_STRUCTURE
    assert engine.local == []


async def test_a_card_that_refuses_leaves_the_reply_to_this_host(engine, monkeypatch, unused_port):
    async def handler(request):
        return web.json_response({"error": "TTS model failed to load"}, status=503)

    runner, url = await _audio_server(unused_port, handler)
    monkeypatch.setattr(tts, "KOKORO_GPU_URL", url)
    try:
        assert await tts.text_to_speech("Hello", voice="af_heart") == b"LOCAL"
    finally:
        await runner.cleanup()
    assert engine.local == [("Hello", "af_heart", False)]


async def test_a_card_that_is_down_leaves_the_reply_to_this_host(engine, monkeypatch, unused_port):
    monkeypatch.setattr(tts, "KOKORO_GPU_URL", f"http://127.0.0.1:{unused_port()}")
    assert await tts.text_to_speech("Hello", voice="af_heart") == b"LOCAL"


async def test_long_passages_and_storybooks_stay_here(engine, monkeypatch):
    monkeypatch.setattr(tts, "KOKORO_GPU_URL", "http://gpu.example.test:8082")
    assert await tts.text_to_speech("word " * 1000, voice="af_heart") == b"LOCAL"
    assert await tts.text_to_speech("Once upon a time", voice="af_heart", storybook=True) == b"LOCAL"
    assert len(engine.local) == 2

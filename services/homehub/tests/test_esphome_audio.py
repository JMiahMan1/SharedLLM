"""ESPHome media players get audio in the format they ask for, through the hub's ffmpeg proxy (HA's ffmpeg_proxy)."""
from __future__ import annotations

import asyncio
import contextlib
import io
import math
import os
import shutil
import struct
import tempfile
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from aioesphomeapi import MediaPlayerFormatPurpose, MediaPlayerInfo, MediaPlayerSupportedFormat
from fastapi.testclient import TestClient

from services.homehub import esphome_audio, tts
from services.homehub.drivers import esphome
from services.homehub.drivers.esphome import ESPHomeDriver
from services.homehub.esphome_audio import AudioFormat, ProxyError, choose_format
from services.homehub.tests import test_drivers_esphome as fake
from services.homehub.tests.conftest import make_hub

HAVE_FFMPEG = shutil.which("ffmpeg") is not None
needs_ffmpeg = pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg is not installed here; the hub image has it "
                                  "(services/homehub/Dockerfile), so the conversion itself is checked there")
HUB = "http://hub.example.com:8123"
MP = "media_player.den_den_speaker_smart_speaker"
FLAC_48K_MONO = [
    MediaPlayerSupportedFormat(format="flac", sample_rate=48000, num_channels=1, sample_bytes=2,
                               purpose=MediaPlayerFormatPurpose.DEFAULT),
    MediaPlayerSupportedFormat(format="flac", sample_rate=48000, num_channels=1, sample_bytes=2,
                               purpose=MediaPlayerFormatPurpose.ANNOUNCEMENT),
]


def _wav(rate: int = 22050, seconds: float = 0.5) -> bytes:
    n = int(rate * seconds)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(struct.pack(f"<{n}h", *(int(6000 * math.sin(2 * math.pi * 440 * i / rate)) for i in range(n))))
    return buf.getvalue()


def _flac_streaminfo(data: bytes) -> tuple[int, int, int]:
    """(sample rate, channels, bits per sample) from a FLAC stream's STREAMINFO block."""
    assert data[:4] == b"fLaC", data[:16]
    info = data[8:8 + 34]
    rate = (info[10] << 12) | (info[11] << 4) | (info[12] >> 4)
    channels = ((info[12] >> 1) & 0x7) + 1
    bits = (((info[12] & 0x1) << 4) | (info[13] >> 4)) + 1
    return rate, channels, bits


@pytest.fixture(autouse=True)
def fresh_proxy(monkeypatch):
    esphome_audio.reset()
    monkeypatch.setattr(esphome_audio.PROXY, "_have_ffmpeg", True)  # a test decides about ffmpeg itself
    monkeypatch.setenv("HOMEHUB_EXTERNAL_URL", HUB)
    monkeypatch.setattr(esphome, "COMMAND_SETTLE", 0.05)  # the fake node pushes no media state back
    tts.reset()
    yield
    esphome_audio.reset()
    tts.reset()


def _driver(formats):
    """The fake node from the driver tests, its media player advertising ``formats``."""
    infos = [i if not isinstance(i, MediaPlayerInfo) else MediaPlayerInfo(
        object_id=i.object_id, key=i.key, name=i.name, supports_pause=True, feature_flags=i.feature_flags,
        supported_formats=list(formats)) for i in fake.INFOS]

    class Node(fake.FakeClient):
        async def device_info_and_list_entities(self):
            info, _, services = await super().device_info_and_list_entities()
            return info, list(infos), services

    hub, entry = make_hub("esphome", dict(fake.DATA), fake.ENTITIES, options={"allow_service_calls": True})
    drv = ESPHomeDriver(hub, entry)
    drv._make_client = lambda host: Node()  # type: ignore[method-assign]

    async def _no_mdns(host, timeout):
        return None

    drv._resolve_mdns = _no_mdns  # type: ignore[method-assign]
    hub.drivers[entry.entry_id] = drv
    return hub, drv


def _app(hub):
    from cryptography.fernet import Fernet

    from services.homehub.api import build_app
    from services.homehub.auth import Auth
    from services.homehub.runtime import Runtime
    from services.homehub.store import Store

    store = Store(tempfile.mktemp(suffix=".db"), Fernet.generate_key().decode())
    return TestClient(build_app(Runtime(hub, store), Auth(store, "s")))


# -- choosing the format ----------------------------------------------------------------
def test_the_choice_follows_home_assistant():
    d = AudioFormat("mp3", 44100, 2, 2, 0)
    a = AudioFormat("flac", 48000, 1, 2, 1)
    d2 = AudioFormat("wav", 16000, 1, 2, 0)
    assert choose_format([d, a, d2], announcement=False) == d
    assert choose_format([d, a, d2], announcement=True) == a
    assert choose_format([d, d2], announcement=True) == d      # no announcement format: the default one
    assert choose_format([a], announcement=False) is None       # only announcements: keep the URL
    assert choose_format([], announcement=True) is None


def test_ffmpeg_arguments_match_the_format():
    args = AudioFormat("flac", 48000, 1, 2, 1).ffmpeg_output_args()
    assert args[args.index("-ar") + 1] == "48000" and args[args.index("-ac") + 1] == "1"
    assert args[args.index("-sample_fmt") + 1] == "s16" and args[-2:] == ["-f", "flac"]
    assert "-map_metadata" in args and "-vn" in args
    wav = AudioFormat("wav", 16000, 1, 2).ffmpeg_output_args()
    assert wav[wav.index("-c:a") + 1] == "pcm_s16le"
    mp3 = AudioFormat("mp3", 44100, 2, 2).ffmpeg_output_args()
    assert "-sample_fmt" not in mp3  # libmp3lame takes no packed s16; ffmpeg picks its own


# -- the driver hands the node proxy URLs -------------------------------------------------
async def test_announce_gets_the_flac_48k_proxy_url_and_formats_are_attributes():
    hub, drv = _driver(FLAC_48K_MONO)
    await drv.async_setup()
    attrs = hub.states.get(MP).attributes
    assert attrs["supported_formats"] == [
        {"format": "flac", "sample_rate": 48000, "num_channels": 1, "sample_bytes": 2, "purpose": "default"},
        {"format": "flac", "sample_rate": 48000, "num_channels": 1, "sample_bytes": 2, "purpose": "announcement"}]
    await hub.call_service("media_player", "play_media", {"entity_id": MP, "media_content_id": "http://x/a.mp3?k=s",
                                                          "media_content_type": "music", "announce": True})
    _, args, kw = drv._client.commands[-1]
    url = kw["media_url"]
    assert url.startswith(f"{HUB}/api/esphome/ffmpeg_proxy/{drv.entry.entry_id}/") and url.endswith(".flac")
    assert "x/a.mp3" not in url and "k=s" not in url  # the source (and any credential in it) stays on the hub
    assert kw["announcement"] is True and args == (8,)
    conv = esphome_audio.PROXY.lookup(drv.entry.entry_id, url.rsplit("/", 1)[1])
    assert conv is not None and conv.fmt.purpose == 1 and conv.source_url == "http://x/a.mp3?k=s"

    # Music too (default purpose), and an enqueued URL.
    await hub.call_service("media_player", "play_media", {"entity_id": MP, "media_content_id": "https://ma/s.flac",
                                                          "media_content_type": "music"})
    assert "/api/esphome/ffmpeg_proxy/" in drv._client.commands[-1][2]["media_url"]
    await hub.call_service("media_player", "play_media", {"entity_id": MP, "media_content_id": "https://ma/n.flac",
                                                          "media_content_type": "music", "enqueue": "add"})
    assert "/api/esphome/ffmpeg_proxy/" in drv._client.commands[-1][2]["media_url"]
    await drv.async_unload()


async def test_urls_that_stay_direct():
    hub, drv = _driver(FLAC_48K_MONO)
    await drv.async_setup()

    async def play(url, **extra):
        await hub.call_service("media_player", "play_media", {"entity_id": MP, "media_content_id": url,
                                                              "media_content_type": "music", **extra})
        return drv._client.commands[-1][2]["media_url"]

    assert await play("http://x/a.mp3", extra={"bypass_proxy": True}) == "http://x/a.mp3"  # HA's escape hatch
    assert await play("file:///etc/hostname") == "file:///etc/hostname"  # ffmpeg never reads hub files
    proxied = await play("http://x/b.mp3")
    assert await play(proxied) == proxied  # never wrapped twice
    esphome_audio.PROXY._have_ffmpeg = False
    assert await play("http://x/c.mp3") == "http://x/c.mp3"  # no ffmpeg: the URL as it was
    await drv.async_unload()


async def test_a_node_with_no_formats_keeps_the_direct_url():
    hub, drv = _driver([])
    await drv.async_setup()
    assert "supported_formats" not in hub.states.get(MP).attributes
    await hub.call_service("media_player", "play_media", {"entity_id": MP, "media_content_id": "http://x/a.mp3",
                                                          "media_content_type": "music", "announce": True})
    assert drv._client.commands[-1][2]["media_url"] == "http://x/a.mp3"
    await drv.async_unload()


async def test_an_unknown_codec_keeps_the_direct_url():
    hub, drv = _driver([MediaPlayerSupportedFormat(format="aac", sample_rate=48000, num_channels=2,
                                                   purpose=MediaPlayerFormatPurpose.DEFAULT)])
    await drv.async_setup()
    await hub.call_service("media_player", "play_media", {"entity_id": MP, "media_content_id": "http://x/a.mp3",
                                                          "media_content_type": "music"})
    assert drv._client.commands[-1][2]["media_url"] == "http://x/a.mp3"
    await drv.async_unload()


async def test_satellite_announcements_use_the_announcement_format(monkeypatch):
    from services.homehub.core import EntityEntry

    hub, drv = _driver(FLAC_48K_MONO)
    sat = EntityEntry(entity_id="assist_satellite.den_speaker", platform="esphome",
                      unique_id=f"{fake.MAC}-assist_satellite", config_entry_id=drv.entry.entry_id, device_id="dev1")
    hub.entities[sat.entity_id] = sat
    await drv.async_setup()
    announced = []

    async def announce(media_id, timeout, text="", preannounce_media_id="", start_conversation=False):
        announced.append((media_id, preannounce_media_id))

    drv._client.send_voice_assistant_announcement_await_response = announce

    async def clip(message, voice=None):
        return f"{HUB}/api/tts_proxy/abcdefghijklmnop.wav"

    monkeypatch.setattr(tts, "make_clip", clip)
    await hub.call_service("assist_satellite", "announce", {"entity_id": sat.entity_id, "message": "Dinner",
                                                            "preannounce_media_id": "http://x/chime.mp3"})
    media, pre = announced[-1]
    assert "/api/esphome/ffmpeg_proxy/" in media and media.endswith(".flac")
    assert "/api/esphome/ffmpeg_proxy/" in pre
    await drv.async_unload()


# -- the proxy route ----------------------------------------------------------------------------
@needs_ffmpeg
async def test_a_spoken_clip_is_converted_once_to_flac_48k_mono():
    hub, drv = _driver(FLAC_48K_MONO)
    await drv.async_setup()
    tts._clips["clipTOKEN0123456789abcd"] = (time.monotonic() + 600, _wav(22050))
    await hub.call_service("media_player", "play_media", {
        "entity_id": MP, "media_content_id": f"{HUB}/api/tts_proxy/clipTOKEN0123456789abcd.wav",
        "media_content_type": "music", "announce": True})
    url = drv._client.commands[-1][2]["media_url"]
    path = url.removeprefix(HUB)
    spawned = []
    real_spawn = esphome_audio.PROXY._spawn

    async def counting(args, *, stdin):
        spawned.append(args)
        return await real_spawn(args, stdin=stdin)

    esphome_audio.PROXY._spawn = counting  # type: ignore[method-assign]
    try:
        client = _app(hub)
        r1 = client.get(path)
        r2 = client.get(path)  # the node fetches a reply twice
    finally:
        del esphome_audio.PROXY._spawn
    assert r1.status_code == 200 and r1.headers["content-type"] == "audio/flac"
    assert r1.headers["cache-control"] == "no-store"
    assert _flac_streaminfo(r1.content) == (48000, 1, 16)
    assert r2.content == r1.content and len(spawned) == 1
    assert "pipe:0" in spawned[0]  # from the clip in memory, not over HTTP
    # The 22050 Hz original is untouched for every other kind of player.
    orig = client.get("/api/tts_proxy/clipTOKEN0123456789abcd.wav")
    assert orig.headers["content-type"] == "audio/wav" and orig.content == tts._clips["clipTOKEN0123456789abcd"][1]
    assert esphome_audio.PROXY.running() == 0
    await drv.async_unload()


class _Source(BaseHTTPRequestHandler):
    wav = b""
    endless = False

    def log_message(self, *_a):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.end_headers()
        with contextlib.suppress(OSError):
            if not self.endless:
                self.wfile.write(self.wav)
                return
            # A header that promises hours, then noise (FLAC cannot shrink it) for as long as anyone listens.
            header = bytearray(self.wav[:44])
            header[4:8] = struct.pack("<I", 0x7FFFFFF0)
            header[40:44] = struct.pack("<I", 0x7FFFFFC0)
            self.wfile.write(bytes(header))
            while True:
                self.wfile.write(os.urandom(44100))
                self.wfile.flush()
                time.sleep(0.05)


@pytest.fixture
def source():
    _Source.wav = _wav(22050, 1.0)
    _Source.endless = False
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Source)
    srv.daemon_threads = True
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/reply.wav?token=secret"
    srv.shutdown()
    srv.server_close()


@needs_ffmpeg
def test_a_url_streams_as_valid_flac_48k_mono(source):
    hub, _ = make_hub()
    fmt = AudioFormat("flac", 48000, 1, 2, 1)
    url = esphome_audio.PROXY.create("entry1", source, fmt, HUB)
    client = _app(hub)
    r = client.get(url.removeprefix(HUB))
    assert r.status_code == 200 and r.headers["content-type"] == "audio/flac"
    assert _flac_streaminfo(r.content) == (48000, 1, 16)
    assert esphome_audio.PROXY.running() == 0
    # Another device, a wrong extension or a made-up token are all unknown.
    name = url.rsplit("/", 1)[1]
    assert client.get(f"/api/esphome/ffmpeg_proxy/other/{name}").status_code == 404
    assert client.get(url.removeprefix(HUB).replace(".flac", ".mp3")).status_code == 404
    assert client.get("/api/esphome/ffmpeg_proxy/entry1/AAAAAAAAAAAAAAAAAAAAAAAA.flac").status_code == 404


@needs_ffmpeg
def test_a_source_that_cannot_be_read_is_a_502_without_the_url():
    hub, _ = make_hub()
    # Nothing listens on the discard port, so ffmpeg fails at once.
    url = esphome_audio.PROXY.create("entry1", "http://127.0.0.1:9/x.wav?token=secret",
                                     AudioFormat("flac", 48000, 1), HUB)
    r = _app(hub).get(url.removeprefix(HUB))
    assert r.status_code == 502 and "secret" not in r.text and "127.0.0.1" not in r.text
    assert esphome_audio.PROXY.running() == 0


def test_tokens_expire():
    hub, _ = make_hub()
    url = esphome_audio.PROXY.create("entry1", "http://x/a.mp3", AudioFormat("flac", 48000, 1), HUB)
    name = url.rsplit("/", 1)[1]
    conv = esphome_audio.PROXY.lookup("entry1", name)
    assert conv is not None
    conv.expires = time.monotonic() - 1
    assert esphome_audio.PROXY.lookup("entry1", name) is None
    assert _app(hub).get(url.removeprefix(HUB)).status_code == 404
    assert conv.token not in esphome_audio.PROXY._conversions  # pruned


def test_a_device_keeps_a_bounded_number_of_tokens():
    first = esphome_audio.PROXY.create("entry1", "http://x/0.mp3", AudioFormat("flac", 48000, 1), HUB)
    for i in range(esphome_audio.MAX_PER_DEVICE):
        esphome_audio.PROXY.create("entry1", f"http://x/{i + 1}.mp3", AudioFormat("flac", 48000, 1), HUB)
    assert esphome_audio.PROXY.lookup("entry1", first.rsplit("/", 1)[1]) is None
    assert sum(c.device == "entry1" for c in esphome_audio.PROXY._conversions.values()) == esphome_audio.MAX_PER_DEVICE


@needs_ffmpeg
async def test_ffmpeg_ends_when_the_node_hangs_up_stops_or_fetches_again(source, monkeypatch):
    _Source.endless = True
    proxy = esphome_audio.PROXY
    fmt = AudioFormat("flac", 48000, 1, 2)

    async def started():
        conv = proxy.lookup("entry1", proxy.create("entry1", source, fmt, HUB).rsplit("/", 1)[1])
        body = await proxy.open_stream(conv)
        assert (await body.__anext__())[:4] == b"fLaC"
        return conv, body

    # The node hangs up: the response body is closed.
    conv, body = await started()
    proc = conv.proc
    assert proxy.running() == 1 and proc.returncode is None
    await body.aclose()
    assert proc.returncode is not None and proxy.running() == 0 and conv.proc is None

    # The device is told to stop (media_stop / turn_off / disconnect): its conversions end.
    conv, body = await started()
    proc = conv.proc
    proxy.stop_device("entry1")
    with contextlib.suppress(StopAsyncIteration):
        async with asyncio.timeout(5):
            async for _ in body:
                pass
    assert proc.returncode is not None and proxy.running() == 0

    # Fetching the same token again stops the earlier conversion (HA does the same).
    conv, body = await started()
    old = conv.proc
    again = await proxy.open_stream(conv)
    await again.__anext__()
    await asyncio.wait_for(old.wait(), 5)
    assert old.returncode is not None and conv.proc is not old
    await body.aclose()
    await again.aclose()
    assert proxy.running() == 0

    # At most max_procs at once; one more waits briefly, then is refused.
    monkeypatch.setattr(proxy, "max_procs", 1)
    monkeypatch.setattr(esphome_audio, "SLOT_WAIT_S", 0.2)
    proxy._slots = None
    conv, body = await started()
    other = proxy.lookup("entry1", proxy.create("entry1", source, fmt, HUB).rsplit("/", 1)[1])
    with pytest.raises(ProxyError) as exc:
        await proxy.open_stream(other)
    assert exc.value.status == 503
    await body.aclose()
    assert proxy.running() == 0


async def test_the_driver_stops_conversions_on_stop_and_disconnect(monkeypatch):
    hub, drv = _driver(FLAC_48K_MONO)
    await drv.async_setup()
    stopped = []
    monkeypatch.setattr(esphome_audio.PROXY, "stop_device", lambda device: stopped.append(device))
    await hub.call_service("media_player", "media_stop", {"entity_id": MP})
    await hub.call_service("media_player", "turn_off", {"entity_id": MP})
    await drv._client.on_stop(False)
    assert stopped == [drv.entry.entry_id] * 3
    await drv.async_unload()


def test_execution_can_ask_the_hub_for_a_proxy_url():
    """Execution talks to some nodes itself (alarms, timers); the hub hands it a proxy URL for them."""
    hub, _ = make_hub()
    client = _app(hub)
    body = {"device": "exec-den", "url": "http://jarvis.example.com:8888/media/a.wav", "announcement": True,
            "formats": [{"format": "flac", "sample_rate": 48000, "num_channels": 1, "sample_bytes": 2, "purpose": 1}]}
    assert client.post("/hub/esphome/audio_url", json=body).status_code == 401
    r = client.post("/hub/esphome/audio_url", json=body, headers={"X-Internal-Secret": "s"})
    out = r.json()
    assert r.status_code == 200 and out["proxied"] is True
    assert out["url"].startswith(f"{HUB}/api/esphome/ffmpeg_proxy/exec-den/") and out["url"].endswith(".flac")
    conv = esphome_audio.PROXY.lookup("exec-den", out["url"].rsplit("/", 1)[1])
    assert conv.fmt.sample_rate == 48000 and conv.fmt.purpose == 1
    direct = client.post("/hub/esphome/audio_url", json={**body, "formats": []}, headers={"X-Internal-Secret": "s"})
    assert direct.json() == {"url": body["url"], "proxied": False}
    bad = client.post("/hub/esphome/audio_url", json={**body, "device": "../x"}, headers={"X-Internal-Secret": "s"})
    assert bad.status_code == 400


@needs_ffmpeg
async def test_a_stream_never_read_still_gives_back_its_ffmpeg_and_its_place(source, monkeypatch):
    """open_stream holds a place and an ffmpeg before the body is iterated: closing before the first chunk,
    or a request cancelled before its response sends anything, must release both."""
    _Source.endless = True
    proxy = esphome_audio.PROXY
    monkeypatch.setattr(proxy, "max_procs", 1)
    monkeypatch.setattr(esphome_audio, "SLOT_WAIT_S", 0.2)
    proxy._slots = None
    fmt = AudioFormat("flac", 48000, 1, 2)

    def new_conv():
        return proxy.lookup("entry1", proxy.create("entry1", source, fmt, HUB).rsplit("/", 1)[1])

    # Closed before anyone read it.
    conv = new_conv()
    stream = await proxy.open_stream(conv)
    proc = conv.proc
    await stream.aclose()
    assert proc.returncode is not None and proxy.running() == 0 and conv.proc is None
    await stream.aclose()  # twice is harmless: the place is given back once

    # The response is cancelled before it sends a byte (the node hung up as the request started).
    conv = new_conv()
    stream = await proxy.open_stream(conv)
    proc = conv.proc
    response = esphome_audio.stream_response(stream, fmt.content_type, {})

    async def send(_message):
        raise asyncio.CancelledError

    async def receive():
        await asyncio.sleep(3600)
        return {"type": "http.disconnect"}

    with pytest.raises(asyncio.CancelledError):
        await response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)
    assert stream.closed and proxy.running() == 0 and conv.proc is None
    await asyncio.wait_for(proc.wait(), 5)

    # The one place is free again: a new stream starts instead of a 503.
    conv = new_conv()
    stream = await proxy.open_stream(conv)
    assert proxy.running() == 1
    await stream.aclose()
    assert proxy.running() == 0

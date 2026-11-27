"""Announcements speak over what plays and put the speaker back: announce: true, the clip's real length.

Before: every handler called volume_set and then a plain play_media, so the announcement replaced what
was playing and the speaker stayed at the announcement volume for good (timer alerts at 0.7, the
direct Cast route too). /execute/announce restored after a fixed 10 s whatever the clip's length.
"""
from __future__ import annotations

import asyncio
import struct
from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import AsyncMock, patch

import pytest

from services.common.audio_clip import byte_range, wav_duration
from services.execution import announce_clips
from services.execution import announce_handlers as ah

CLIP_ID = "tts-0a1b2c3d"
CLIP_URL = f"http://192.0.2.205:8888/media/{CLIP_ID}?user=u&mt=t"


def wav(seconds: float, rate: int = 8000) -> bytes:
    body = b"\x00\x00" * int(rate * seconds)
    fmt = struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
    return b"RIFF" + struct.pack("<I", 36 + len(body)) + b"WAVEfmt " + fmt + b"data" + struct.pack("<I", len(body)) + body


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    announce_clips.reset()
    delays: list[float] = []

    async def no_wait(seconds):
        delays.append(seconds)
        await asyncio.sleep(0)

    monkeypatch.setattr(announce_clips, "_sleep", no_wait)
    yield delays
    announce_clips.reset()


class FakeHA:
    def __init__(self, volume: float = 0.3, ok: bool = True, states: dict | None = None):
        self.calls: list[tuple] = []
        self.volume = volume
        self.ok = ok
        self.states = states or {}

    async def call_service(self, ha_url, ha_token, domain, service, entity_id=None, data=None, **kw):
        self.calls.append((domain, service, entity_id, data))
        if service == "volume_set":
            self.volume = data["volume_level"]
        if service in ("play_media", "play_announcement"):
            return {"ok": self.ok}
        return {"ok": True}

    async def get_state(self, ha_url, ha_token, entity_id):
        return self.states.get(entity_id) or {"state": "idle", "attributes": {"volume_level": self.volume}}

    async def get_all_states(self, ha_url, ha_token):
        return list(self.states.values())

    async def get_services(self, ha_url, ha_token):
        return []

    def of(self, service):
        return [c for c in self.calls if c[1] == service]


@pytest.fixture
def ha(monkeypatch):
    fake = FakeHA()
    for name in ("call_service", "get_state", "get_all_states", "get_services"):
        monkeypatch.setattr(f"services.execution.ha_client.{name}", getattr(fake, name))
    monkeypatch.setattr(ah, "routing_index", AsyncMock(return_value=None))
    return fake


async def settle():
    for _ in range(5):
        await asyncio.sleep(0)


# -- the clip's length -----------------------------------------------------------------------------

def test_wav_duration_reads_the_header():
    assert wav_duration(wav(2.5)) == pytest.approx(2.5)
    assert wav_duration(b"not a wav") is None
    streamed = bytearray(wav(1.0))
    streamed[40:44] = struct.pack("<I", 0xFFFFFFFF)  # a streaming encoder's unknown data size
    assert wav_duration(bytes(streamed)) == pytest.approx(1.0)


def test_byte_range_parsing():
    assert byte_range(None, 10) is None
    assert byte_range("bytes=2-5", 10) == (2, 5)
    assert byte_range("bytes=7-", 10) == (7, 9)
    assert byte_range("bytes=-4", 10) == (6, 9)
    assert byte_range("bytes=0-1,4-5", 10) is None  # multi-range: whole body
    with pytest.raises(ValueError):
        byte_range("bytes=10-", 10)


def test_a_cached_clip_is_looked_up_by_its_url():
    announce_clips.remember(CLIP_ID, wav(3.0))
    assert announce_clips.seconds_for_url(CLIP_URL) == pytest.approx(3.0)
    assert announce_clips.restore_delay(CLIP_URL) == pytest.approx(3.0 + announce_clips.RESTORE_MARGIN_S)
    assert announce_clips.restore_delay("http://elsewhere/x.wav") == (
        announce_clips.DEFAULT_CLIP_SECONDS + announce_clips.RESTORE_MARGIN_S)


def test_the_media_cache_records_each_clips_length():
    import services.execution.main as exec_main

    with patch.object(exec_main, "TEMP_AUDIO_CACHE", {}):
        exec_main.cache_temp_audio(CLIP_ID, wav(1.5))
    assert announce_clips.seconds_for_url(CLIP_URL) == pytest.approx(1.5)


# -- the handlers -----------------------------------------------------------------------------------

async def test_cast_announces_and_puts_the_volume_back_after_the_clip(ha, _clean):
    announce_clips.remember(CLIP_ID, wav(4.0))
    res = await ah.announce_cast("http://ha", "t", "media_player.den_speaker", CLIP_URL, 0.7)
    assert res["ok"] and res["target"] == "media_player.den_speaker"
    play = ha.of("play_media")[0][3]
    assert play["announce"] is True and play["media_content_id"] == CLIP_URL
    assert ha.of("volume_set")[0][3] == {"volume_level": 0.7}
    await settle()
    assert ha.of("volume_set")[-1][3] == {"volume_level": 0.3}
    assert _clean == [pytest.approx(4.0 + announce_clips.RESTORE_MARGIN_S)]


@pytest.mark.parametrize("handler", [ah.announce_esphome, ah.announce_speaker])
async def test_every_speaker_announcement_is_an_announcement(ha, handler):
    await handler("http://ha", "t", "media_player.kitchen_satellite", CLIP_URL, 0.7)
    await settle()
    assert ha.of("play_media")[0][3]["announce"] is True
    assert [c[3]["volume_level"] for c in ha.of("volume_set")] == [0.7, 0.3]


async def test_music_assistant_gets_announce_volume_not_a_volume_change(ha):
    res = await ah.announce_music_assistant("http://ha", "t", "media_player.porch_ma", CLIP_URL, 0.7)
    assert res["ok"]
    assert ha.of("volume_set") == []
    assert ha.of("play_media")[0][3] == {"media_content_id": CLIP_URL, "media_content_type": "url",
                                         "announce": True, "extra": {"announce_volume": 70}}


async def test_back_to_back_announcements_keep_the_volume_from_before_the_first(ha, monkeypatch):
    gate = asyncio.Event()

    async def held(seconds):
        await gate.wait()

    monkeypatch.setattr(announce_clips, "_sleep", held)
    await ah.announce_cast("http://ha", "t", "media_player.den_speaker", CLIP_URL, 0.7)
    await ah.announce_cast("http://ha", "t", "media_player.den_speaker", CLIP_URL, 0.7)
    gate.set()
    await settle()
    # The second announcement reads 0.7 on the speaker; the one restore still goes back to 0.3.
    assert [c[3]["volume_level"] for c in ha.of("volume_set")] == [0.7, 0.7, 0.3]


async def test_a_clip_that_did_not_play_puts_the_volume_back_at_once(ha, _clean):
    ha.ok = False
    res = await ah.announce_speaker("http://ha", "t", "media_player.den_speaker", CLIP_URL, 0.7)
    assert not res["ok"]
    await settle()
    assert ha.of("volume_set")[-1][3] == {"volume_level": 0.3}
    assert _clean == [0.0]


async def test_no_change_no_restore(ha):
    ha.volume = 0.7
    await ah.announce_cast("http://ha", "t", "media_player.den_speaker", CLIP_URL, 0.7)
    await settle()
    assert ha.of("volume_set") == []


async def test_webos_announces_on_its_music_assistant_sibling_first(ha, monkeypatch):
    monkeypatch.setattr(ah, "resolve_tv_companions",
                        lambda *a, **k: {"ma_sibling": "media_player.lounge_tv_airplay"})
    res = await ah.announce_webos("http://ha", "t", "media_player.lounge_tv", CLIP_URL, 0.6, state="on",
                                  message="Dinner")
    assert res["ok"] and res["target"] == "media_player.lounge_tv_airplay"
    ann = ha.of("play_announcement")
    assert ann[0][2] == "media_player.lounge_tv_airplay" and ann[0][3]["announce_volume"] == 60
    assert not [c for c in ha.of("play_media") if c[2] == "media_player.lounge_tv"]
    assert ha.of("volume_set") == []


# -- verify_playback --------------------------------------------------------------------------------

async def _verify(monkeypatch, states, device_type, timeout=1):
    import services.execution.main as exec_main

    seq = iter(states)
    last = {"s": None}

    async def get_state(ha_url, ha_token, entity_id):
        last["s"] = next(seq, last["s"])
        return last["s"]

    monkeypatch.setattr(exec_main.ha_client, "get_state", get_state)
    monkeypatch.setattr(exec_main.asyncio, "sleep", AsyncMock())
    with patch("time.time", side_effect=[0.0] + [0.1 * i for i in range(1, 20)] + [99.0] * 50):
        return await exec_main.verify_playback("http://ha", "t", "media_player.x", CLIP_URL, timeout=timeout,
                                               device_type=device_type)


async def test_a_tv_that_is_merely_on_is_not_proof_of_playback(monkeypatch):
    out = await _verify(monkeypatch, [{"state": "on", "attributes": {}}], "webos")
    assert out["verified"] is False


async def test_esphome_is_verified_by_going_to_playing_without_the_url(monkeypatch):
    out = await _verify(monkeypatch, [{"state": "idle", "attributes": {}},
                                      {"state": "playing", "attributes": {}}], "esphome")
    assert out["verified"] is True


async def test_a_cast_device_needs_the_url(monkeypatch):
    assert (await _verify(monkeypatch, [{"state": "playing", "attributes": {"media_content_id": "other"}}],
                          "cast"))["verified"] is False
    assert (await _verify(monkeypatch, [{"state": "playing", "attributes": {"media_content_id": CLIP_URL}}],
                          "cast"))["verified"] is True


# -- the direct Cast route (hub down) ---------------------------------------------------------------

def test_direct_cast_puts_the_volume_back_after_the_clip():
    from services.execution import direct_announce

    calls: list = []
    status = SimpleNamespace(player_state="PLAYING", content_id=CLIP_URL)

    class MC:
        def __init__(self):
            self.status = status

        def play_media(self, url, mime):
            calls.append(("play", url))

        def block_until_active(self, timeout=None):
            pass

    cc = SimpleNamespace(status=SimpleNamespace(volume_level=0.25), media_controller=MC(), name="Den speaker",
                         set_volume=lambda v: calls.append(("volume", v)),
                         disconnect=lambda timeout=None: calls.append(("disconnect",)))
    slept: list[float] = []

    def sleep(s):
        slept.append(s)
        status.player_state = "IDLE"

    ok, _detail = direct_announce._cast_play_sync({"host": "192.0.2.9"}, CLIP_URL, 0.7, 5.0,
                                                  connect=lambda dev: cc, sleep=sleep)
    assert ok
    assert calls == [("volume", 0.7), ("play", CLIP_URL), ("volume", 0.25), ("disconnect",)]


# -- /execute/announce ------------------------------------------------------------------------------

class _RecordingSleep:
    """An asyncio.sleep stand-in that returns at once and records (task, seconds)."""

    def __init__(self):
        self.calls: list = []

    async def __call__(self, secs, *args, **kwargs):
        self.calls.append((asyncio.current_task(), secs))


_own_task_sleep = _RecordingSleep()


async def test_execute_announce_waits_the_clips_real_length_not_ten_seconds():
    from services.execution.main import execute_announce
    from services.execution.schemas import AnnouncementRequest, UserContext

    me = asyncio.current_task()
    _own_task_sleep.calls.clear()
    ctx = UserContext(user="test", is_admin=True, ha_url="http://ha.local:8123", ha_token="t")
    req = AnnouncementRequest(user_context=ctx, entity_id="media_player.den_speaker", message="Hi", volume=0.5)
    playing = {"state": "playing", "attributes": {"friendly_name": "Den Speaker", "volume_level": 0.5}}

    class Resp:
        status = 200
        headers: ClassVar[dict] = {}

        async def read(self):
            return b"x"

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, timeout=None):
            return Resp()

    with patch("services.execution.main.ha_client.get_state", AsyncMock(return_value=playing)), \
         patch("services.execution.main.ha_client.call_service", AsyncMock(return_value={"ok": True})), \
         patch("services.execution.main.ha_client.get_config", AsyncMock(return_value={"components": []})), \
         patch("services.execution.main.ha_client.get_logbook", AsyncMock(return_value=[])), \
         patch("services.execution.main.asyncio.sleep", new=_own_task_sleep), \
         patch("services.execution.main.text_to_speech", AsyncMock(return_value=wav(3.0))), \
         patch("services.execution.main.TEMP_AUDIO_CACHE", {}), \
         patch("services.execution.main.get_client", lambda: Client()), \
         patch("services.execution.main.verify_playback", AsyncMock(return_value={"verified": True, "detail": ""})), \
         patch("services.execution.main.detect_tv_type", return_value="speaker"), \
         patch("services.execution.announce_handlers.dispatch_announce", AsyncMock(return_value={"ok": True})), \
         patch("services.execution.main.get_public_host", return_value="192.0.2.205"):
        await execute_announce(req)
    # Only this announcement's own waits: a background loop another test left running in the
    # same event loop sleeps through the same patched asyncio.sleep (10 s polls in CI).
    waits = [secs for task, secs in _own_task_sleep.calls if task is me]
    assert pytest.approx(3.0 + announce_clips.RESTORE_MARGIN_S) in waits
    assert 10 not in waits

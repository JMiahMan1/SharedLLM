"""The hold music setting: the game-show clip, the built-in tune and an uploaded clip, each made the stream's
rate, the same loudness and a seamless loop; a clip that cannot play falls back to the built-in tune."""
from __future__ import annotations

import io
import json
import logging
import math
import shutil
import subprocess
import wave
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.homehub import hold_music, voice_api, voice_pipeline
from services.homehub.auth import Auth
from services.homehub.tests.conftest import make_hub
from services.homehub.tests.test_voice_hold_music import AUDIO, Music, quick_hold
from services.homehub.tests.test_voice_pipeline import (  # fixtures and stand-ins, not tests
    env,
    loudness_alone,
    make_driver,
    run_turn,
    settings_from_the_test,
)

__all__ = ["env", "loudness_alone", "quick_hold", "settings_from_the_test"]

SECRET = "s" * 32
HAS_FFMPEG = shutil.which("ffmpeg") is not None


class KV:
    """The store's key-value half, in memory."""

    def __init__(self):
        self.kv: dict = {}

    def get_kv(self, key, default=None):
        return self.kv.get(key, default)

    def set_kv(self, key, value):
        self.kv[key] = json.loads(json.dumps(value))


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(hold_music, "_CACHE", {})
    monkeypatch.setattr(hold_music, "_WARNED", set())
    monkeypatch.setattr(voice_pipeline, "_HOLD_MUSIC", {})


@pytest.fixture
def hub(tmp_path):
    h, _entry = make_hub()
    h.store = KV()
    h.data_dir = str(tmp_path)
    return h


def wav(x: np.ndarray, rate: int, *, channels: int = 1, width: int = 2) -> bytes:
    """Float samples (-1..1) as a WAV; ``channels`` copies, ``width`` bytes a sample."""
    frames = np.repeat(x[:, None], channels, axis=1).reshape(-1)
    if width == 1:
        raw = np.clip(np.round(frames * 127 + 128), 0, 255).astype(np.uint8).tobytes()
    elif width == 2:
        raw = np.clip(np.round(frames * 32767), -32768, 32767).astype("<i2").tobytes()
    else:  # 24-bit
        v = np.clip(np.round(frames * (2 ** 23 - 1)), -(2 ** 23), 2 ** 23 - 1).astype(np.int32)
        raw = np.stack([v & 0xFF, (v >> 8) & 0xFF, (v >> 16) & 0xFF], axis=1).astype(np.uint8).tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(raw)
    return buf.getvalue()


def music(seconds: float, rate: int, amp: float = 0.3) -> np.ndarray:
    """A stand-in clip: two tones whose periods do not fit the clip, so looping it as it is would click."""
    t = np.arange(int(seconds * rate)) / rate
    return amp * (0.6 * np.sin(2 * np.pi * 220.3 * t) + 0.4 * np.sin(2 * np.pi * 331.7 * t))


def dbfs(pcm: bytes) -> float:
    x = np.frombuffer(pcm, "<i2").astype(float)
    return 20 * math.log10(math.sqrt(float(np.mean(x ** 2))) / 32767)


def seam_ok(pcm: bytes) -> bool:
    """Played back to back, the step from the last sample to the first is no bigger than the music's own."""
    x = np.frombuffer(pcm, "<i2").astype(float)
    steps = np.abs(np.diff(np.concatenate([x, x[:1]])))
    return bool(steps[-1] <= np.percentile(steps, 99.9))


# -- decoding, resampling, loudness, the loop ---------------------------------------------------------------
@pytest.mark.parametrize(("channels", "width"), [(1, 2), (2, 2), (1, 1), (2, 3)])
def test_a_wav_of_any_common_shape_decodes_to_mono(channels, width):
    x = music(1.0, 16000)
    y, rate = hold_music.read_wav(wav(x, 16000, channels=channels, width=width))
    assert rate == 16000 and y.ndim == 1 and len(y) == len(x)
    assert np.max(np.abs(y - x)) < (0.02 if width == 1 else 1e-3)


def test_resampling_keeps_the_pitch_and_the_length():
    x = np.sin(2 * np.pi * 440 * np.arange(32000) / 32000)
    y = hold_music.resample(x, 32000, 24000)
    assert len(y) == 24000
    spectrum = np.abs(np.fft.rfft(y * np.hanning(len(y))))
    assert np.fft.rfftfreq(len(y), 1 / 24000)[int(np.argmax(spectrum))] == pytest.approx(440, abs=2)
    assert len(hold_music.resample(x, 32000, 22050)) == 22050
    assert hold_music.resample(x, 24000, 24000) is x


def test_a_tone_above_the_new_rates_limit_does_not_fold_back():
    x = 0.5 * np.sin(2 * np.pi * 15000 * np.arange(32000) / 32000)  # above 12 kHz, 24 kHz's limit
    y = hold_music.resample(x, 32000, 24000)
    assert float(np.sqrt(np.mean(y[200:-200] ** 2))) < 0.05 * float(np.sqrt(np.mean(x ** 2)))


@pytest.mark.parametrize("amp", [0.02, 0.3, 0.9])
def test_the_clip_is_brought_to_the_built_in_tunes_loudness_not_its_peak(amp):
    pcm = hold_music.prepare(music(6.0, 32000, amp), 32000, 24000)
    assert dbfs(pcm) == pytest.approx(hold_music.TARGET_DBFS, abs=0.3)
    assert dbfs(pcm) == pytest.approx(dbfs(voice_pipeline.hold_music_loop(24000)), abs=0.5)
    assert np.max(np.abs(np.frombuffer(pcm, "<i2"))) < 32767  # no clipping


def test_the_loop_point_has_no_click():
    x = music(6.0, 24000)
    naive = np.round(hold_music.normalise(x) * 32767).astype("<i2").tobytes()
    assert not seam_ok(naive)  # as it is, the wrap is a jump
    pcm = hold_music.prepare(x, 24000, 24000)
    assert seam_ok(pcm)
    y = np.frombuffer(pcm, "<i2").astype(float)
    assert abs(y[-1] - y[0]) < 0.1 * np.max(np.abs(y))  # sample continuity at the loop point
    assert len(y) == len(x) - int(hold_music.LOOP_CROSSFADE_S * 24000)  # the tail is folded in, not added


def test_silence_at_either_end_is_trimmed_so_the_loop_has_no_gap():
    x = np.concatenate([np.zeros(24000), music(4.0, 24000), np.zeros(24000)])
    pcm = hold_music.prepare(x, 24000, 24000)
    seconds = len(pcm) / 2 / 24000
    assert 3.4 <= seconds <= 3.6  # 4 s of music less the crossfade, none of the 2 s of silence


# -- the shipped game-show clip -----------------------------------------------------------------------------
def test_the_game_show_clip_ships_with_its_recipe():
    assert hold_music.GAMESHOW_CLIP.is_file()
    recipe = json.loads(hold_music.GAMESHOW_CLIP.with_suffix(".json").read_text())
    assert recipe["prompt"] and isinstance(recipe["seed"], int) and 20 <= recipe["duration_s"] <= 30
    assert "no vocals" in recipe["prompt"]
    x, rate = hold_music.read_wav(hold_music.GAMESHOW_CLIP.read_bytes())
    assert rate == 24000 and 18.0 <= len(x) / rate <= 30.0


@pytest.mark.parametrize("rate", [24000, 22050, 16000])
def test_the_game_show_clip_plays_at_the_streams_rate_quietly_and_seamlessly(hub, rate):
    pcm, source = hold_music.loop_for(hub, rate, voice_pipeline.hold_music_loop)
    assert source == "gameshow" and pcm != voice_pipeline.hold_music_loop(rate)
    assert len(pcm) / 2 / rate == pytest.approx(len(pcm) / 2 / rate)  # whole samples
    assert dbfs(pcm) == pytest.approx(-24.0, abs=0.3) and seam_ok(pcm)
    assert hold_music.loop_for(hub, rate, voice_pipeline.hold_music_loop)[0] is pcm  # cached
    assert hold_music.settings(hub)["source"] == "gameshow"  # the default


def test_the_built_in_tune_is_still_there_when_chosen(hub):
    hold_music.save_settings(hub, {"source": "builtin"})
    assert hold_music.loop_for(hub, 24000, voice_pipeline.hold_music_loop) == (
        voice_pipeline.hold_music_loop(24000), "builtin")


# -- falling back -------------------------------------------------------------------------------------------
def test_a_missing_clip_plays_the_built_in_tune_and_says_so_once(hub, monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(hold_music, "GAMESHOW_CLIP", tmp_path / "gone.wav")
    caplog.set_level(logging.WARNING, logger="homehub.voice.hold_music")
    for _ in range(3):
        pcm, source = hold_music.loop_for(hub, 24000, voice_pipeline.hold_music_loop)
        assert source == "builtin" and pcm == voice_pipeline.hold_music_loop(24000)
    warnings = [r for r in caplog.records if "missing" in r.getMessage()]
    assert len(warnings) == 1 and "built-in tune plays" in warnings[0].getMessage()


def test_a_clip_that_will_not_decode_plays_the_built_in_tune(hub, tmp_path, caplog):
    hub.store.set_kv(hold_music.KV_KEY, {"source": "upload", "upload": {"name": "x.wav"}})
    (tmp_path / hold_music.UPLOAD_FILE).write_bytes(b"RIFF\x00\x00\x00\x00WAVEjunk")
    caplog.set_level(logging.WARNING, logger="homehub.voice.hold_music")
    pcm, source = hold_music.loop_for(hub, 24000, voice_pipeline.hold_music_loop)
    assert source == "builtin" and pcm == voice_pipeline.hold_music_loop(24000)
    hold_music.loop_for(hub, 24000, voice_pipeline.hold_music_loop)
    assert sum("could not be decoded" in r.getMessage() for r in caplog.records) == 1


def test_an_uploaded_clip_whose_file_is_gone_plays_the_built_in_tune(hub):
    hub.store.set_kv(hold_music.KV_KEY, {"source": "upload", "upload": {"name": "x.wav"}})
    assert hold_music.settings(hub)["upload"]["missing"] is True
    assert hold_music.loop_for(hub, 24000, voice_pipeline.hold_music_loop)[1] == "builtin"


def test_a_hub_without_a_store_plays_the_game_show_clip():
    assert hold_music.loop_for(SimpleNamespace(), 24000, voice_pipeline.hold_music_loop)[1] == "gameshow"
    assert hold_music.loop_for(None, 24000, voice_pipeline.hold_music_loop)[1] == "gameshow"


# -- uploads ------------------------------------------------------------------------------------------------
def test_an_uploaded_wav_is_kept_decoded_and_becomes_the_hold_music(hub, tmp_path):
    out = hold_music.store_upload(hub, wav(music(5.0, 44100), 44100, channels=2), "Our Theme.wav")
    assert out["source"] == "upload"
    assert out["upload"]["name"] == "Our Theme.wav" and out["upload"]["seconds"] == 5.0
    kept = tmp_path / hold_music.UPLOAD_FILE
    x, rate = hold_music.read_wav(kept.read_bytes())
    assert rate == 44100 and len(x) == 5 * 44100  # mono now
    pcm, source = hold_music.loop_for(hub, 24000, voice_pipeline.hold_music_loop)
    assert source == "upload" and dbfs(pcm) == pytest.approx(-24.0, abs=0.3) and seam_ok(pcm)
    after = hold_music.delete_upload(hub)
    assert after["source"] == "gameshow" and after["upload"] is None and not kept.exists()


def test_a_very_high_rate_upload_is_kept_at_48_khz(hub, tmp_path):
    hold_music.store_upload(hub, wav(music(3.0, 96000), 96000), "hi.wav")
    _x, rate = hold_music.read_wav((tmp_path / hold_music.UPLOAD_FILE).read_bytes())
    assert rate == hold_music.MAX_STORED_RATE


@pytest.mark.parametrize(("data", "name", "status", "says"), [
    (b"", "a.wav", 400, "empty"),
    (b"hello, this is not audio at all", "a.wav", 415, "Use a wav"),
    (b"%PDF-1.7 not audio", "a.pdf", 415, "Use a wav"),
    (b"OggS" + b"\x00" * 60, "a.wav", 415, "holds ogg"),
    (b"x" * (hold_music.MAX_UPLOAD_BYTES + 1), "big.wav", 413, "most is 5 MB"),
])
def test_uploads_are_checked_for_type_and_size(hub, data, name, status, says):
    with pytest.raises(hold_music.ClipError) as err:
        hold_music.store_upload(hub, data, name)
    assert err.value.status == status and says in str(err.value)
    assert hold_music.settings(hub)["source"] == "gameshow"  # nothing changed


def test_an_upload_over_a_minute_is_refused(hub):
    long = wav(music(61.0, 8000), 8000)
    assert len(long) < hold_music.MAX_UPLOAD_BYTES  # small enough; refused for its length
    with pytest.raises(hold_music.ClipError, match="longer than 60 s") as err:
        hold_music.store_upload(hub, long, "long.wav")
    assert err.value.status == 400
    hold_music.store_upload(hub, wav(music(59.0, 8000), 8000), "fine.wav")  # just under is fine


@pytest.mark.parametrize(("x", "says"), [(music(1.0, 16000), "shorter than"), (np.zeros(48000), "silent")])
def test_a_tiny_or_silent_upload_is_refused(hub, x, says):
    with pytest.raises(hold_music.ClipError, match=says):
        hold_music.store_upload(hub, wav(x, 16000), "a.wav")


@pytest.mark.parametrize(("kind", "codecs"), [("mp3", ["libmp3lame"]), ("ogg", ["libvorbis", "libopus", "flac"]),
                                               ("m4a", ["aac"])])
@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg is not installed (the hub's image has it)")
def test_mp3_ogg_and_m4a_uploads_are_decoded_with_ffmpeg(hub, tmp_path, kind, codecs):
    src = tmp_path / "src.wav"
    src.write_bytes(wav(music(4.0, 48000), 48000))
    out = tmp_path / f"clip.{kind}"
    for codec in codecs:  # whichever encoder this ffmpeg was built with
        done = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(src), "-c:a", codec,
                               str(out)], capture_output=True, check=False)
        if done.returncode == 0:
            break
    else:
        pytest.skip(f"this ffmpeg has none of {codecs}")
    info = hold_music.store_upload(hub, out.read_bytes(), out.name)
    assert info["upload"]["type"] == kind and 3.8 <= info["upload"]["seconds"] <= 4.3
    pcm, source = hold_music.loop_for(hub, 22050, voice_pipeline.hold_music_loop)
    assert source == "upload" and dbfs(pcm) == pytest.approx(-24.0, abs=0.3)


def test_without_ffmpeg_a_compressed_upload_says_to_use_a_wav(hub, monkeypatch):
    monkeypatch.setattr(hold_music.shutil, "which", lambda name: None)
    with pytest.raises(hold_music.ClipError, match="upload a WAV"):
        hold_music.store_upload(hub, b"ID3" + b"\x00" * 100, "a.mp3")


# -- the setting and the lines ------------------------------------------------------------------------------
def test_the_setting_checks_what_it_is_given(hub):
    with pytest.raises(hold_music.ClipError, match="source must be one of"):
        hold_music.save_settings(hub, {"source": "jazz"})
    with pytest.raises(hold_music.ClipError, match="Upload a clip first"):
        hold_music.save_settings(hub, {"source": "upload"})
    with pytest.raises(hold_music.ClipError, match="at most 160"):
        hold_music.save_settings(hub, {"lines": ["x" * 161]})
    with pytest.raises(hold_music.ClipError, match="At most 20"):
        hold_music.save_settings(hub, {"lines": [f"Line {i}." for i in range(21)]})
    out = hold_music.save_settings(hub, {"lines": ["  Cue  the music. ", "", "Thinking..."]})
    assert out["lines"] == ["Cue the music.", "Thinking..."] and out["source"] == "gameshow"
    assert hold_music.hold_lines(hub, ("Default.",)) == ("Cue the music.", "Thinking...")
    hold_music.save_settings(hub, {"lines": []})
    assert hold_music.hold_lines(hub, ("Default.",)) == ("Default.",)


def test_the_default_lines_have_game_show_ones():
    assert "Let me think about that... cue the thinking music." in voice_pipeline.DEFAULT_HOLD_LINES
    assert any("clock is ticking" in line for line in voice_pipeline.DEFAULT_HOLD_LINES)


# -- the API ------------------------------------------------------------------------------------------------
def test_the_api_reads_changes_and_takes_an_upload(hub):
    runtime = SimpleNamespace(hub=hub, store=hub.store)
    app = FastAPI()
    app.include_router(voice_api.build_router(runtime, Auth(hub.store, SECRET)))
    client = TestClient(app)
    h = {"Authorization": f"Bearer {SECRET}"}
    assert client.get("/hub/voice/hold-music").status_code == 401
    assert client.put("/hub/voice/hold-music", json={"source": "builtin"}).status_code == 401
    got = client.get("/hub/voice/hold-music", headers=h).json()
    assert got["source"] == "gameshow" and [s["id"] for s in got["sources"]] == ["gameshow", "builtin", "upload"]
    assert got["limits"]["max_bytes"] == 5 * 1024 * 1024 and got["limits"]["types"] == ["wav", "mp3", "ogg", "m4a"]
    assert client.put("/hub/voice/hold-music", headers=h, json={"source": "jazz"}).status_code == 400
    assert client.put("/hub/voice/hold-music", headers=h, json=[1]).status_code == 400
    assert client.put("/hub/voice/hold-music", headers=h, json={"source": "builtin"}).json()["source"] == "builtin"
    up = client.post("/hub/voice/hold-music/clip", headers=h, params={"filename": "ours.wav"},
                     content=wav(music(3.0, 24000), 24000))
    assert up.status_code == 200 and up.json()["source"] == "upload" and up.json()["upload"]["name"] == "ours.wav"
    bad = client.post("/hub/voice/hold-music/clip", headers=h, params={"filename": "a.txt"}, content=b"just text here")
    assert bad.status_code == 415
    big = client.post("/hub/voice/hold-music/clip", headers=h, params={"filename": "big.wav"},
                      content=b"RIFF" + b"\x00" * hold_music.MAX_UPLOAD_BYTES)
    assert big.status_code == 413
    gone = client.delete("/hub/voice/hold-music/clip", headers=h).json()
    assert gone["upload"] is None and gone["source"] == "gameshow"


# -- in a run -----------------------------------------------------------------------------------------------
@pytest.mark.parametrize("source", ["gameshow", "builtin"])
async def test_a_wait_plays_the_chosen_music_and_says_a_chosen_line(env, monkeypatch, tmp_path, source):
    be = Music(delay=0.8, answer_synth_s=0.2).install(monkeypatch)
    hub, drv = make_driver()
    hub.store, hub.data_dir = KV(), str(tmp_path)
    hold_music.save_settings(hub, {"source": source, "lines": ["Cue the thinking music."]})
    await drv.async_setup()
    await run_turn(drv, drv._client, AUDIO)
    assert be.appended[1] == "Cue the thinking music."
    expected, _ = hold_music.loop_for(hub, 24000, voice_pipeline.hold_music_loop)
    first = be.music_pcm[0]
    assert expected.startswith(first)  # the music in the stream is the chosen loop, from its start
    assert drv.voice.last_run.music_source == source
    assert drv.voice.last_run.public()["music_source"] == source
    await drv.async_unload()


def beat_track(seconds: float, rate: int, bpm: float, lead_s: float = 0.0) -> np.ndarray:
    """A steady pulse: a short decaying tone on every beat over a quiet bed, starting after ``lead_s``."""
    n = int(seconds * rate)
    t = np.arange(n) / rate
    x = 0.02 * np.sin(2 * np.pi * 110.0 * t)
    period = 60.0 / bpm
    hit = np.arange(int(0.08 * rate)) / rate
    tone = 0.6 * np.sin(2 * np.pi * 880.0 * hit) * np.exp(-hit * 40)
    k = 0
    while lead_s + k * period < seconds:
        i = round((lead_s + k * period) * rate)
        j = min(n, i + len(tone))
        x[i:j] += tone[: j - i]
        k += 1
    return x


def test_a_clip_with_a_beat_loops_on_whole_bars():
    """23.9 s at 122 bpm is 48.6 beats: looped as it is, the beat would stumble every time round."""
    rate, bpm = 24000, 122.0
    x = beat_track(23.94, rate, bpm, lead_s=0.29)
    bpm_found, first, _ = hold_music.estimate_beat(x, rate)
    assert abs(bpm_found - bpm) < 0.3
    assert abs(first / rate - 0.29) < 0.02
    loop = np.frombuffer(hold_music.prepare(x, rate, rate), "<i2")
    beats = len(loop) / rate * bpm / 60
    assert abs(beats / hold_music.BEATS_PER_BAR - round(beats / hold_music.BEATS_PER_BAR)) < 0.02
    assert round(beats) % hold_music.BEATS_PER_BAR == 0
    assert seam_ok(loop.tobytes())


def test_a_clip_with_no_steady_beat_is_only_crossfaded():
    rng = np.random.default_rng(7)
    rate = 24000
    x = 0.2 * rng.standard_normal(int(8 * rate))
    assert hold_music.estimate_beat(x, rate) is None
    assert len(hold_music.bar_aligned(x, rate)) == len(x)


def test_a_stored_clip_is_looped_once_not_twice():
    """The shipped clip is stored unlooped, so loading it folds the crossfade away once."""
    rate = 24000
    x = music(6.0, rate)
    stored = np.frombuffer(hold_music.prepare(x, rate, rate, loop=False), "<i2")
    assert len(stored) == len(x)
    looped_once = np.frombuffer(hold_music.prepare(stored / 32767, rate, rate), "<i2")
    assert len(looped_once) == len(np.frombuffer(hold_music.prepare(x, rate, rate), "<i2"))


def test_the_shipped_game_show_clip_loops_on_whole_bars():
    x, rate = hold_music.read_wav(hold_music.GAMESHOW_CLIP.read_bytes())
    beat = hold_music.estimate_beat(x, rate)
    assert beat is not None
    loop = np.frombuffer(hold_music.prepare(x, rate, 24000), "<i2")
    bars = len(loop) / 24000 * beat[0] / 60 / hold_music.BEATS_PER_BAR
    assert abs(bars - round(bars)) < 0.02
    assert seam_ok(loop.tobytes())

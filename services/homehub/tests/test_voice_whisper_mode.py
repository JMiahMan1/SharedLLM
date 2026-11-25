"""Whisper mode heard from the audio, the quieter reply and the volume coming back, and the new-voice line.

The speech here is synthetic: a pitch-gliding harmonic source (talking) or white noise (whispering)
shaped by the same three formants and the same syllable rhythm, with a little room noise. No recorded
or real voice is used.
"""
from __future__ import annotations

import asyncio
import json
import logging
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.homehub import tts, voice_api, voice_pipeline, voice_whisper
from services.homehub.auth import Auth
from services.homehub.tests.conftest import make_hub
from services.homehub.tests.test_voice_pipeline import (  # fixtures and stand-ins, not tests
    DATA,
    ENTITIES,
    MAC,
    SECRET,
    URL,
    Backends,
    E,
    FakeSTT,
    VoiceClient,
    _silence,
    _tone,
    env,
    loudness_alone,
    run_turn,
    settings_from_the_test,
    speak_to,
)
from services.homehub.voice_pipeline import PipelineError, Reply, read_speech_stream

__all__ = ["env", "loudness_alone", "settings_from_the_test"]

RATE = 16000
VOLUME = "number.test_satellite_assistant_volume"
WHISPER_SENSOR = "binary_sensor.test_satellite_last_request_whispered"


def _speech(seconds: float = 2.0, level_db: float = -26.0, *, whisper: bool = False, seed: int = 1) -> bytes:
    """Talking (a gliding 100-180 Hz harmonic source) or whispering (noise), through the same mouth."""
    rng = np.random.default_rng(seed)
    n = int(seconds * RATE)
    t = np.arange(n) / RATE
    if whisper:
        source = rng.standard_normal(n)
    else:
        f0 = 140 + 40 * np.sin(2 * np.pi * 0.7 * t)
        phase = 2 * np.pi * np.cumsum(f0) / RATE
        source = sum(np.sin(k * phase) / k for k in range(1, 28))
    freqs = np.fft.rfftfreq(n, 1 / RATE)
    mouth = sum(1 / (1 + ((freqs - f) / bw) ** 2) for f, bw in ((500, 90), (1500, 140), (2500, 200)))
    y = np.fft.irfft(np.fft.rfft(source) * mouth, n)
    y *= np.clip(np.sin(np.pi * 3.5 * t), 0, None) ** 0.6  # syllables with short gaps
    loud = y[np.abs(y) > 0.05 * np.abs(y).max()]
    y *= 10 ** (level_db / 20) * 32768 / np.sqrt(np.mean(loud ** 2))
    y += rng.standard_normal(n) * 10 ** (-62 / 20) * 32768  # the room
    return np.clip(np.round(y), -32768, 32767).astype("<i2").tobytes()


class FakeStore:
    def __init__(self) -> None:
        self.kv: dict[str, object] = {}

    def get_kv(self, key, default=None):
        return json.loads(json.dumps(self.kv[key])) if key in self.kv else default

    def set_kv(self, key, value):
        self.kv[key] = json.loads(json.dumps(value))

    def save_config_entry(self, _entry):
        pass


# -- the features ------------------------------------------------------------------------
def test_talking_is_voiced_and_whispering_is_not():
    spoken = voice_whisper.measure(_speech())
    whispered = voice_whisper.measure(_speech(level_db=-44, whisper=True))
    assert spoken is not None and whispered is not None
    assert spoken.voiced_ratio > 0.55 and spoken.hnr_db > 0
    assert whispered.voiced_ratio < 0.25 and whispered.hnr_db < voice_whisper.MAX_HNR_DB
    assert whispered.level_db < spoken.level_db - 15
    assert set(spoken.public()) == {"level_db", "voiced", "hnr_db", "tilt_db", "speech_s"}


def test_a_whisper_needs_to_be_quieter_than_the_satellite_usually_hears():
    usual = voice_whisper.measure(_speech()).level_db
    quiet_whisper = voice_whisper.measure(_speech(level_db=-44, whisper=True))
    close_whisper = voice_whisper.measure(_speech(level_db=-27, whisper=True))  # right at the microphone
    assert voice_whisper.decide(quiet_whisper, usual)
    assert not voice_whisper.decide(close_whisper, usual)
    # Before the satellite's usual level is known, a pitchless command is a whisper.
    assert voice_whisper.decide(close_whisper, None)


def test_a_far_or_quiet_voice_is_not_a_whisper():
    usual = voice_whisper.measure(_speech()).level_db
    far = voice_whisper.measure(_speech(level_db=-44))
    assert far is not None and not voice_whisper.decide(far, usual)
    assert not voice_whisper.decide(far, None)
    assert not voice_whisper.decide(None, usual)


def test_too_little_audio_says_nothing():
    assert voice_whisper.measure(_silence(0.2)) is None
    assert voice_whisper.measure(_silence(2.0)) is None  # no speech at all


def test_the_usual_level_is_learned_from_a_few_commands():
    base = voice_whisper.LevelBaseline()
    for level in (-30.0, -28.0):
        base.add(level)
    assert base.usual_db is None  # two commands are not enough to trust
    base.add(-29.0)
    assert base.usual_db is not None and -30 < base.usual_db < -28
    again = voice_whisper.LevelBaseline.from_dict(base.to_dict())
    assert again.runs == 3 and again.usual_db == pytest.approx(base.usual_db, abs=0.01)
    assert voice_whisper.LevelBaseline.from_dict({"level_db": "x", "runs": 3}).runs == 0


def test_settings_are_validated_and_default_to_on():
    s = voice_whisper.WhisperSettings()
    assert s.enabled is True and s.volume == 0.22 and s.level_drop_db == voice_whisper.DEFAULT_LEVEL_DROP_DB
    off = voice_whisper.validate_settings({"enabled": False, "volume": 0.3}, s)
    assert off.enabled is False and off.volume == 0.3 and off.level_drop_db == s.level_drop_db
    for bad in ({"enabled": "no"}, {"volume": 0}, {"volume": 1.5}, {"volume": True}, {"level_drop_db": 1},
                {"loudness": 3}, []):
        with pytest.raises(ValueError):
            voice_whisper.validate_settings(bad, s)
    store = FakeStore()
    assert voice_whisper.load_settings(store, 0.3).volume == 0.3
    store.set_kv(voice_whisper.SETTINGS_KV, {"enabled": False})
    assert voice_whisper.load_settings(store).enabled is False
    store.set_kv(voice_whisper.SETTINGS_KV, {"volume": 9})
    assert voice_whisper.load_settings(store) == voice_whisper.WhisperSettings()


# -- the pipeline ---------------------------------------------------------------------------
def _entities(whisper_sensor: bool = False, volume: bool = True) -> list[dict]:
    ents = [dict(e) for e in ENTITIES]
    if whisper_sensor:
        ents.append({"entity_id": WHISPER_SENSOR, "unique_id": f"{MAC}-last_request_whispered", "device_id": "dev1"})
    if volume:
        ents.append({"entity_id": VOLUME, "unique_id": f"{MAC}-assistant_volume", "device_id": "dev1"})
    return ents


async def _driver(monkeypatch, *, whisper_sensor=False, volume=True, usual_db: float | None = -26.0):
    from services.homehub.drivers.esphome import ESPHomeDriver

    hub, entry = make_hub("esphome", dict(DATA), _entities(whisper_sensor, volume), options={})
    hub.store = FakeStore()
    if usual_db is not None:
        hub.store.set_kv(voice_pipeline.LEVEL_KV.format(entry_id=entry.entry_id), {"level_db": usual_db, "runs": 5})
    drv = ESPHomeDriver(hub, entry)
    drv._make_client = lambda host, port=None: VoiceClient(voice_pipeline.VoiceAssistantFeature.VOICE_ASSISTANT
                                                            | voice_pipeline.VoiceAssistantFeature.API_AUDIO)
    hub.drivers[entry.entry_id] = drv
    await drv.async_setup()
    if volume:
        hub.publish(VOLUME, "0.8", {})
    calls: list[tuple[str, str, dict]] = []

    async def fake_call_service(domain, service, data=None, *a, **k):
        calls.append((domain, service, dict(data or {})))
        if service == "set_value":
            hub.publish(VOLUME, str(data["value"]), {})
        return {"changed_states": [], "response": None}

    monkeypatch.setattr(hub, "call_service", fake_call_service)
    monkeypatch.setattr(voice_pipeline, "PLAYBACK_FETCH_S", 0.0)
    monkeypatch.setattr(voice_pipeline, "PLAYBACK_TAIL_S", 0.0)
    return hub, drv, calls


def _volumes(calls) -> list[float]:
    return [c[2]["value"] for c in calls if c[1] == "set_value"]


WHISPERED = [_speech(level_db=-44, whisper=True), _silence(1.0)]
SPOKEN = [_speech(level_db=-26), _silence(1.0)]


async def test_a_whispered_command_gets_a_quiet_reply_and_the_volume_back_after_it(env, monkeypatch, caplog):
    _hub, drv, calls = await _driver(monkeypatch)
    b = Backends(stt=FakeSTT(text="what time is it"), reply="It is ten past nine.").install(monkeypatch)
    order: list[str] = []
    real_speak = b.speak

    async def speak(text, voice):
        order.append(f"speak at {_volumes(calls)}")
        return await real_speak(text, voice)

    monkeypatch.setattr(voice_pipeline.BACKENDS, "speak", speak)
    caplog.set_level(logging.INFO, logger="homehub.voice")
    await run_turn(drv, drv._client, WHISPERED)
    run = drv.voice.last_run
    assert run.outcome == "ok" and run.whispered and run.whisper["by"] == "audio"
    assert order == ["speak at [0.22]"]  # the reply was made after the volume went down
    await drv.voice._volume.settled()
    assert _volumes(calls) == [0.22, 0.8]
    assert run.volume == "lowered 0.8 to 0.22" and run.volume_restored == "restored to 0.8"
    assert b.asked[0][1]["whispered"] is True
    [line] = [r.getMessage() for r in caplog.records if "whisper=" in r.getMessage()]
    assert "whisper=yes (level" in line and "by audio" in line and "usual -26.0" in line
    assert "new_voice=no" in line and "volume=lowered 0.8 to 0.22, restored to 0.8" in line
    assert drv.voice.history[0]["whisper"]["voiced"] < 0.25
    await drv.async_unload()


async def test_the_volume_waits_for_the_reply_to_finish_playing(env, monkeypatch):
    _hub, drv, calls = await _driver(monkeypatch)
    Backends(stt=FakeSTT(text="hi"), reply="Hello.").install(monkeypatch)
    monkeypatch.setattr(voice_pipeline, "PLAYBACK_TAIL_S", 0.3)
    await run_turn(drv, drv._client, WHISPERED)
    assert _volumes(calls) == [0.22]  # still playing
    await drv.voice._volume.settled()
    assert _volumes(calls) == [0.22, 0.8]
    await drv.async_unload()


async def test_the_volume_comes_back_when_jarvis_fails(env, monkeypatch):
    _hub, drv, calls = await _driver(monkeypatch)
    Backends(stt=FakeSTT(text="hi"), ask_error=PipelineError("intent-failed", "boom")).install(monkeypatch)
    await run_turn(drv, drv._client, WHISPERED)
    await drv.voice._volume.settled()
    run = drv.voice.last_run
    assert run.outcome == "error" and _volumes(calls) == [0.22, 0.8]
    assert run.volume_restored == "restored to 0.8 (nothing was played)"
    await drv.async_unload()


async def test_the_volume_comes_back_when_the_reply_cannot_be_spoken(env, monkeypatch):
    from services.homehub.core import HubError

    _hub, drv, calls = await _driver(monkeypatch)
    Backends(stt=FakeSTT(text="hi"), speak_error=HubError("tts down")).install(monkeypatch)
    await run_turn(drv, drv._client, WHISPERED)
    await drv.voice._volume.settled()
    assert drv.voice.last_run.outcome == "error" and _volumes(calls) == [0.22, 0.8]
    await drv.async_unload()


async def test_the_volume_comes_back_at_once_when_the_run_is_cut_off(env, monkeypatch):
    _hub, drv, calls = await _driver(monkeypatch)
    b = Backends(stt=FakeSTT(text="hi"), ask_delay=5.0).install(monkeypatch)
    client = drv._client
    await client.handlers["start"]("c", 0, None, "Hey Jarvis")
    task = drv.voice._task
    await speak_to(client, WHISPERED)
    for _ in range(200):
        if b.asked:
            break
        await asyncio.sleep(0.01)
    assert _volumes(calls) == [0.22]
    await client.handlers["stop"](True)  # the person (or a new wake word) cut it off
    for _ in range(200):
        if task.done():
            break
        await asyncio.sleep(0.01)
    assert task.cancelled()
    await drv.voice._volume.settled()
    assert _volumes(calls) == [0.22, 0.8]
    assert drv.voice.last_run.volume_restored == "restored to 0.8 (cut off)"
    await drv.async_unload()


async def test_a_whispered_follow_up_keeps_the_volume_low_and_an_ordinary_one_brings_it_back(env, monkeypatch):
    _hub, drv, calls = await _driver(monkeypatch)
    Backends(stt=FakeSTT(text="hi"), reply="Hello.").install(monkeypatch)
    monkeypatch.setattr(voice_pipeline, "PLAYBACK_TAIL_S", 30.0)  # the first reply is still "playing"
    await run_turn(drv, drv._client, WHISPERED)
    first = drv.voice.last_run
    await run_turn(drv, drv._client, WHISPERED)
    second = drv.voice.last_run
    assert _volumes(calls) == [0.22]  # never raised in between, never lowered twice
    assert second.volume == "kept low" and first.volume_restored == "kept low for the next whispered command"
    await run_turn(drv, drv._client, SPOKEN)
    assert _volumes(calls) == [0.22, 0.8]  # put back before the ordinary reply, to the volume from before
    assert second.volume_restored == "restored to 0.8 (an ordinary command came next)"
    assert not drv.voice.last_run.whispered and drv.voice.last_run.volume is None
    await drv.async_unload()


async def test_spoken_commands_teach_the_usual_level_and_leave_the_volume(env, monkeypatch):
    hub, drv, calls = await _driver(monkeypatch, usual_db=None)
    Backends(stt=FakeSTT(text="hi"), reply="Hello.").install(monkeypatch)
    key = voice_pipeline.LEVEL_KV.format(entry_id=drv.entry.entry_id)
    for _ in range(3):
        await run_turn(drv, drv._client, SPOKEN)
        assert not drv.voice.last_run.whispered
    learned = hub.store.get_kv(key)
    assert learned["runs"] == 3 and -30 < learned["level_db"] < -22
    assert _volumes(calls) == []
    # Now a whisper at the microphone's usual loudness is answered normally; a quiet one is not.
    await run_turn(drv, drv._client, [_speech(level_db=-26, whisper=True), _silence(1.0)])
    assert not drv.voice.last_run.whispered
    await run_turn(drv, drv._client, WHISPERED)
    assert drv.voice.last_run.whispered
    assert hub.store.get_kv(key)["runs"] == 3  # whispers never move the usual level
    await drv.voice._volume.settled()
    await drv.async_unload()


async def test_whisper_mode_off_leaves_everything_as_it_was(env, monkeypatch):
    hub, drv, calls = await _driver(monkeypatch, whisper_sensor=True)
    hub.store.set_kv(voice_whisper.SETTINGS_KV, {"enabled": False})
    hub.publish(WHISPER_SENSOR, "on", {})
    b = Backends(stt=FakeSTT(text="hi"), reply="Hello.").install(monkeypatch)
    await run_turn(drv, drv._client, WHISPERED)
    run = drv.voice.last_run
    assert not run.whispered and run.whisper == {"mode": "off"} and _volumes(calls) == []
    assert "whispered" not in b.asked[0][1]
    await drv.async_unload()


async def test_the_whisper_volume_setting_is_used(env, monkeypatch):
    hub, drv, calls = await _driver(monkeypatch)
    hub.store.set_kv(voice_whisper.SETTINGS_KV, {"volume": 0.35})
    Backends(stt=FakeSTT(text="hi"), reply="Hello.").install(monkeypatch)
    await run_turn(drv, drv._client, WHISPERED)
    await drv.voice._volume.settled()
    assert _volumes(calls) == [0.35, 0.8]
    await drv.async_unload()


async def test_a_satellite_without_a_volume_gets_quieter_audio(env, monkeypatch):
    _hub, drv, calls = await _driver(monkeypatch, volume=False)
    Backends(stt=FakeSTT(text="hi"), reply="Hello.").install(monkeypatch)
    quietened: list[tuple[str, float]] = []
    monkeypatch.setattr(voice_pipeline.BACKENDS, "quieten", lambda url, f: quietened.append((url, f)) or True)
    await run_turn(drv, drv._client, WHISPERED)
    run = drv.voice.last_run
    assert run.whispered and run.reply_gain == 0.22 and quietened == [(URL, 0.22)]
    assert run.volume == "no volume to set: reply audio scaled to 0.22" and calls == []
    await drv.async_unload()


def test_a_quieter_stream_scales_every_part_and_a_stored_clip_can_be_quietened():
    stream = tts.ClipStream()
    stream.gain = 0.25
    stream.append(_wav_clip(0.2))
    stream.append_pcm(_tone(0.1, amplitude=8000))  # hold music goes in the same way
    pcm = np.frombuffer(b"".join(stream.parts), "<i2")
    assert 1500 < np.abs(pcm).max() <= 2000
    tts._clips["tok"] = (1e12, _wav_clip(0.2))
    assert tts.scale_clip("tok", 0.5)
    assert tts.clip_seconds(tts.clip("tok")) == pytest.approx(0.2, abs=0.01)
    assert not tts.scale_clip("gone", 0.5)
    tts._clips.pop("tok")


def _wav_clip(seconds: float) -> bytes:
    import io
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(_tone(seconds, amplitude=8000))
    return buf.getvalue()


# -- the new-voice line ---------------------------------------------------------------------
def _reply(text: str, note: str | None) -> Reply:
    out = Reply(text)
    out.new_voice = True
    out.note = note
    return out


async def test_the_new_voice_line_is_said_after_the_answer(env, monkeypatch, caplog):
    _hub, drv, _calls = await _driver(monkeypatch)
    note = "By the way, I don't think I know your voice yet."
    b = Backends(stt=FakeSTT(text="hi"), reply=_reply("It is sunny.", note)).install(monkeypatch)
    caplog.set_level(logging.INFO, logger="homehub.voice")
    await run_turn(drv, drv._client, SPOKEN)
    assert b.spoken == [f"It is sunny. {note}"]
    run = drv.voice.last_run
    assert run.new_voice is True and run.new_voice_invited is True
    assert any("new_voice=yes, invited" in r.getMessage() for r in caplog.records)
    # Text only on the node: the TTS_START event still carries the answer itself.
    assert any(k == E.VOICE_ASSISTANT_TTS_START and d["text"] == "It is sunny." for k, d in drv._client.events)
    await drv.async_unload()


async def test_no_new_voice_line_for_a_whisper(env, monkeypatch):
    _hub, drv, _calls = await _driver(monkeypatch)
    b = Backends(stt=FakeSTT(text="hi"), reply=_reply("It is late.", "By the way...")).install(monkeypatch)
    await run_turn(drv, drv._client, WHISPERED)
    assert b.spoken == ["It is late."] and not drv.voice.last_run.new_voice_invited
    await drv.voice._volume.settled()
    await drv.async_unload()


async def test_the_gateway_answer_carries_the_new_voice_fields():
    lines = [json.dumps({"message": {"content": "Sure."}, "speech": True}),
             json.dumps({"done": True, "reply": "Sure.", "speech_rest": "", "new_voice": True,
                         "voice_note": "  By the way.  "})]

    async def gen():
        for line in lines:
            yield line.encode()

    said: list[str] = []

    async def on_sentence(text, _voice):
        said.append(text)

    out = await read_speech_stream(gen(), on_sentence)
    assert out.new_voice is True and out.note == "By the way." and said == ["Sure."]
    whole = voice_pipeline._reply_from_answer(200, {"message": {"content": "Hi."}})
    assert whole.new_voice is False and whole.note is None


# -- the settings API -----------------------------------------------------------------------
async def test_whisper_mode_is_a_hub_setting(env, monkeypatch):
    hub, drv, _calls = await _driver(monkeypatch)
    runtime = SimpleNamespace(hub=hub, store=hub.store)
    app = FastAPI()
    app.include_router(voice_api.build_router(runtime, Auth(hub, SECRET)))
    client = TestClient(app)
    h = {"Authorization": f"Bearer {SECRET}"}
    assert client.get("/hub/voice/settings").status_code == 401
    body = client.get("/hub/voice/settings", headers=h).json()
    assert body["whisper"] == {"enabled": True, "volume": 0.22, "level_drop_db": voice_whisper.DEFAULT_LEVEL_DROP_DB}
    assert body["defaults"]["whisper"]["enabled"] is True
    changed = client.put("/hub/voice/settings", headers=h, json={"whisper": {"enabled": False, "volume": 0.4}})
    assert changed.status_code == 200 and changed.json()["whisper"]["enabled"] is False
    assert voice_whisper.load_settings(hub.store).volume == 0.4
    bad = client.put("/hub/voice/settings", headers=h, json={"whisper": {"volume": 2}})
    assert bad.status_code == 400 and "volume" in bad.json()["detail"]
    assert client.put("/hub/voice/settings", headers=h, json={"enabled": True}).status_code == 400
    await drv.async_unload()



@pytest.mark.parametrize(("voiced", "hnr", "level", "expected"), [
    # Real whispers at the Office Echo (2026-11-25), usual level -34 dBFS: all three are whispers.
    (0.36, -2.5, -54.4, True),
    (0.43, -2.2, -48.1, True),   # missed at the old 0.40 voiced bar
    (0.41, -1.6, -41.9, True),   # 7.9 dB under: missed at the old 8 dB default
    # Ordinary commands there, and speech from across the room, are not.
    (0.85, 7.4, -29.9, False),
    (0.90, 8.2, -29.8, False),
    (0.56, 0.2, -48.0, False),   # the quietest, least voiced spoken clip measured, 14 dB under
])
def test_real_whispers_are_caught_and_speech_is_not(voiced, hnr, level, expected):
    f = voice_whisper.Features(level_db=level, voiced_ratio=voiced, hnr_db=hnr, tilt_db=-16.0, speech_frames=60)
    assert voice_whisper.decide(f, usual_db=-34.0) is expected

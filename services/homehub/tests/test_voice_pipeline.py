"""The voice pipeline on a stand-in ESPHome node, with stand-in speech to text, Jarvis and text to speech."""
from __future__ import annotations

import asyncio
import io
import json
import math
import struct
import wave
from types import SimpleNamespace
from typing import ClassVar

import pytest
from aioesphomeapi import APIVersion, DeviceInfo, VoiceAssistantEventType, VoiceAssistantFeature
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.homehub import voice_api, voice_pipeline
from services.homehub.auth import Auth
from services.homehub.core import UNAVAILABLE, HubError, ServiceNotFound
from services.homehub.drivers import esphome
from services.homehub.drivers.esphome import ESPHomeDriver
from services.homehub.tests.conftest import make_hub
from services.homehub.voice_pipeline import EnergyVAD, PipelineError, VoiceSettings

E = VoiceAssistantEventType
F = VoiceAssistantFeature
MAC = "02:00:00:00:0A:0B"
DATA = {"host": "127.0.0.1", "port": 6053, "password": "", "device_name": "test-satellite"}
SAT = "assist_satellite.test_satellite"
VAD_SELECT = "select.test_satellite_finished_speaking_detection"
ENTITIES = [
    {"entity_id": SAT, "unique_id": f"{MAC}-assist_satellite"},
    {"entity_id": VAD_SELECT, "unique_id": f"{MAC}-vad_sensitivity"},
    {"entity_id": "select.test_satellite_wake_word", "unique_id": f"{MAC}-wake_word"},
]
FULL = int(F.VOICE_ASSISTANT | F.API_AUDIO | F.ANNOUNCE | F.START_CONVERSATION)
SECRET = "internal-secret"
URL = "http://hub.example.com:8123/api/tts_proxy/clip.wav"


class VoiceClient:
    """An APIClient as far as the driver and the pipeline use it."""

    instances: ClassVar[list[VoiceClient]] = []

    def __init__(self, flags: int) -> None:
        self.flags = flags
        self.api_version = APIVersion(1, 14)
        self.events: list[tuple[VoiceAssistantEventType, dict]] = []
        self.handlers: dict | None = None
        self.unsubscribed = 0
        self.announced: list[dict] = []
        self.audio_out: list[bytes] = []
        self.on_stop = None
        self.available_wake_words = [("okay_nabu", "Okay Nabu"), ("hey_jarvis", "Hey Jarvis")]
        self.active_wake_words = ["okay_nabu"]
        self.config_error: Exception | None = None
        VoiceClient.instances.append(self)

    async def get_voice_assistant_configuration(self, timeout, external_wake_words=None):
        if self.config_error is not None:
            raise self.config_error
        return SimpleNamespace(
            available_wake_words=[SimpleNamespace(id=i, wake_word=w, trained_languages=["en"])
                                  for i, w in self.available_wake_words],
            active_wake_words=list(self.active_wake_words), max_active_wake_words=1)

    async def set_voice_assistant_configuration(self, active_wake_words):
        self.active_wake_words = list(active_wake_words)

    async def connect(self, on_stop=None, login=False):
        self.on_stop = on_stop

    async def device_info_and_list_entities(self):
        return DeviceInfo(name="test-satellite", mac_address=MAC, voice_assistant_feature_flags=self.flags), [], []

    def subscribe_home_assistant_states_and_services(self, **_kw):
        pass

    async def disconnect(self, force=False):
        pass

    def subscribe_voice_assistant(self, *, handle_start, handle_stop, handle_audio=None,
                                  handle_announcement_finished=None):
        self.handlers = {"start": handle_start, "stop": handle_stop, "audio": handle_audio,
                         "announcement_finished": handle_announcement_finished}

        def unsub():
            self.unsubscribed += 1
        return unsub

    def send_voice_assistant_event(self, event_type, data):
        self.events.append((event_type, dict(data or {})))

    def send_voice_assistant_audio(self, data):
        self.audio_out.append(data)

    async def send_voice_assistant_announcement_await_response(self, media_id, timeout, text="",
                                                               preannounce_media_id="", start_conversation=False):
        self.announced.append({"media_id": media_id, "text": text, "preannounce_media_id": preannounce_media_id,
                               "start_conversation": start_conversation})

    def kinds(self) -> list[VoiceAssistantEventType]:
        return [k for k, _ in self.events]


class FakeSTT:
    def __init__(self, text="what's the weather", fail_open=None, fail_finish=None, slow=0.0):
        self.text, self.fail_open, self.fail_finish, self.slow = text, fail_open, fail_finish, slow
        self.audio = b""
        self.closed = False

    async def open(self):
        if self.fail_open:
            raise self.fail_open

    async def send(self, pcm):
        self.audio += pcm

    async def finish(self):
        if self.slow:
            await asyncio.sleep(self.slow)
        if self.fail_finish:
            raise self.fail_finish
        return self.text

    async def close(self):
        self.closed = True


class Backends:
    def __init__(self, stt=None, reply="It is sunny.", ask_error=None, ask_delay=0.0, speak_error=None):
        self.stt = stt or FakeSTT()
        self.reply, self.ask_error, self.ask_delay, self.speak_error = reply, ask_error, ask_delay, speak_error
        self.asked: list[tuple[str, dict]] = []
        self.spoken: list[str] = []

    def install(self, monkeypatch):
        monkeypatch.setattr(voice_pipeline, "BACKENDS", voice_pipeline.Backends(
            open_stt=lambda _s: self.stt, ask=self.ask, speak=self.speak, clip_audio=lambda _u: _wav(24000, 0.1)))
        return self

    async def ask(self, settings, text, context):
        self.asked.append((text, context))
        if self.ask_delay:
            await asyncio.sleep(self.ask_delay)
        if self.ask_error:
            raise self.ask_error
        return self.reply

    async def speak(self, text, voice):
        if self.speak_error:
            raise self.speak_error
        self.spoken.append(text)
        return URL


def _tone(seconds: float, amplitude: int = 8000, rate: int = 16000) -> bytes:
    n = int(seconds * rate)
    return struct.pack(f"<{n}h", *(int(amplitude * math.sin(2 * math.pi * 220 * i / rate)) for i in range(n)))


def _silence(seconds: float, rate: int = 16000) -> bytes:
    return b"\x00\x00" * int(seconds * rate)


def _wav(rate: int, seconds: float) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(_tone(seconds, rate=rate))
    return buf.getvalue()


@pytest.fixture
def env(monkeypatch):
    for k, v in {"HOMEHUB_VOICE_STT": "127.0.0.1:10310", "HOMEHUB_VOICE_JARVIS_URL": "http://jarvis.example.com",
                 "HOMEHUB_EXTERNAL_URL": "http://hub.example.com:8123"}.items():
        monkeypatch.setenv(k, v)
    for k in ("HOMEHUB_VOICE_MAX_S", "HOMEHUB_VOICE_NO_SPEECH_S", "HOMEHUB_VOICE_JARVIS_TIMEOUT_S",
              "HOMEHUB_VOICE_CONTINUE", "HOMEHUB_VOICE_STT_TIMEOUT_S"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(esphome, "RECONNECT_DELAYS", (0,))
    monkeypatch.setattr(voice_pipeline, "STREAM_CHUNK", 4096)
    VoiceClient.instances.clear()


def make_driver(flags: int = FULL, options: dict | None = None):
    hub, entry = make_hub("esphome", dict(DATA), ENTITIES, options=options or {})
    drv = ESPHomeDriver(hub, entry)
    drv._make_client = lambda host: VoiceClient(flags)  # type: ignore[method-assign]
    hub.drivers[entry.entry_id] = drv
    return hub, drv


async def speak_to(client: VoiceClient, chunks: list[bytes], chunk_bytes: int = 960) -> None:
    for blob in chunks:
        for i in range(0, len(blob), chunk_bytes):
            await client.handlers["audio"](blob[i:i + chunk_bytes], None)
    await asyncio.sleep(0)


async def run_turn(drv, client, audio, wake="Hey Jarvis", end_with_stop=False):
    port = await client.handlers["start"]("conv-1", 0, SimpleNamespace(volume_multiplier=1.0), wake)
    assert port == 0
    task = drv.voice._task
    await speak_to(client, audio)
    if end_with_stop:
        await client.handlers["stop"](False)
    await asyncio.wait_for(task, 5)
    return task


def test_energy_vad_finds_speech_and_its_end():
    vad = EnergyVAD(0.3)
    assert vad.process(_silence(0.5)) == []
    assert vad.process(_tone(0.5)) == ["start"]
    assert vad.process(_silence(0.2)) == []
    assert vad.process(_silence(0.2)) == ["end"]
    assert 400 <= vad.speech_frames * EnergyVAD.FRAME_MS <= 600
    noisy = EnergyVAD(0.3)
    assert noisy.process(_tone(1.0, amplitude=150)) == []  # a hum under the absolute floor is not speech


async def test_happy_path_sends_the_whole_event_sequence(env, monkeypatch):
    be = Backends().install(monkeypatch)
    hub, drv = make_driver()
    await drv.async_setup()
    client = drv._client
    assert client.handlers and client.handlers["audio"] is not None  # API audio
    st = hub.states.get(SAT)
    assert st.state == "idle" and st.attributes["supported_features"] == 3
    assert sorted(drv.entity_abilities(hub.entities[SAT])) == ["announce", "converse"]
    assert hub.states.get(VAD_SELECT).state == "default"

    await run_turn(drv, client, [_silence(0.3), _tone(1.0), _silence(1.0)])
    assert client.kinds() == [E.VOICE_ASSISTANT_RUN_START, E.VOICE_ASSISTANT_STT_START,
                              E.VOICE_ASSISTANT_STT_VAD_START, E.VOICE_ASSISTANT_STT_VAD_END,
                              E.VOICE_ASSISTANT_STT_END, E.VOICE_ASSISTANT_INTENT_START,
                              E.VOICE_ASSISTANT_INTENT_END, E.VOICE_ASSISTANT_TTS_START,
                              E.VOICE_ASSISTANT_TTS_END, E.VOICE_ASSISTANT_RUN_END]
    data = dict(client.events)
    assert data[E.VOICE_ASSISTANT_STT_END] == {"text": "what's the weather"}
    assert data[E.VOICE_ASSISTANT_INTENT_END] == {"conversation_id": "conv-1", "continue_conversation": "0"}
    assert data[E.VOICE_ASSISTANT_TTS_START] == {"text": "It is sunny."}
    assert data[E.VOICE_ASSISTANT_TTS_END] == {"url": URL}
    text, ctx = be.asked[0]
    assert text == "what's the weather"  # the transcript, unchanged: the gateway finds the speaker by it
    assert ctx["satellite"] == SAT and ctx["area_id"] == "office" and ctx["area"] == "Office"
    assert ctx["conversation_id"] == "conv-1" and ctx["source"] == "homehub_satellite"
    assert be.stt.audio and be.stt.closed
    assert hub.states.get(SAT).state == "idle"
    status = drv.voice.status()
    assert status["last_run"]["outcome"] == "ok" and status["last_run"]["ended_by"] == "vad"
    assert {"listen", "stt", "intent", "tts", "total"} <= set(status["last_run"]["timings_ms"])
    assert status["runs"] == 1 and status["last_error"] is None
    assert "weather" not in json.dumps(status) and "sunny" not in json.dumps(status)  # no transcripts kept
    await drv.async_unload()


async def test_a_question_continues_the_conversation(env, monkeypatch):
    Backends(reply="Which room?").install(monkeypatch)
    _hub, drv = make_driver()
    await drv.async_setup()
    await run_turn(drv, drv._client, [_tone(0.6), _silence(1.0)])
    assert dict(drv._client.events)[E.VOICE_ASSISTANT_INTENT_END]["continue_conversation"] == "1"
    monkeypatch.setenv("HOMEHUB_VOICE_CONTINUE", "0")
    await run_turn(drv, drv._client, [_tone(0.6), _silence(1.0)])
    assert drv._client.events[-4] == (E.VOICE_ASSISTANT_INTENT_END,
                                      {"conversation_id": "conv-1", "continue_conversation": "0"})
    await drv.async_unload()


async def test_finished_speaking_detection_sets_the_silence(env, monkeypatch):
    Backends().install(monkeypatch)
    hub, drv = make_driver()
    await drv.async_setup()
    await hub.call_service("select", "select_option", {"entity_id": VAD_SELECT, "option": "aggressive"})
    assert drv.voice.silence_seconds() == 0.25
    await run_turn(drv, drv._client, [_tone(0.6), _silence(0.4)])
    assert drv.voice.last_run.ended_by == "vad"
    await drv.async_unload()


async def _error_of(drv) -> dict:
    kinds = drv._client.kinds()
    assert kinds[-2:] == [E.VOICE_ASSISTANT_ERROR, E.VOICE_ASSISTANT_RUN_END]
    return drv._client.events[-2][1]


@pytest.mark.parametrize(("backends", "code", "words"), [
    (lambda: Backends(stt=FakeSTT(fail_open=PipelineError("stt-provider-unavailable", "Speech to text at x down"))),
     "stt-provider-unavailable", "Speech to text"),
    (lambda: Backends(stt=FakeSTT(text="")), "stt-no-text-recognized", "No words"),
    (lambda: Backends(ask_error=PipelineError("intent-failed", "Jarvis at x is unreachable")),
     "intent-failed", "unreachable"),
    (lambda: Backends(speak_error=HubError("speech engine down")), "tts-failed", "speech engine down"),
])
async def test_each_failure_is_an_error_event_then_run_end(env, monkeypatch, backends, code, words):
    be = backends().install(monkeypatch)
    hub, drv = make_driver()
    await drv.async_setup()
    await run_turn(drv, drv._client, [_tone(0.6), _silence(1.0)])
    err = await _error_of(drv)
    assert err["code"] == code and words in err["message"]
    assert drv.voice.last_error["code"] == code and drv.voice.last_run.outcome == "error"
    assert hub.states.get(SAT).state == "idle"
    assert be.stt.closed
    await drv.async_unload()


async def test_jarvis_taking_too_long_times_out(env, monkeypatch):
    monkeypatch.setenv("HOMEHUB_VOICE_JARVIS_TIMEOUT_S", "0.05")
    monkeypatch.setattr(voice_pipeline, "JARVIS_GUARD_S", 0.0)
    Backends(ask_delay=2.0).install(monkeypatch)
    _hub, drv = make_driver()
    await drv.async_setup()
    await run_turn(drv, drv._client, [_tone(0.6), _silence(1.0)])
    assert (await _error_of(drv))["code"] == "intent-timeout"
    await drv.async_unload()


async def test_stt_taking_too_long_times_out(env, monkeypatch):
    monkeypatch.setenv("HOMEHUB_VOICE_STT_TIMEOUT_S", "0.05")
    Backends(stt=FakeSTT(slow=2.0)).install(monkeypatch)
    _hub, drv = make_driver()
    await drv.async_setup()
    await run_turn(drv, drv._client, [_tone(0.6), _silence(1.0)])
    assert (await _error_of(drv))["code"] == "stt-timeout"
    await drv.async_unload()


async def test_an_unconfigured_hub_says_which_setting_is_missing(env, monkeypatch):
    monkeypatch.delenv("HOMEHUB_VOICE_STT")
    Backends().install(monkeypatch)
    _hub, drv = make_driver()
    await drv.async_setup()
    await run_turn(drv, drv._client, [])
    err = await _error_of(drv)
    assert err["code"] == "stt-provider-missing" and "HOMEHUB_VOICE_STT" in err["message"]
    assert "HOMEHUB_VOICE_STT" in VoiceSettings.from_env().problems()[0]
    await drv.async_unload()


async def test_nobody_speaking_ends_with_no_speech(env, monkeypatch):
    monkeypatch.setenv("HOMEHUB_VOICE_NO_SPEECH_S", "0.2")
    Backends().install(monkeypatch)
    _hub, drv = make_driver()
    await drv.async_setup()
    await run_turn(drv, drv._client, [_silence(0.1)])
    err = await _error_of(drv)
    assert err["code"] == "stt-no-text-recognized" and "No speech" in err["message"]
    # It says what arrived, so "too quiet" and "nothing came through" are told apart.
    assert "0.1 s of audio arrived" in err["message"] and "loudest moment was 0 RMS" in err["message"]
    await drv.async_unload()


def test_the_vad_reports_when_no_audio_arrived():
    from services.homehub.voice_pipeline import EnergyVAD
    assert EnergyVAD(1.0).heard() == "no audio arrived from the satellite"


async def test_a_command_that_never_stops_is_cut_at_the_max_duration(env, monkeypatch):
    monkeypatch.setenv("HOMEHUB_VOICE_MAX_S", "0.3")
    be = Backends().install(monkeypatch)
    _hub, drv = make_driver()
    await drv.async_setup()
    client = drv._client
    await client.handlers["start"]("c", 0, None, None)
    task = drv.voice._task
    await speak_to(client, [_tone(0.2)])
    await asyncio.sleep(0.4)  # the person keeps talking past the limit; no more audio is needed to end it
    await asyncio.wait_for(task, 5)
    assert drv.voice.last_run.ended_by == "max_duration" and drv.voice.last_run.outcome == "ok"
    assert E.VOICE_ASSISTANT_STT_VAD_END in client.kinds() and be.asked
    await drv.async_unload()


async def test_the_node_ending_its_audio_ends_the_command(env, monkeypatch):
    Backends().install(monkeypatch)
    _hub, drv = make_driver()
    await drv.async_setup()
    await run_turn(drv, drv._client, [_tone(0.5)], end_with_stop=True)
    assert drv.voice.last_run.ended_by == "satellite" and drv.voice.last_run.outcome == "ok"
    await drv.async_unload()


async def test_the_node_aborting_stops_the_run_without_more_events(env, monkeypatch):
    Backends().install(monkeypatch)
    _hub, drv = make_driver()
    await drv.async_setup()
    client = drv._client
    await client.handlers["start"]("c", 0, None, "Hey Jarvis")
    task = drv.voice._task
    await speak_to(client, [_tone(0.3)])
    await client.handlers["stop"](True)
    for _ in range(100):
        if task.done():
            break
        await asyncio.sleep(0.01)
    assert task.cancelled()
    assert E.VOICE_ASSISTANT_RUN_END not in client.kinds() and E.VOICE_ASSISTANT_ERROR not in client.kinds()
    assert drv.voice.last_run.outcome == "aborted"
    await drv.async_unload()


async def test_a_server_side_wake_word_is_refused_by_name(env, monkeypatch):
    Backends().install(monkeypatch)
    _hub, drv = make_driver()
    await drv.async_setup()
    client = drv._client
    await client.handlers["start"]("c", 2, None, None)  # USE_WAKE_WORD
    await asyncio.wait_for(drv.voice._task, 5)
    err = await _error_of(drv)
    assert err["code"] == "wake-engine-missing" and "On device" in err["message"]
    await drv.async_unload()


async def test_a_node_without_the_voice_flag_is_not_subscribed(env, monkeypatch):
    Backends().install(monkeypatch)
    hub, drv = make_driver(flags=int(F.ANNOUNCE))
    await drv.async_setup()
    assert drv._client.handlers is None and not drv.voice.subscribed
    assert drv.entity_abilities(hub.entities[SAT]) == ["announce"]
    with pytest.raises(ServiceNotFound, match="voice pipeline"):
        await hub.call_service("assist_satellite", "start_conversation", {"entity_id": SAT, "start_message": "Hi"})
    assert drv.voice.status()["features"] == ["announce"]
    await drv.async_unload()


async def test_a_reconnect_subscribes_again(env, monkeypatch):
    Backends().install(monkeypatch)
    hub, drv = make_driver()
    await drv.async_setup()
    first = drv._client
    await first.on_stop(False)
    assert hub.states.get(SAT).state == UNAVAILABLE and not drv.voice.subscribed
    for _ in range(50):
        await asyncio.sleep(0.01)
        if drv._client is not None:
            break
    second = drv._client
    assert second is not None and second is not first and second.handlers is not None
    assert drv.voice.subscribed and hub.states.get(SAT).state == "idle"
    await run_turn(drv, second, [_tone(0.5), _silence(1.0)])
    assert second.kinds()[-1] == E.VOICE_ASSISTANT_RUN_END and not first.kinds()
    await drv.async_unload()


async def test_start_conversation_speaks_first_and_the_next_run_knows_the_question(env, monkeypatch):
    be = Backends(reply="Done.").install(monkeypatch)
    hub, drv = make_driver()
    await drv.async_setup()
    await hub.call_service("assist_satellite", "start_conversation",
                           {"entity_id": SAT, "start_message": "Should I turn off the lights?",
                            "extra_system_prompt": "The person was asked about the lights."})
    assert drv._client.announced == [{"media_id": URL, "text": "Should I turn off the lights?",
                                      "preannounce_media_id": "", "start_conversation": True}]
    await run_turn(drv, drv._client, [_tone(0.5), _silence(1.0)], wake=None)
    ctx = be.asked[0][1]
    assert ctx["asked"] == "Should I turn off the lights?" and ctx["started_by"] == "start_conversation"
    assert ctx["extra_system_prompt"] == "The person was asked about the lights."
    with pytest.raises(HubError, match="start_message"):
        await hub.call_service("assist_satellite", "start_conversation", {"entity_id": SAT})
    monkeypatch.delenv("HOMEHUB_VOICE_JARVIS_URL")
    with pytest.raises(HubError, match="HOMEHUB_VOICE_JARVIS_URL"):
        await hub.call_service("assist_satellite", "start_conversation", {"entity_id": SAT, "start_message": "Hi"})
    await drv.async_unload()


async def test_a_satellite_turned_off_is_not_subscribed(env, monkeypatch):
    Backends().install(monkeypatch)
    hub, drv = make_driver(options={"voice_pipeline": False})
    await drv.async_setup()
    assert drv._client.handlers is None and not drv.voice.subscribed
    assert hub.states.get(SAT).attributes["supported_features"] == 1  # announce still works
    with pytest.raises(ServiceNotFound):
        await hub.call_service("assist_satellite", "start_conversation", {"entity_id": SAT, "start_message": "Hi"})
    await drv.async_unload()


async def test_udp_audio_for_nodes_without_api_audio(env, monkeypatch):
    Backends().install(monkeypatch)
    _hub, drv = make_driver(flags=int(F.VOICE_ASSISTANT | F.ANNOUNCE))
    await drv.async_setup()
    client = drv._client
    assert client.handlers["audio"] is None
    port = await client.handlers["start"]("c", 0, None, "Hey Jarvis")
    assert port and port > 0
    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(asyncio.DatagramProtocol, remote_addr=("127.0.0.1", port))
    audio = _tone(0.6) + _silence(1.0)
    for i in range(0, len(audio), 1024):
        transport.sendto(audio[i:i + 1024])
        await asyncio.sleep(0)
    await asyncio.wait_for(drv.voice._task, 5)
    transport.close()
    assert client.kinds()[-1] == E.VOICE_ASSISTANT_RUN_END and drv.voice.last_run.outcome == "ok"
    assert drv.voice._udp is None  # the port is closed with the run
    await drv.async_unload()


async def test_a_node_with_its_own_speaker_is_sent_the_audio(env, monkeypatch):
    Backends().install(monkeypatch)
    _hub, drv = make_driver(flags=FULL | int(F.SPEAKER))
    await drv.async_setup()
    await run_turn(drv, drv._client, [_tone(0.5), _silence(1.0)])
    kinds = drv._client.kinds()
    assert kinds[-4:] == [E.VOICE_ASSISTANT_TTS_END, E.VOICE_ASSISTANT_TTS_STREAM_START,
                          E.VOICE_ASSISTANT_TTS_STREAM_END, E.VOICE_ASSISTANT_RUN_END]
    assert sum(len(c) for c in drv._client.audio_out) == 2 * 1600  # 0.1 s at 24 kHz, as 16 kHz 16-bit
    await drv.async_unload()


def test_satellite_pcm_resamples_and_mixes_down():
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes(struct.pack("<4h", 100, 300, 100, 300) * 2400)
    pcm = voice_pipeline.to_satellite_pcm(buf.getvalue())
    samples = struct.unpack(f"<{len(pcm) // 2}h", pcm)
    assert len(samples) == 1600 and set(samples) == {200}


def test_chat_reply_text_reads_every_gateway_shape():
    assert voice_pipeline.chat_reply_text({"message": {"role": "assistant", "content": " Hi "}}) == "Hi"
    assert voice_pipeline.chat_reply_text({"choices": [{"message": {"content": "Yo"}}]}) == "Yo"
    assert voice_pipeline.chat_reply_text({"message": "Mission started"}) == "Mission started"
    assert voice_pipeline.chat_reply_text({"message": {"content": ""}}) is None


async def test_wyoming_client_speaks_the_protocol():
    got: dict = {"types": [], "audio": b""}

    async def serve(reader, writer):
        while line := await reader.readline():
            header = json.loads(line)
            data = json.loads(await reader.readexactly(header["data_length"])) if header.get("data_length") else {}
            payload = await reader.readexactly(header["payload_length"]) if header.get("payload_length") else b""
            got["types"].append(header["type"])
            if header["type"] == "transcribe":
                got["language"] = data["language"]
            got["audio"] += payload
            if header["type"] == "audio-stop":
                body = json.dumps({"text": " turn on the lamp "}).encode()
                writer.write(json.dumps({"type": "transcript", "data_length": len(body)}).encode() + b"\n" + body)
                await writer.drain()

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    stt = voice_pipeline.WyomingSTT(f"127.0.0.1:{port}", "en")
    await stt.open()
    await stt.send(b"\x01\x00" * 10)
    assert await stt.finish() == "turn on the lamp"
    await stt.close()
    server.close()
    assert got["types"] == ["transcribe", "audio-start", "audio-chunk", "audio-stop"]
    assert got["language"] == "en" and got["audio"] == b"\x01\x00" * 10

    dead = await asyncio.start_server(serve, "127.0.0.1", 0)
    dead_port = dead.sockets[0].getsockname()[1]
    dead.close()
    await dead.wait_closed()
    with pytest.raises(PipelineError) as exc:
        await voice_pipeline.WyomingSTT(f"127.0.0.1:{dead_port}", "en").open()
    assert exc.value.code == "stt-provider-unavailable" and "HOMEHUB_VOICE_STT" in exc.value.message


async def test_the_voice_api_lists_satellites_and_turns_one_off(env, monkeypatch):
    Backends().install(monkeypatch)
    hub, drv = make_driver()
    await drv.async_setup()
    saved = []
    runtime = SimpleNamespace(hub=hub, store=SimpleNamespace(save_config_entry=saved.append))
    app = FastAPI()
    app.include_router(voice_api.build_router(runtime, Auth(hub, SECRET)))
    client = TestClient(app)
    h = {"Authorization": f"Bearer {SECRET}"}
    assert client.get("/hub/voice").status_code == 401
    body = client.get("/hub/voice", headers=h).json()
    assert body["settings"]["stt"] == "127.0.0.1:10310" and body["settings"]["problems"] == []
    [sat] = body["satellites"]
    assert sat["entity_id"] == SAT and sat["subscribed"] and sat["audio"] == "api" and sat["area"] == "Office"
    assert set(sat["features"]) == {"voice_assistant", "api_audio", "announce", "start_conversation"}
    entry_id = drv.entry.entry_id
    assert client.patch(f"/hub/voice/{entry_id}", headers=h, json={"enabled": "no"}).status_code == 400
    off = client.patch(f"/hub/voice/{entry_id}", headers=h, json={"enabled": False}).json()
    assert off["enabled"] is False and off["subscribed"] is False and saved
    assert drv._client.unsubscribed == 1
    on = client.patch(f"/hub/voice/{entry_id}", headers=h, json={"enabled": True}).json()
    assert on["subscribed"] is True
    assert client.patch("/hub/voice/none", headers=h, json={"enabled": True}).status_code == 404
    await drv.async_unload()


WAKE = "select.test_satellite_wake_word"


async def test_the_nodes_wake_word_is_read_shown_and_can_be_changed(env, monkeypatch):
    """The node keeps its own wake word (HA set it); one nobody says looks like a dead wake word."""
    Backends().install(monkeypatch)
    hub, drv = make_driver()
    await drv.async_setup()
    for _ in range(5):
        await asyncio.sleep(0)
    st = hub.states.get(WAKE)
    assert st.state == "Okay Nabu" and st.attributes["options"] == ["Okay Nabu", "Hey Jarvis", "No wake word"]
    assert drv.voice.status()["wake_words"]["active"] == ["Okay Nabu"]

    await drv.async_call(hub.entities[WAKE], "select", "select_option", {"option": "Hey Jarvis"})
    assert drv._client.active_wake_words == ["hey_jarvis"]
    assert hub.states.get(WAKE).state == "Hey Jarvis"
    with pytest.raises(HubError, match="not one of"):
        await drv.async_call(hub.entities[WAKE], "select", "select_option", {"option": "Hey Mars"})


async def test_a_node_that_cannot_report_its_wake_words_says_so(env, monkeypatch):
    Backends().install(monkeypatch)
    hub, drv = make_driver()
    original = drv._make_client

    def failing(host):
        c = original(host)
        c.config_error = RuntimeError("connection reset")
        return c

    drv._make_client = failing  # type: ignore[method-assign]
    await drv.async_setup()
    for _ in range(5):
        await asyncio.sleep(0)
    st = hub.states.get(WAKE)
    assert st.state == UNAVAILABLE and "did not report its wake words" in st.attributes["reason"]
    assert "connection reset" in drv.voice.status()["wake_words"]["error"]



async def test_a_wake_word_fixed_in_firmware_is_not_reported_as_missing(env, monkeypatch, caplog):
    Backends().install(monkeypatch)
    hub, drv = make_driver()
    original = drv._make_client

    def fixed(host):
        c = original(host)
        c.available_wake_words, c.active_wake_words = [], []
        return c

    drv._make_client = fixed  # type: ignore[method-assign]
    await drv.async_setup()
    for _ in range(5):
        await asyncio.sleep(0)
    st = hub.states.get(WAKE)
    assert st.state == UNAVAILABLE and "fixed in this satellite's firmware" in st.attributes["reason"]
    assert "will not wake" not in caplog.text

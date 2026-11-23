"""Tests for Phase 2A whisper mode, echo ring, and sound guard."""
from __future__ import annotations

import os

import pytest

from services.homehub import echo_ring, sound_guard, voice_pipeline
from services.homehub.core import HubError
from services.homehub.drivers import esphome
from services.homehub.tests.conftest import make_hub
from services.homehub.tests.test_voice_pipeline import (
    DATA,
    ENTITIES,
    FULL,
    MAC,
    Backends,
    FakeSTT,
    VoiceClient,
    _silence,
    _tone,
    run_turn,
)


def _setup_env(monkeypatch):
    for k, v in {"HOMEHUB_VOICE_STT": "127.0.0.1:10310",
                 "HOMEHUB_VOICE_JARVIS_URL": "http://jarvis.example.com",
                 "HOMEHUB_EXTERNAL_URL": "http://hub.example.com:8123"}.items():
        monkeypatch.setenv(k, v)
    for k in ("HOMEHUB_VOICE_MAX_S", "HOMEHUB_VOICE_NO_SPEECH_S", "HOMEHUB_VOICE_JARVIS_TIMEOUT_S",
              "HOMEHUB_VOICE_CONTINUE", "HOMEHUB_VOICE_STT_TIMEOUT_S"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(esphome, "RECONNECT_DELAYS", (0,))
    monkeypatch.setattr(voice_pipeline, "STREAM_CHUNK", 4096)
    monkeypatch.setattr(voice_pipeline, "speech_classifier", lambda _engine, _mode: None)
    VoiceClient.instances.clear()


def _entities_with_whisper():
    ents = [dict(e) for e in ENTITIES]
    ents.append({"entity_id": "binary_sensor.test_satellite_last_request_whispered",
                 "unique_id": f"{MAC}-last_request_whispered", "device_id": "dev1"})
    ents.append({"entity_id": "number.test_satellite_assistant_volume",
                 "unique_id": f"{MAC}-assistant_volume", "device_id": "dev1"})
    return ents


@pytest.mark.asyncio
async def test_whisper_on_ducks_and_restores(monkeypatch):
    _setup_env(monkeypatch)
    monkeypatch.setenv("HOMEHUB_VOICE_WHISPER_TTS_VOICE", "soft-voice")
    monkeypatch.setenv("HOMEHUB_VOICE_WHISPER_VOLUME", "0.22")
    monkeypatch.setattr(voice_pipeline, "current_settings", voice_pipeline.VoiceSettings.from_env)
    from services.homehub.drivers.esphome import ESPHomeDriver
    hub, entry = make_hub("esphome", dict(DATA), _entities_with_whisper(), options={})
    drv = ESPHomeDriver(hub, entry)
    drv._make_client = lambda host, port=None: VoiceClient(FULL)
    hub.drivers[entry.entry_id] = drv
    await drv.async_setup()
    hub.publish("binary_sensor.test_satellite_last_request_whispered", "on", {})
    hub.publish("number.test_satellite_assistant_volume", "0.8", {})
    calls = []

    async def fake_call_service(domain, service, data=None, *a, **k):
        calls.append((domain, service, dict(data or {})))
        return {"changed_states": [], "response": None}

    monkeypatch.setattr(hub, "call_service", fake_call_service)
    b = Backends(stt=FakeSTT(text="hi"), reply="Hello there.")
    b.install(monkeypatch)
    client = VoiceClient.instances[-1]
    await run_turn(drv, client, [_tone(0.5), _silence(1.0)])
    duck_calls = [c for c in calls if c[1] == "set_value"]
    assert any(c[2].get("value") == 0.22 for c in duck_calls)
    assert any(c[2].get("value") == 0.8 for c in duck_calls)
    assert drv.voice.last_run.whispered is True
    assert drv.voice.last_run.public()["whispered"] is True
    assert b.voices and b.voices[0] == "soft-voice"
    await drv.async_unload()


@pytest.mark.asyncio
async def test_whisper_off_leaves_volume(monkeypatch):
    _setup_env(monkeypatch)
    monkeypatch.setattr(voice_pipeline, "current_settings", voice_pipeline.VoiceSettings.from_env)
    from services.homehub.drivers.esphome import ESPHomeDriver
    hub, entry = make_hub("esphome", dict(DATA), [dict(e) for e in ENTITIES], options={})
    drv = ESPHomeDriver(hub, entry)
    drv._make_client = lambda host, port=None: VoiceClient(FULL)
    hub.drivers[entry.entry_id] = drv
    await drv.async_setup()
    client = VoiceClient.instances[-1]
    b = Backends(stt=FakeSTT(text="hi"), reply="Hello.")
    b.install(monkeypatch)
    calls = []

    async def fake_call_service(domain, service, data=None, *a, **k):
        calls.append((domain, service, dict(data or {})))
        return {"changed_states": [], "response": None}

    monkeypatch.setattr(hub, "call_service", fake_call_service)
    await run_turn(drv, client, [_tone(0.5), _silence(1.0)])
    assert not any(c[1] == "set_value" for c in calls)
    await drv.async_unload()


def test_invalid_whisper_volume_raises():
    os.environ["HOMEHUB_VOICE_WHISPER_VOLUME"] = "0"
    try:
        with pytest.raises(HubError):
            voice_pipeline.VoiceSettings.from_env()
    finally:
        del os.environ["HOMEHUB_VOICE_WHISPER_VOLUME"]
    os.environ["HOMEHUB_VOICE_WHISPER_VOLUME"] = "1.5"
    try:
        with pytest.raises(HubError):
            voice_pipeline.VoiceSettings.from_env()
    finally:
        del os.environ["HOMEHUB_VOICE_WHISPER_VOLUME"]


@pytest.mark.asyncio
async def test_context_includes_whispered():
    from services.homehub.drivers.esphome import ESPHomeDriver
    hub, entry = make_hub("esphome", dict(DATA), [dict(e) for e in ENTITIES], options={})
    drv = ESPHomeDriver(hub, entry)
    drv._make_client = lambda host, port=None: VoiceClient(FULL)
    hub.drivers[entry.entry_id] = drv
    run = voice_pipeline.Run(id="r", conversation_id="c", wake_word=None)
    run.whispered = True
    ctx = drv.voice._context(run)
    assert ctx.get("whispered") is True
    run2 = voice_pipeline.Run(id="r2", conversation_id="c", wake_word=None)
    ctx2 = drv.voice._context(run2)
    assert "whispered" not in ctx2


@pytest.mark.asyncio
async def test_echo_ring_notify_progress_music_clear(monkeypatch):
    from services.homehub.core import DeviceEntry, EntityEntry
    hub, _ = make_hub()
    hub.devices["echo1"] = DeviceEntry(id="echo1", name="echo", config_entries=["entry1"])
    hub.entities["light.echo_light_ring"] = EntityEntry("light.echo_light_ring", "test", "uid", "entry1", "echo1", original_name="Light Ring")
    hub.entities["number.echo_light_ring_fill"] = EntityEntry("number.echo_light_ring_fill", "test", "uid2", "entry1", "echo1", original_name="Light Ring Fill")
    calls = []

    async def fake_call_service(domain, service, data=None, *a, **k):
        calls.append((domain, service, dict(data or {})))
        return {"changed_states": [], "response": None}

    monkeypatch.setattr(hub, "call_service", fake_call_service)
    await echo_ring.notify(hub, device_id="echo1", color="#ff0000", effect="Pulse", brightness=200, duration_s=0)
    await echo_ring.progress(hub, device_id="echo1", percent=50, color="#00ff00")
    await echo_ring.music_mode(hub, device_id="echo1", effect="Music beat", brightness=200)
    await echo_ring.clear(hub, device_id="echo1")
    with pytest.raises(HubError):
        await echo_ring.music_mode(hub, device_id="echo1", effect="Nope")
    hub2, _ = make_hub()
    with pytest.raises(HubError):
        await echo_ring.notify(hub2, device_id="dev1", color="#ff0000")
    assert any(c[1] == "turn_on" for c in calls)
    assert any(c[0] == "number" and c[1] == "set_value" for c in calls)


@pytest.mark.asyncio
async def test_echo_ring_flash_all(monkeypatch):
    from services.homehub.core import DeviceEntry, EntityEntry
    hub, _ = make_hub()
    hub.devices["echo1"] = DeviceEntry(id="echo1", name="echo", config_entries=["entry1"])
    hub.entities["light.echo_light_ring"] = EntityEntry("light.echo_light_ring", "test", "uid", "entry1", "echo1", original_name="Light Ring")
    calls = []

    async def fake_call_service(domain, service, data=None, *a, **k):
        calls.append((domain, service, dict(data or {})))
        return {"changed_states": [], "response": None}

    monkeypatch.setattr(hub, "call_service", fake_call_service)
    res = await echo_ring.flash_all(hub, color="#ff0000")
    assert "targeted" in res and "failed" in res
    assert len(res["targeted"]) == 1


@pytest.mark.asyncio
async def test_sound_guard_scenarios():
    guard = sound_guard.SoundGuard()
    a1 = guard.evaluate(device_id="e1", entity_id="event.e1_sound", room="Kitchen",
                        event_type="smoke_or_co_alarm", now=0.0, nobody_home=False)
    assert a1 is None
    a2 = guard.evaluate(device_id="e2", entity_id="event.e2_sound", room="Living",
                        event_type="smoke_or_co_alarm", now=5.0, nobody_home=False)
    assert a2 is not None
    assert a2.priority == 5
    assert a2.flash_rings is True
    assert a2.corroborated is True
    assert set(a2.rooms) == {"Kitchen", "Living"}
    guard2 = sound_guard.SoundGuard()
    a3 = guard2.evaluate(device_id="e1", entity_id="event.e1_sound", room="Kitchen",
                         event_type="smoke_or_co_alarm", now=0.0, nobody_home=True)
    assert a3 is not None and a3.priority == 5
    a4 = guard2.evaluate(device_id="e1", entity_id="event.e1_sound", room="Kitchen",
                         event_type="smoke_or_co_alarm", now=10.0, nobody_home=True)
    assert a4 is None
    g3 = sound_guard.SoundGuard()
    bc = g3.evaluate(device_id="e1", entity_id="event.e1_sound", room="Nursery",
                     event_type="baby_cry", now=0.0)
    assert bc is not None and bc.priority == 3 and bc.drop_in_room == "Nursery" and bc.flash_rings is False
    assert sound_guard.extract_sound_event("event.x_sound", "foo") is None
    assert sound_guard.extract_sound_event("event.x_sound", "unavailable") is None


def test_sound_guard_extract_event_type_from_state():
    from services.homehub.core import State
    st = State(entity_id="event.kitchen_sound",
               state="2026-11-22T18:45:00+00:00",
               attributes={"event_type": "smoke_or_co_alarm"})
    assert sound_guard.extract_sound_event("event.kitchen_sound", st) == "smoke_or_co_alarm"
    # dict form too
    assert sound_guard.extract_sound_event("event.kitchen_sound",
                                          {"state": "2026-11-22T18:45:00+00:00",
                                           "attributes": {"event_type": "smoke_or_co_alarm"}}) == "smoke_or_co_alarm"


def test_sound_guard_emergency_household_cooldown():
    guard = sound_guard.SoundGuard()
    a1 = guard.evaluate(device_id="e1", entity_id="event.e1_sound", room="Kitchen",
                        event_type="smoke_or_co_alarm", now=0.0, nobody_home=False)
    assert a1 is None
    a2 = guard.evaluate(device_id="e2", entity_id="event.e2_sound", room="Hallway",
                        event_type="smoke_or_co_alarm", now=1.0, nobody_home=False)
    assert a2 is not None and a2.priority == 5
    a3 = guard.evaluate(device_id="e3", entity_id="event.e3_sound", room="Living Room",
                        event_type="smoke_or_co_alarm", now=3.0, nobody_home=False)
    assert a3 is None


@pytest.mark.asyncio
async def test_whisper_volume_percentage_scale(monkeypatch):
    _setup_env(monkeypatch)
    monkeypatch.setenv("HOMEHUB_VOICE_WHISPER_TTS_VOICE", "soft-voice")
    monkeypatch.setenv("HOMEHUB_VOICE_WHISPER_VOLUME", "0.22")
    monkeypatch.setattr(voice_pipeline, "current_settings", voice_pipeline.VoiceSettings.from_env)
    from services.homehub.core import EntityEntry
    from services.homehub.drivers.esphome import ESPHomeDriver
    hub, entry = make_hub("esphome", dict(DATA), [dict(e) for e in ENTITIES], options={})
    # device_id dev1 already; add whispered + assistant_volume entities
    spec1 = {"entity_id": "binary_sensor.test_satellite_last_request_whispered",
             "platform": "test", "config_entry_id": "entry1", "device_id": "dev1",
             "unique_id": f"{MAC}-last_request_whispered"}
    spec2 = {"entity_id": "number.test_satellite_assistant_volume",
             "platform": "test", "config_entry_id": "entry1", "device_id": "dev1",
             "unique_id": f"{MAC}-assistant_volume"}
    hub.entities[spec1["entity_id"]] = EntityEntry(**spec1)
    hub.entities[spec2["entity_id"]] = EntityEntry(**spec2)
    drv = ESPHomeDriver(hub, entry)
    drv._make_client = lambda host, port=None: VoiceClient(FULL)
    hub.drivers[entry.entry_id] = drv
    await drv.async_setup()
    hub.publish("binary_sensor.test_satellite_last_request_whispered", "on", {})
    hub.publish("number.test_satellite_assistant_volume", "60", {"max": 100})
    calls = []

    async def fake_call_service(domain, service, data=None, *a, **k):
        calls.append((domain, service, dict(data or {})))
        return {"changed_states": [], "response": None}

    monkeypatch.setattr(hub, "call_service", fake_call_service)
    b = Backends(stt=FakeSTT(text="hi"), reply="Hello there.")
    b.install(monkeypatch)
    client = VoiceClient.instances[-1]
    await run_turn(drv, client, [_tone(0.5), _silence(1.0)])
    duck_calls = [c for c in calls if c[1] == "set_value"]
    assert any(c[2].get("value") == 22.0 for c in duck_calls)
    assert any(c[2].get("value") == 60.0 for c in duck_calls)
    await drv.async_unload()


@pytest.mark.asyncio
async def test_echo_ring_flash_all_animation_payload(monkeypatch):
    from services.homehub.core import DeviceEntry, EntityEntry
    hub, _ = make_hub()
    hub.devices["echo1"] = DeviceEntry(id="echo1", name="echo", config_entries=["entry1"])
    hub.entities["light.echo_light_ring"] = EntityEntry("light.echo_light_ring", "test", "uid", "entry1", "echo1",
                                                         original_name="Light Ring")
    # mark esphome animation service present
    hub.services[("esphome", "echo_light_ring_animation")] = None
    calls = []

    async def fake_call_service(domain, service, data=None, *a, **k):
        calls.append((domain, service, dict(data or {})))
        return {"changed_states": [], "response": None}

    monkeypatch.setattr(hub, "call_service", fake_call_service)
    res = await echo_ring.flash_all(hub, color="#ff0000", repeat=3, brightness=255, frames=None)
    assert len(res["targeted"]) == 1
    anim_calls = [c for c in calls if c[0] == "esphome" and c[1] == "echo_light_ring_animation"]
    assert anim_calls
    payload = anim_calls[0][2]
    assert payload["repeat"] == 3
    assert payload["brightness"] == 255
    assert payload["frames"] == [
        "200:" + ",".join(["ff0000"] * 12),
        "200:" + ",".join(["000000"] * 12),
    ]


@pytest.mark.asyncio
async def test_echo_ring_target_guards():
    from services.homehub.core import HubError
    hub, _ = make_hub()
    with pytest.raises(HubError, match="device_id or entity_id is required"):
        echo_ring._light_ring_entity(hub, None, None)
    with pytest.raises(HubError, match="device_id or entity_id is required"):
        echo_ring._fill_entity(hub, None, None)

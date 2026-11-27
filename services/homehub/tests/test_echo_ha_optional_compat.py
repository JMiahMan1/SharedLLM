"""Phase 5: Echo satellites docs + Home Assistant optional-integration compat."""
from __future__ import annotations

import pytest

from services.execution import satellite_timers
from services.gateway import fast_drop_in
from services.homehub import echo_ring, ha_ws_compat, sound_guard, voice_pipeline
from services.homehub.core import DeviceEntry, EntityEntry, State
from services.homehub.tests.conftest import make_hub


def test_voice_settings_whisper_mode_without_ha(monkeypatch):
    monkeypatch.delenv("HA_URL", raising=False)
    monkeypatch.delenv("HA_TOKEN", raising=False)
    monkeypatch.setenv("HOMEHUB_VOICE_WHISPER_VOLUME", "0.22")
    monkeypatch.setenv("HOMEHUB_VOICE_WHISPER_TTS_VOICE", "soft-voice")
    settings = voice_pipeline.VoiceSettings.from_env()
    assert settings.whisper_volume == 0.22
    assert settings.whisper_tts_voice == "soft-voice"


@pytest.mark.asyncio
async def test_echo_ring_services_run_directly_against_hub_without_ha(monkeypatch):
    monkeypatch.delenv("HA_URL", raising=False)
    monkeypatch.delenv("HA_TOKEN", raising=False)
    hub, _ = make_hub()
    hub.devices["echo1"] = DeviceEntry(id="echo1", name="echo", config_entries=["entry1"])
    hub.entities["light.echo_light_ring"] = EntityEntry(
        "light.echo_light_ring", "test", "uid", "entry1", "echo1", original_name="Light Ring"
    )
    hub.entities["number.echo_light_ring_fill"] = EntityEntry(
        "number.echo_light_ring_fill", "test", "uid2", "entry1", "echo1", original_name="Light Ring Fill"
    )
    calls = []

    async def fake_call_service(domain, service, data=None, *a, **k):
        calls.append((domain, service, dict(data or {})))
        return {"changed_states": [], "response": None}

    monkeypatch.setattr(hub, "call_service", fake_call_service)
    await echo_ring.notify(hub, device_id="echo1", color="#ff0000")
    await echo_ring.progress(hub, device_id="echo1", percent=50, color="#00ff00")
    await echo_ring.music_mode(hub, device_id="echo1", effect="Music beat", brightness=200)
    await echo_ring.clear(hub, device_id="echo1")
    assert any(c[1] == "turn_on" for c in calls)
    assert any(c[0] == "number" and c[1] == "set_value" for c in calls)


def test_sound_guard_processes_state_event_without_ha(monkeypatch):
    monkeypatch.delenv("HA_URL", raising=False)
    monkeypatch.delenv("HA_TOKEN", raising=False)
    st = State(
        entity_id="event.kitchen_sound",
        state="2026-11-22T18:00:00+00:00",
        attributes={"event_type": "smoke_or_co_alarm"},
    )
    event_type = sound_guard.extract_sound_event("event.kitchen_sound", st)
    assert event_type == "smoke_or_co_alarm"
    guard = sound_guard.SoundGuard()
    alert = guard.evaluate(
        device_id="kitchen",
        entity_id="event.kitchen_sound",
        room="Kitchen",
        event_type=event_type,
        now=0.0,
        nobody_home=True,
    )
    assert alert is not None
    assert alert.priority == 5


@pytest.mark.asyncio
async def test_ha_ws_compat_intact_and_functional_without_ha(monkeypatch):
    monkeypatch.delenv("HA_URL", raising=False)
    monkeypatch.delenv("HA_TOKEN", raising=False)
    assert hasattr(ha_ws_compat, "COMMANDS")
    assert "network/url" in ha_ws_compat.COMMANDS
    hub, _ = make_hub()
    results = []

    class Conn:
        def __init__(self):
            self.hub = hub

        async def result(self, mid, value=None):
            results.append((mid, value))

        async def error(self, mid, code, message):
            results.append(("error", mid, code))

    conn = Conn()
    await ha_ws_compat.network_url(conn, 1, {})
    assert results
    assert results[0][0] == 1
    assert isinstance(results[0][1], dict)


def test_fast_drop_in_and_satellite_timers_without_ha(monkeypatch):
    monkeypatch.delenv("HA_URL", raising=False)
    monkeypatch.delenv("HA_TOKEN", raising=False)
    parsed = fast_drop_in.parse_drop_in_command("drop in on the kitchen")
    assert parsed == {"tool": "DropInRequest", "payload": {"action": "start", "target_room": "kitchen"}}
    states = [{"entity_id": "assist_satellite.kitchen", "attributes": {"friendly_name": "Kitchen satellite"}}]
    assert satellite_timers.resolve_room_satellite(states, "kitchen") == "assist_satellite.kitchen"
    assert satellite_timers.resolve_room_satellite(
        states, "", fallback_satellite="assist_satellite.living"
    ) == "assist_satellite.living"

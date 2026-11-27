"""The voice context names the satellite's own speaker, so "play music" plays where it was asked."""
from __future__ import annotations

from types import SimpleNamespace

from services.homehub.drivers.esphome import ESPHomeDriver
from services.homehub.tests.conftest import make_hub
from services.homehub.tests.test_voice_pipeline import DATA, ENTITIES, FULL, MAC, SAT, VoiceClient, env  # noqa: F401

SPEAKER = "media_player.test_satellite_speaker"


def _driver(entities):
    hub, entry = make_hub("esphome", dict(DATA), entities)
    drv = ESPHomeDriver(hub, entry)
    drv._make_client = lambda host, port=None: VoiceClient(FULL)  # type: ignore[method-assign]
    hub.drivers[entry.entry_id] = drv
    return drv


def _run():
    return SimpleNamespace(conversation_id="conv-1", wake_word="Hey Jarvis", whispered=False)


async def test_the_satellites_own_speaker_is_in_the_context(env):  # noqa: F811
    drv = _driver([*ENTITIES, {"entity_id": SPEAKER, "unique_id": f"{MAC}-media_player"}])
    await drv.async_setup()
    ctx = drv.voice._context(_run())
    assert ctx["media_player"] == SPEAKER and ctx["satellite"] == SAT
    await drv.async_unload()


async def test_a_satellite_without_a_speaker_names_none(env):  # noqa: F811
    drv = _driver(list(ENTITIES))
    await drv.async_setup()
    assert "media_player" not in drv.voice._context(_run())
    await drv.async_unload()

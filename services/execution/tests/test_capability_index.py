"""The hub's capability index as execution uses it: loading, and the tools that pick devices with it.

Every id and name here is made up; names are chosen to mislead the old name heuristics.
"""
from __future__ import annotations

import pytest

from services.execution import announce_handlers, capability_index
from services.execution.capability_index import CapabilityIndex, CapabilityIndexError
from services.execution.handlers import tv_stage

HUB = "http://192.0.2.10:8123"

ENTITIES = [
    {"entity_id": "media_player.box_one", "domain": "media_player", "integration": "roku", "platform": "roku",
     "area_id": "den", "available": True, "abilities": ["launch_app", "play_media", "power"],
     "details": {"device_class": "tv"}},
    {"entity_id": "media_player.samsung_kitchen_speaker", "domain": "media_player", "integration": "music_assistant",
     "platform": "music_assistant", "area_id": "kitchen", "available": True,
     "abilities": ["announce", "play_media", "tts", "volume"], "details": {}},
    {"entity_id": "light.porch", "domain": "light", "integration": "wiz", "platform": "wiz", "area_id": "porch",
     "available": False, "abilities": ["dim", "power"], "details": {}},
]


class FakeResp:
    def __init__(self, status, body):
        self.status, self._body = status, body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def json(self, content_type=None):
        return self._body


class FakeSession:
    def __init__(self, status, body):
        self.status, self.body, self.calls = status, body, []

    def get(self, url, headers=None, timeout=None):
        self.calls.append((url, headers))
        return FakeResp(self.status, self.body)


@pytest.fixture(autouse=True)
def _clear():
    capability_index.clear_cache()
    yield
    capability_index.clear_cache()


def _session(monkeypatch, status, body):
    session = FakeSession(status, body)

    async def get_session(host, verify=True):
        return session

    import services.execution.http_client as http_client

    monkeypatch.setattr(http_client, "get_session", get_session)
    return session


async def test_load_parses_caches_and_tells_a_plain_ha_from_a_failure(monkeypatch, real_capability_index):
    s = _session(monkeypatch, 200, {"entities": ENTITIES, "abilities": {"dim": "x"},
                                    "areas": [{"area_id": "den", "name": "Den", "aliases": ["Snug"]}]})
    idx = await capability_index.load(HUB, "tok")
    assert idx.with_ability("announce") == ["media_player.samsung_kitchen_speaker"]
    assert idx.with_ability("power", area_id="den") == ["media_player.box_one"]
    assert idx.with_ability("dim", available_only=True) == []
    assert idx.tv_platform("media_player.box_one") == "roku"
    assert idx.area_id_for("snug") == "den" and idx.area_name("den") == "Den"
    await capability_index.load(HUB, "tok")
    assert len(s.calls) == 1 and s.calls[0][0] == f"{HUB}/api/homehub/capabilities"
    capability_index.clear_cache()
    _session(monkeypatch, 404, None)
    assert await capability_index.load(HUB, "tok") is None
    capability_index.clear_cache()
    _session(monkeypatch, 500, None)
    with pytest.raises(CapabilityIndexError):
        await capability_index.load(HUB, "tok")
    with pytest.raises(CapabilityIndexError):  # routing never falls back on a failure, only on a 404
        await capability_index.routing_index(HUB, "tok", "a test")
    with pytest.raises(CapabilityIndexError):
        await capability_index.load("", "tok")


def test_the_index_answer_wins_over_name_guesses():
    # The name says Samsung; the integration that owns it says Music Assistant.
    attrs = {"friendly_name": "Samsung kitchen speaker"}
    assert announce_handlers.detect_tv_type("media_player.samsung_kitchen_speaker", "idle", attrs) == "samsung"
    assert announce_handlers.detect_tv_type("media_player.samsung_kitchen_speaker", "idle", attrs,
                                            platform="music_assistant") == "music_assistant"


async def test_classification_uses_the_index_for_owner_announce_and_screen(monkeypatch):
    idx = CapabilityIndex(ENTITIES)

    async def routing_index(ha_url, ha_token, purpose):
        return idx

    monkeypatch.setattr(capability_index, "routing_index", routing_index)
    monkeypatch.setattr(announce_handlers, "_get_device_registry", lambda: None)
    states = [
        {"entity_id": "media_player.box_one", "state": "off", "attributes": {"friendly_name": "Box one"}},
        {"entity_id": "media_player.samsung_kitchen_speaker", "state": "idle",
         "attributes": {"friendly_name": "Samsung kitchen speaker", "supported_features": 0}},
    ]
    out = {d["entity_id"]: d for d in await announce_handlers.classify_communication_devices(HUB, "tok", states)}
    box = out["media_player.box_one"]
    assert box["platform"] == "roku" and box["kind"] == "tv" and box["has_screen"] is True
    speaker = out["media_player.samsung_kitchen_speaker"]
    assert speaker["platform"] == "music_assistant" and speaker["kind"] == "speaker"
    assert speaker["announce_capable"] is True  # from the index, though its feature bits say nothing


async def test_tv_stage_reads_owners_from_the_index(monkeypatch):
    async def routing_index(ha_url, ha_token, purpose):
        return CapabilityIndex(ENTITIES)

    monkeypatch.setattr(capability_index, "routing_index", routing_index)
    assert await tv_stage.ha_entity_platforms(HUB, "tok") == {
        "media_player.box_one": "roku", "media_player.samsung_kitchen_speaker": "music_assistant"}

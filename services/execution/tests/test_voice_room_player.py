"""Room -> player resolution for voice satellite fast path."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.execution import capability_index, ha_client, ha_health
from services.execution.capability_index import CapabilityIndex
from services.execution.handlers import audiobookshelf, media
from services.execution.schemas import AudiobookshelfRequest, MediaTransportRequest, UserContext

HUB = "http://192.0.2.10:8123"
AREAS = [
    {"area_id": "area_1", "name": "Den", "aliases": ["Snug"]},
]
ENTITIES = [
    # capability_index test helper shape
]


def _cap(eid, integration, area, abilities):
    return {"entity_id": eid, "domain": eid.split(".", 1)[0], "integration": integration, "platform": integration,
            "area_id": area, "device_id": None, "available": True, "abilities": sorted(abilities),
            "details": {}, "services": []}



IDX = CapabilityIndex([
    _cap("media_player.den_speaker", "esphome", "area_1", {"play_media", "announce"}),
], {}, list(AREAS))


@pytest.fixture(autouse=True)
def _patch(monkeypatch):
    async def routing_index(ha_url, ha_token, purpose):
        return IDX

    monkeypatch.setattr(capability_index, "routing_index", routing_index)
    monkeypatch.setattr(ha_health, "is_down", lambda url: False)

    async def no_states(ha_url, ha_token):
        return []

    monkeypatch.setattr(ha_client, "get_states", no_states)
    monkeypatch.setattr(ha_client, "get_state", AsyncMock(return_value=None))
    calls = []
    async def fake_call_service(*a, **k):
        calls.append((a, k))
        return {"ok": True}
    monkeypatch.setattr(ha_client, "call_service", fake_call_service)
    return calls


@pytest.mark.asyncio
async def test_resolve_room_player_picks_area_player(_patch):
    ctx = SimpleNamespace(ha_url=HUB, ha_token="tok")
    picked = await media.resolve_room_player(ctx, "den", None)
    assert picked == "media_player.den_speaker"


@pytest.mark.asyncio
async def test_resolve_room_player_unresolvable_returns_none(_patch):
    ctx = SimpleNamespace(ha_url=HUB, ha_token="tok")
    assert await media.resolve_room_player(ctx, "kitchen", None) is None


@pytest.mark.asyncio
async def test_unresolvable_room_clear_failure_no_service(_patch):
    req = MediaTransportRequest(
        user_context=UserContext(user="tester", ha_url=HUB, ha_token="tok"),
        entity_id=None,
        device_name="kitchen",
        command="pause",
    )
    result = await media.handle_media_transport(req)
    assert result.status == "FAILURE"
    assert result.message == "I couldn't find a speaker in kitchen."
    assert _patch == []



@pytest.mark.asyncio
async def test_abs_unresolvable_room_clear_failure(_patch):
    req = AudiobookshelfRequest(
        user_context=UserContext(user="tester", ha_url=HUB, ha_token="tok",
                                 audiobookshelf_url="http://abs.test", audiobookshelf_api_key="key"),
        action="resume",
        device_name="kitchen",
    )
    result = await audiobookshelf.handle_audiobookshelf(req)
    assert result.status == "FAILURE"
    assert result.message == "I couldn't find a speaker in kitchen."


# A device named by its own name, not a room: four players for one TV, none in a known area.
OFFICE_STATES = [
    {"entity_id": "media_player.office_tv", "state": "idle", "attributes": {"friendly_name": "Office TV Remote"}},
    {"entity_id": "media_player.office_tv_chrome", "state": "idle", "attributes": {"friendly_name": "Office TV"}},
    {"entity_id": "media_player.office_tv_3", "state": "paused", "attributes": {"friendly_name": "Office TV"}},
    {"entity_id": "media_player.office_tv_cast", "state": "idle", "attributes": {"friendly_name": "Office TV"}},
    {"entity_id": "light.office_tv_backlight", "state": "on", "attributes": {"friendly_name": "Office TV backlight"}},
    {"entity_id": "media_player.den_speaker", "state": "playing", "attributes": {"friendly_name": "Den speaker"}},
]


def _states(monkeypatch, states):
    async def get_states(ha_url, ha_token):
        return [dict(s) for s in states]

    monkeypatch.setattr(ha_client, "get_states", get_states)


@pytest.mark.asyncio
async def test_device_name_resolves_to_the_paused_player_of_that_name(_patch, monkeypatch):
    _states(monkeypatch, OFFICE_STATES)
    ctx = SimpleNamespace(ha_url=HUB, ha_token="tok")
    assert await media.resolve_room_player(ctx, "Office TV", None) == "media_player.office_tv_3"
    assert await media.resolve_room_player(ctx, "the office tv", None) == "media_player.office_tv_3"


@pytest.mark.asyncio
async def test_device_name_with_nothing_active_is_deterministic(_patch, monkeypatch):
    _states(monkeypatch, [dict(s, state="idle") for s in OFFICE_STATES])
    ctx = SimpleNamespace(ha_url=HUB, ha_token="tok")
    first = await media.resolve_room_player(ctx, "Office TV", None)
    assert first == "media_player.office_tv"
    _states(monkeypatch, [dict(s, state="idle") for s in reversed(OFFICE_STATES)])
    assert await media.resolve_room_player(ctx, "Office TV", None) == first


@pytest.mark.asyncio
async def test_device_name_prefers_players_voice_may_control(_patch, monkeypatch):
    from services.execution import hardware_router, voice_exposure

    _states(monkeypatch, OFFICE_STATES)
    monkeypatch.setattr(hardware_router, "hub_pair", lambda ctx: (HUB, "tok"))
    exposed = {"media_player.office_tv_cast"}

    async def fetch_exposed(ha_url, ha_token, entity_id):
        return entity_id in exposed

    monkeypatch.setattr(voice_exposure, "fetch_exposed", fetch_exposed)
    ctx = SimpleNamespace(ha_url=HUB, ha_token="tok", via="assistant")
    # The paused player may not be controlled by voice: the exposed one wins.
    assert await media.resolve_room_player(ctx, "Office TV", None) == "media_player.office_tv_cast"


@pytest.mark.asyncio
async def test_device_name_matching_no_player_invents_nothing(_patch, monkeypatch):
    _states(monkeypatch, OFFICE_STATES)
    ctx = SimpleNamespace(ha_url=HUB, ha_token="tok")
    assert await media.resolve_room_player(ctx, "Office speaker", None) is None
    assert await media.resolve_room_player(ctx, "backlight", None) is None  # a light is not a player


@pytest.mark.asyncio
async def test_transport_resume_on_a_named_device(_patch, monkeypatch):
    _states(monkeypatch, OFFICE_STATES)
    seen = []

    async def via_ha(req):
        seen.append((req.entity_id, req.command))
        return media.ExecutionResult(status="SUCCESS", message="ok", service="media_transport")

    monkeypatch.setattr(media, "_transport_via_ha", via_ha)
    req = MediaTransportRequest(
        user_context=UserContext(user="tester", ha_url=HUB, ha_token="tok"),
        entity_id=None, device_name="Office TV", command="resume",
    )
    result = await media.handle_media_transport(req)
    assert result.status == "SUCCESS"
    assert seen == [("media_player.office_tv_3", "resume")]


@pytest.mark.asyncio
async def test_abs_resolves_a_named_device_too(_patch, monkeypatch):
    _states(monkeypatch, OFFICE_STATES)
    ctx = SimpleNamespace(ha_url=HUB, ha_token="tok")
    # audiobookshelf resolves through the same resolver.
    assert await media.resolve_room_player(ctx, "Office TV", "music") == "media_player.office_tv_3"

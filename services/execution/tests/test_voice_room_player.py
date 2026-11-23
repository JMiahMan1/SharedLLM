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

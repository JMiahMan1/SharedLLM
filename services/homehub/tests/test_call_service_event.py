"""The call_service event names the entities a call resolved to, so listeners need not resolve targets."""
import pytest

from services.homehub.core import HubError


@pytest.mark.asyncio
async def test_a_call_aimed_at_an_area_announces_its_entities(hub_factory):
    hub, _ = hub_factory(entities=[{"entity_id": "light.desk", "unique_id": "d"},
                                   {"entity_id": "light.shelf", "unique_id": "s"}])
    seen = []
    hub.bus.subscribe(lambda _t, data: seen.append(data), "call_service")
    with pytest.raises(HubError):  # no light driver in the test hub; the event is fired first
        await hub.call_service("light", "turn_off", {"area_id": "office"})
    assert seen and sorted(seen[0]["entity_ids"]) == ["light.desk", "light.shelf"]
    assert seen[0]["service_data"] == {"area_id": "office"}

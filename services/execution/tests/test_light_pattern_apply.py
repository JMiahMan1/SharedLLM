"""Applying a light pattern: each step colours the cluster members at its
positions. (There was no apply at all; Admin's Execute button had nowhere to go.)"""
import pytest

from services.execution.handlers import groups
from services.execution.schemas import ExecutionResult, UserContext
from services.execution.schemas_groups import LightPatternRequest

CTX = UserContext(user="jeremiah", is_admin=True, ha_url="http://ha", ha_token="t")
PATTERNS = [{"pattern_id": "xmas", "pattern_name": "Christmas", "cluster_id": None, "transition_ms": 0,
             "steps": [{"positions": [0, 2], "rgb": [255, 0, 0], "brightness_pct": 90},
                       {"positions": [1, 3], "rgb": [0, 170, 0], "brightness_pct": 70}]}]
CLUSTERS = [{"cluster_id": "porch", "cluster_name": "Porch",
             "member_entity_ids": ["light.a", "light.b", "light.c"]}]


@pytest.fixture
def lights(monkeypatch):
    calls = []

    async def identity(method, path, json_data=None):
        return PATTERNS if path.endswith("/patterns") else CLUSTERS

    async def command(ctx, domain, service, entity_id, data=None, service_name="ha_service"):
        calls.append((entity_id, service, data))
        return ExecutionResult(status="SUCCESS", message="ok", service=service_name)

    from services.execution import ha_client, hardware_router
    monkeypatch.setattr(groups, "_call_identity", identity)
    monkeypatch.setattr(hardware_router, "execute_device_command", command)
    monkeypatch.setattr(ha_client, "authorize_action", lambda ctx, domain, action: True)
    return calls


def _apply(pattern="Christmas", cluster="porch"):
    return groups.handle_light_pattern(
        LightPatternRequest(action="apply", pattern_id=pattern, cluster_id=cluster), CTX)


@pytest.mark.asyncio
async def test_each_step_colours_its_positions(lights):
    res = await _apply()
    assert res.status == "SUCCESS", res.message
    by_light = {e: d for e, s, d in lights}
    assert by_light["light.a"]["rgb_color"] == [255, 0, 0] and by_light["light.a"]["brightness_pct"] == 90
    assert by_light["light.b"]["rgb_color"] == [0, 170, 0]
    assert by_light["light.c"]["rgb_color"] == [255, 0, 0]
    assert len(lights) == 3  # position 3 is past a 3-light cluster: skipped, not wrapped
    assert all(s == "turn_on" for _, s, _ in lights)


@pytest.mark.asyncio
async def test_a_pattern_and_cluster_are_found_by_id_or_name(lights):
    assert (await _apply(pattern="xmas", cluster="Porch")).status == "SUCCESS"


@pytest.mark.asyncio
async def test_missing_pieces_are_said_plainly(lights):
    assert "No light pattern" in (await _apply(pattern="nope")).message
    assert "Choose a light cluster" in (await _apply(cluster=None)).message
    assert "No light cluster" in (await _apply(cluster="attic")).message
    assert lights == []

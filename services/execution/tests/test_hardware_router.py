# services/execution/tests/test_hardware_router.py
"""Unit tests for unified HA-first routing with direct ESPHome fallback.

All network calls are mocked: HA service results are simulated, and the
ESPHome client functions are patched at module level.
"""

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from services.execution import esphome_client, hardware_router

CTX = SimpleNamespace(
    user="jeremiah", is_admin=True, ha_url="http://ha.local:8123", ha_token="tok"
)

UNREACHABLE = {"ok": False, "error": "Home Assistant is unreachable: Connection refused"}
HA_HTTP_404 = {"ok": False, "error": "HA returned 404: entity not found", "status_code": 404}


@pytest.fixture()
def one_mapped_device(monkeypatch):
    """One configured ESPHome device explicitly mapped to an HA entity."""
    monkeypatch.setattr(
        esphome_client,
        "_device_cache",
        {
            "office-light": {
                "host": "192.168.2.87",
                "port": 6053,
                "noise_psk": "psk",
                "ha_entity_id": "light.office_light",
            }
        },
    )
    monkeypatch.setattr(esphome_client, "_cache_loaded_at", time.monotonic())
    monkeypatch.setattr(esphome_client, "_entity_list_cache", {})
    return esphome_client


# ── translation table ────────────────────────────────────────────────────────────


def test_translate_light_turn_on_with_brightness():
    params = hardware_router._translate_ha_call(
        "light", "turn_on", {"brightness_pct": 55}
    )
    assert params == {"state": True, "brightness_pct": 55}


def test_translate_light_toggle_unsupported():
    # No state readable while HA is down -> must not misroute a toggle.
    assert hardware_router._translate_ha_call("light", "toggle", {}) is None


def test_translate_cover_and_button_and_climate():
    assert hardware_router._translate_ha_call("cover", "open_cover", None) == {"position": 100}
    assert hardware_router._translate_ha_call("cover", "set_cover_position", {"position": 40}) == {"position": 40}
    assert hardware_router._translate_ha_call("button", "press", None) == {}
    params = hardware_router._translate_ha_call(
        "climate", "set_temperature", {"temperature": 21.5}
    )
    assert params == {"target_temperature": 21.5}


def test_translate_scene_unsupported():
    assert hardware_router._translate_ha_call("scene", "turn_on", {}) is None


# ── HA-entity correlation ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_find_device_explicit_mapping_wins(one_mapped_device):
    name = await esphome_client.find_device_for_ha_entity("LIGHT.OFFICE_LIGHT")
    assert name == "office-light"


@pytest.mark.asyncio
async def test_find_device_object_id_correlation(one_mapped_device):
    # Same device but WITHOUT explicit mapping: correlate via entity list.
    one_mapped_device._device_cache["office-light"]["ha_entity_id"] = None
    entities = [
        {"domain": "light", "name": "Office Light", "object_id": "office_light", "key": 1}
    ]
    with patch.object(
        esphome_client, "get_device_entities_cached", AsyncMock(return_value=entities)
    ):
        name = await esphome_client.find_device_for_ha_entity("light.office_light")
    assert name == "office-light"


@pytest.mark.asyncio
async def test_find_device_no_match_returns_none(one_mapped_device):
    with patch.object(
        esphome_client, "get_device_entities_cached", AsyncMock(return_value=[])
    ):
        assert await esphome_client.find_device_for_ha_entity("switch.unrelated") is None
    assert await esphome_client.find_device_for_ha_entity("not-an-entity") is None


@pytest.mark.asyncio
async def test_find_device_skips_unreachable_devices(one_mapped_device):
    """An unreachable device must be skipped, not break correlation."""
    one_mapped_device._device_cache["office-light"]["ha_entity_id"] = None
    with patch.object(
        esphome_client,
        "get_device_entities_cached",
        AsyncMock(side_effect=ConnectionError("down")),
    ):
        assert await esphome_client.find_device_for_ha_entity("light.office_light") is None


# ── routing behavior ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ha_success_short_circuits(one_mapped_device):
    with patch.object(
        hardware_router, "_call_ha", AsyncMock(return_value={"ok": True})
    ) as ha:
        result = await hardware_router.execute_device_command(
            CTX, "light", "turn_on", "light.other"
        )
    assert result.status == "SUCCESS"
    assert result.detail["route"] == "ha"
    ha.assert_awaited_once()


@pytest.mark.asyncio
async def test_ha_http_error_does_not_fall_back(one_matched_device=one_mapped_device):
    """4xx from HA is a real answer — never re-executed via ESPHome."""
    with (
        patch.object(hardware_router, "_call_ha", AsyncMock(return_value=dict(HA_HTTP_404))),
        patch.object(
            esphome_client, "find_device_for_ha_entity", AsyncMock()
        ) as finder,
    ):
        result = await hardware_router.execute_device_command(
            CTX, "light", "turn_on", "light.office_light"
        )
    assert result.status == "FAILURE"
    assert result.detail["route"] == "ha"
    finder.assert_not_awaited()


@pytest.mark.asyncio
async def test_falls_back_to_direct_esphome_when_ha_down(one_mapped_device):
    async def fake_call_entity(device_name, entity_name, params):
        assert device_name == "office-light"
        assert entity_name == "office_light"
        assert params == {"state": True}
        return {"domain": "light", "name": "Office Light", "params": params}

    with (
        patch.object(hardware_router, "_call_ha", AsyncMock(return_value=dict(UNREACHABLE))),
        patch.object(esphome_client, "call_entity", AsyncMock(side_effect=fake_call_entity)),
    ):
        result = await hardware_router.execute_device_command(
            CTX, "light", "turn_on", "light.office_light"
        )
    assert result.status == "SUCCESS"
    assert result.detail["route"] == "esphome_direct"
    assert result.detail["device"] == "office-light"


@pytest.mark.asyncio
async def test_fallback_unknown_device_clear_failure(one_mapped_device):
    with (
        patch.object(hardware_router, "_call_ha", AsyncMock(return_value=dict(UNREACHABLE))),
        patch.object(
            esphome_client, "find_device_for_ha_entity", AsyncMock(return_value=None)
        ),
    ):
        result = await hardware_router.execute_device_command(
            CTX, "light", "turn_on", "light.mystery"
        )
    assert result.status == "FAILURE"
    assert "Admin > Hardware" in result.message


@pytest.mark.asyncio
async def test_fallback_untranslatable_task_fails_cleanly(one_mapped_device):
    with (
        patch.object(hardware_router, "_call_ha", AsyncMock(return_value=dict(UNREACHABLE))),
        patch.object(
            esphome_client,
            "find_device_for_ha_entity",
            AsyncMock(return_value="office-light"),
        ),
    ):
        result = await hardware_router.execute_device_command(
            CTX, "scene", "turn_on", "scene.movie_night"
        )
    assert result.status == "FAILURE"
    assert "cannot be translated" in result.message


# ── endpoint wiring ──────────────────────────────────────────────────────────────


def test_ha_service_route_uses_router(one_mapped_device):
    """POST /execute/ha_service goes through execute_device_command."""
    from fastapi.testclient import TestClient

    from services.config import INTERNAL_SECRET
    from services.execution.main import app

    with patch.object(
        hardware_router,
        "execute_device_command",
        AsyncMock(
            return_value=hardware_router.ExecutionResult(
                status="SUCCESS", message="routed", service="ha_service",
                detail={"route": "ha"},
            )
        ),
    ) as router_mock:
        client = TestClient(app)
        resp = client.post(
            "/execute/ha_service",
            json={
                "user_context": {"user": "jeremiah"},
                "domain": "switch",
                "service": "turn_on",
                "entity_id": "switch.plug",
            },
            headers={"X-Internal-Secret": INTERNAL_SECRET},
        )
    assert resp.status_code == 200
    assert resp.json()["detail"]["route"] == "ha"
    args = router_mock.await_args
    assert args.args[1:4] == ("switch", "turn_on", "switch.plug")

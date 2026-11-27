"""Wiring tests for cross-room Echo satellite timers in execution/handlers/timer.py."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from services.execution import capability_index, ha_client, hardware_router
from services.execution.handlers import timer as timer_handler
from services.execution.schemas import TimerRequest, UserContext


class _FakeIdx:
    def __init__(self, areas: dict[str, list[str]]) -> None:
        self._areas = areas

    def area_id_for(self, name: str) -> str | None:
        slug = name.strip().lower().replace(" ", "_")
        return slug if slug in self._areas else None

    def require_area(self, name: str) -> str:
        area_id = self.area_id_for(name)
        if area_id is None:
            raise capability_index.UnknownAreaError(name)
        return area_id

    def in_area(self, area_id: str, *, domain: str | None = None) -> list[str]:
        items = self._areas.get(area_id, [])
        if domain is None:
            return list(items)
        return [eid for eid in items if eid.startswith(f"{domain}.")]


def _req(target_device: str | None = None, voice_satellite: str | None = None) -> TimerRequest:
    return TimerRequest(
        action="add",
        type="timer",
        title="Pasta",
        duration_str="10m",
        target_device=target_device,
        user_context=UserContext(
            user="jeremiah",
            ha_url="http://homehub:8123",
            ha_token="tok",
            voice_satellite=voice_satellite,
        ),
    )


@pytest.mark.asyncio
async def test_room_targeted_timer_resolves_target_room_satellite(monkeypatch) -> None:
    fake_idx = _FakeIdx({
        "kitchen": ["assist_satellite.kitchen_echo"],
        "office": ["assist_satellite.office_echo"],
        "garage": [],
    })
    monkeypatch.setattr(hardware_router, "hub_pair", lambda _u: ("http://homehub:8123", "tok"))
    monkeypatch.setattr(capability_index, "routing_index", AsyncMock(return_value=fake_idx))

    sat = await timer_handler._resolve_timer_satellite(
        _req(target_device="kitchen", voice_satellite="assist_satellite.office_echo"),
        "kitchen",
    )
    assert sat == "assist_satellite.kitchen_echo"


@pytest.mark.asyncio
async def test_named_room_without_satellite_does_not_fall_back_to_caller_room(monkeypatch) -> None:
    fake_idx = _FakeIdx({
        "office": ["assist_satellite.office_echo"],
        "garage": [],
    })
    monkeypatch.setattr(hardware_router, "hub_pair", lambda _u: ("http://homehub:8123", "tok"))
    monkeypatch.setattr(capability_index, "routing_index", AsyncMock(return_value=fake_idx))

    sat = await timer_handler._resolve_timer_satellite(
        _req(target_device="garage", voice_satellite="assist_satellite.office_echo"),
        "garage",
    )
    assert sat is None


@pytest.mark.asyncio
async def test_no_room_and_offline_hub_fall_back_to_caller_satellite(monkeypatch) -> None:
    # No room specified -> caller's satellite directly.
    sat_same = await timer_handler._resolve_timer_satellite(
        _req(target_device=None, voice_satellite="assist_satellite.office_echo"),
        None,
    )
    assert sat_same == "assist_satellite.office_echo"

    # Offline/unreachable hub -> never fails, falls back safely.
    monkeypatch.setattr(hardware_router, "hub_pair", lambda _u: ("http://homehub:8123", "tok"))
    monkeypatch.setattr(capability_index, "routing_index", AsyncMock(side_effect=RuntimeError("offline")))
    monkeypatch.setattr(ha_client, "get_states", AsyncMock(side_effect=RuntimeError("offline")))

    sat_offline = await timer_handler._resolve_timer_satellite(
        _req(target_device="kitchen", voice_satellite="assist_satellite.office_echo"),
        "kitchen",
    )
    assert sat_offline == "assist_satellite.office_echo"


@pytest.mark.asyncio
async def test_plain_ha_states_fallback_resolves_room_satellite(monkeypatch) -> None:
    monkeypatch.setattr(hardware_router, "hub_pair", lambda _u: ("http://ha:8123", "tok"))
    monkeypatch.setattr(capability_index, "routing_index", AsyncMock(return_value=None))
    monkeypatch.setattr(
        ha_client,
        "get_states",
        AsyncMock(return_value=[
            {"entity_id": "assist_satellite.kitchen_echo", "attributes": {"friendly_name": "Kitchen Echo"}},
            {"entity_id": "assist_satellite.office_echo", "attributes": {"friendly_name": "Office Echo"}},
        ]),
    )

    sat = await timer_handler._resolve_timer_satellite(
        _req(target_device="the kitchen", voice_satellite="assist_satellite.office_echo"),
        "the kitchen",
    )
    assert sat == "assist_satellite.kitchen_echo"

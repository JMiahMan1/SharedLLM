"""Startup and event-bus wiring tests for Echo ring + SoundGuard in homehub."""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from services.homehub import echo_ring, sound_guard
from services.homehub.core import Area, DeviceEntry, EntityEntry, Hub


@pytest.mark.asyncio
async def test_echo_ring_services_and_sound_guard_corroborated_alarm(monkeypatch) -> None:
    hub = Hub(time_zone="UTC")

    echo_ring.register_services(hub)
    for action in ("notify", "progress", "music_mode", "flash_all", "clear"):
        assert ("echo_ring", action) in hub.services

    hub.areas["kitchen"] = Area(area_id="kitchen", name="Kitchen")
    hub.areas["hallway"] = Area(area_id="hallway", name="Hallway")
    hub.devices["dev_kitchen"] = DeviceEntry(id="dev_kitchen", name="Kitchen Echo", area_id="kitchen")
    hub.devices["dev_hall"] = DeviceEntry(id="dev_hall", name="Hall Echo", area_id="hallway")
    hub.entities["event.kitchen_echo_sound"] = EntityEntry(
        entity_id="event.kitchen_echo_sound",
        platform="esphome",
        device_id="dev_kitchen",
        unique_id="kitchen_sound",
        original_name="Kitchen Sound",
    )
    hub.entities["event.hall_echo_sound"] = EntityEntry(
        entity_id="event.hall_echo_sound",
        platform="esphome",
        device_id="dev_hall",
        unique_id="hall_sound",
        original_name="Hall Sound",
    )
    # One person is home so corroboration across 2 Echoes is required.
    hub.states.set("person.jeremiah", "home")

    notifs: list[dict] = []

    async def _create_notif(_hub, call) -> None:
        notifs.append(dict(call.data or {}))

    hub.register_service("persistent_notification", "create", _create_notif, {})
    flash_mock = AsyncMock(return_value={"targeted": [], "failed": []})
    monkeypatch.setattr(echo_ring, "flash_all", flash_mock)

    alerts: list[dict] = []
    hub.bus.subscribe(lambda _et, data: alerts.append(dict(data)), "echo_sound_alert")

    detach = sound_guard.attach_to_hub(hub)
    try:
        # First Echo hears smoke/CO while someone is home -> not yet corroborated.
        hub.states.set("event.kitchen_echo_sound", "2026-11-23T04:00:00Z", {"event_type": "smoke_or_co_alarm"})
        await asyncio.sleep(0)
        assert alerts == []
        assert flash_mock.await_count == 0

        # Second Echo corroborates within window -> fires echo_sound_alert, flashes rings, creates notification.
        hub.states.set("event.hall_echo_sound", "2026-11-23T04:00:02Z", {"event_type": "smoke_or_co_alarm"})
        if detach.tasks:
            await asyncio.gather(*list(detach.tasks))

        assert len(alerts) == 1
        assert alerts[0]["event_type"] == "smoke_or_co_alarm"
        assert alerts[0]["corroborated"] is True
        assert alerts[0]["flash_rings"] is True
        assert set(alerts[0]["rooms"]) == {"Kitchen", "Hallway"}
        flash_mock.assert_awaited_once_with(hub, color="red", repeat=8)
        assert len(notifs) == 1
        assert notifs[0]["notification_id"] == "sound_guard_smoke_or_co_alarm"
    finally:
        detach()


@pytest.mark.asyncio
async def test_sound_guard_nobody_home_escalates_single_device_and_detaches(monkeypatch) -> None:
    hub = Hub(time_zone="UTC")

    hub.areas["nursery"] = Area(area_id="nursery", name="Nursery")
    hub.devices["dev_nursery"] = DeviceEntry(id="dev_nursery", name="Nursery Echo", area_id="nursery")
    hub.entities["event.nursery_echo_sound"] = EntityEntry(
        entity_id="event.nursery_echo_sound",
        platform="esphome",
        device_id="dev_nursery",
        unique_id="nursery_sound",
        original_name="Nursery Sound",
    )
    hub.states.set("person.jeremiah", "not_home")

    flash_mock = AsyncMock(return_value={"targeted": [], "failed": []})
    monkeypatch.setattr(echo_ring, "flash_all", flash_mock)

    alerts: list[dict] = []
    hub.bus.subscribe(lambda _et, data: alerts.append(dict(data)), "echo_sound_alert")
    detach = sound_guard.attach_to_hub(hub)

    hub.states.set("event.nursery_echo_sound", "2026-11-23T04:01:00Z", {"event_type": "glass_break"})
    if detach.tasks:
        await asyncio.gather(*list(detach.tasks))

    assert len(alerts) == 1
    assert alerts[0]["event_type"] == "glass_break"
    assert alerts[0]["flash_rings"] is True
    flash_mock.assert_awaited_once()

    # Detaching stops future SoundGuard alerts.
    detach()
    hub.states.set("event.nursery_echo_sound", "2026-11-23T04:05:00Z", {"event_type": "baby_cry"})
    await asyncio.sleep(0)
    assert len(alerts) == 1


def test_homehub_main_wires_echo_ring_and_sound_guard_on_start_and_stop() -> None:
    main_src = Path(__file__).resolve().parents[1].joinpath("main.py").read_text(encoding="utf-8")
    assert "echo_ring.register_services(self.hub)" in main_src
    assert "sound_guard.attach_to_hub(self.hub)" in main_src
    assert "self._unsub_sound_guard()" in main_src

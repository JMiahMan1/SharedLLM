"""The built-in time and date sensors, and the Home Assistant entries the hub replaces."""
from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime

import pytest

from services.homehub import time_date
from services.homehub.core import ConfigEntry, EntityEntry, Hub
from services.homehub.drivers import DRIVERS, REPLACED, replaced_reason
from services.homehub.runtime import Runtime

NOW = datetime(2026, 3, 4, 23, 30, 41, tzinfo=UTC)  # 16:30 the same day at UTC-7


def test_every_option_has_ha_text_in_the_household_zone():
    s = time_date.states_at(NOW, "Etc/GMT+7")
    assert s == {
        "sensor.time": "16:30", "sensor.date": "2026-03-04", "sensor.date_time": "2026-03-04, 16:30",
        "sensor.date_time_utc": "2026-03-04, 23:30", "sensor.date_time_iso": "2026-03-04T16:30:00",
        "sensor.time_date": "16:30, 2026-03-04", "sensor.time_utc": "23:30",
    }


def test_the_date_rolls_over_at_local_midnight_not_utc():
    s = time_date.states_at(datetime(2026, 3, 5, 6, 59, tzinfo=UTC), "Etc/GMT+7")
    assert s["sensor.date"] == "2026-03-04" and s["sensor.time"] == "23:59"
    assert s["sensor.date_time_utc"] == "2026-03-05, 06:59"


def test_fresh_hub_gets_names_and_imported_entities_keep_theirs():
    hub = Hub(time_zone="Etc/GMT+7")
    hub.entities["sensor.date"] = EntityEntry(entity_id="sensor.date", platform="time_date", name="Today")
    time_date.publish(hub, NOW)
    assert hub.states.get("sensor.time").attributes["friendly_name"] == "Time"
    assert hub.states.get("sensor.time").attributes["icon"] == "mdi:clock"
    assert hub.states.get("sensor.date").state == "2026-03-04"
    assert hub.states.get("sensor.date").attributes["friendly_name"] == "Today"


def test_seconds_to_next_minute():
    assert time_date.seconds_to_next_minute(NOW) == pytest.approx(19.0)
    assert time_date.seconds_to_next_minute(NOW.replace(second=59, microsecond=999_999)) == pytest.approx(0.05)


async def test_run_publishes_at_once_and_on_every_minute():
    hub = Hub(time_zone="UTC")
    clock = [NOW]
    sleeps: list[float] = []

    async def sleep(s: float) -> None:
        sleeps.append(s)
        if len(sleeps) == 3:
            raise asyncio.CancelledError
        clock[0] = clock[0].replace(second=0) + (NOW.replace(minute=31, second=0) - NOW.replace(second=0))

    seen: list[str] = []
    hub.bus.subscribe(lambda _t, ev: ev["new_state"].entity_id == "sensor.time" and seen.append(ev["new_state"].state),
                      "state_changed")
    with pytest.raises(asyncio.CancelledError):
        await time_date.run(hub, now=lambda: clock[0], sleep=sleep)
    assert seen[:2] == ["23:30", "23:31"]
    assert sleeps[0] == pytest.approx(19.0)


async def test_a_failing_tick_does_not_stop_the_clock(monkeypatch):
    hub = Hub(time_zone="UTC")
    calls = {"n": 0}

    def boom(_hub, _now):
        calls["n"] += 1
        raise RuntimeError("bad tick")

    async def sleep(_s: float) -> None:
        if calls["n"] >= 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(time_date, "publish", boom)
    with pytest.raises(asyncio.CancelledError):
        await time_date.run(hub, now=lambda: NOW, sleep=sleep)
    assert calls["n"] == 2


def test_automations_know_the_sensors_before_the_first_tick():
    from services.homehub.automation import ALWAYS_KNOWN

    assert {"sensor.time", "sensor.date", "sensor.date_time"} <= ALWAYS_KNOWN


NEW_REPLACED = ("heos", "led_ble", "tuya", "wemo", "radio_browser", "scheduler", "marketplace", "mqtt", "time_date")


@pytest.mark.parametrize("domain", NEW_REPLACED)
def test_new_replaced_domains_have_a_reason(domain):
    reason = replaced_reason(domain, {})
    assert reason and reason.startswith("Replaced by the hub: ")
    assert chr(0x2014) not in reason and chr(0x2013) not in reason


def test_no_domain_is_both_driven_and_replaced():
    assert not set(DRIVERS) & set(REPLACED)


# Every integration a typical Home Assistant install like the household's carried (domains only, no data).
HA_DOMAINS = (
    "analytics", "androidtv_remote", "backup", "bluetooth", "cast", "dlna_dmr", "emporia_vue", "esphome", "go2rtc",
    "google_translate", "hassio", "heos", "homekit_controller", "ibeacon", "led_ble", "local_todo", "marketplace",
    "matter", "mjpeg", "mobile_app", "mqtt", "music_assistant", "nws", "ollama", "open_meteo", "openai_conversation",
    "radio_browser", "reolink", "roborock", "roku", "samsungtv", "scheduler", "shopping_list", "srp_energy", "sun",
    "switch_as_x", "tasmota", "thermopro", "time_date", "tuya", "webostv", "wemo", "wiz", "wyoming",
)


def test_every_imported_domain_has_a_driver_or_a_reason():
    missing = [d for d in HA_DOMAINS if d not in DRIVERS and not replaced_reason(d, {"x": 1})]
    assert missing == []


def test_live_inventory_when_given(request):
    """``HOMEHUB_HA_INVENTORY=<inventory.json>`` checks a real import's domain list too (skipped without it)."""
    path = os.getenv("HOMEHUB_HA_INVENTORY")
    if not path:
        pytest.skip("HOMEHUB_HA_INVENTORY not set")
    with open(path) as f:
        domains = json.load(f)["config_entry_domains"]
    assert [d for d in domains if d not in DRIVERS and not replaced_reason(d, {"x": 1})] == []


async def test_a_time_date_entry_loads_as_replaced():
    hub = Hub(time_zone="UTC")
    entry = ConfigEntry(entry_id="td1", domain="time_date", title="Time", data={"display_options": "time"})
    hub.config_entries[entry.entry_id] = entry
    rt = Runtime(hub, store=None)
    rt.start_entry(entry)
    assert await rt.wait_first_setups(1.0) == []
    assert entry.state == "not_loaded" and "sensor.time" in (entry.reason or "")
    await rt.shutdown()

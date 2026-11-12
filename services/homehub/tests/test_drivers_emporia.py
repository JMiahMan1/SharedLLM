"""Emporia Vue driver against a fake pyemvue client (no network)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from services.homehub.drivers import emporia as em
from services.homehub.drivers.emporia import EmporiaDriver, month_reset, parse_unique_id
from services.homehub.tests.conftest import make_hub

GID = 100001


def chan(num, usage, gid=GID, nested=None):
    return SimpleNamespace(device_gid=gid, channel_num=num, usage=usage, nested_devices=nested or {})


class FakeVue:
    def __init__(self, ok=True):
        self.ok = ok
        self.usage = {
            "1MIN": {"1,2,3": 2.39041495869954 / 60, "1": -0.05, "2": 0.0, "Balance": 739.533572896321 / 60000},
            "1D": {"1,2,3": 37.9, "1": 10.0, "2": 4.0, "Balance": 3.0},
            "1MON": {"1,2,3": 2498.2, "1": 900.0, "2": 300.0, "Balance": 100.0},
        }
        self.calls = []

    def login(self, email, password):
        if isinstance(self.ok, Exception):
            raise self.ok
        return self.ok

    def get_devices(self):
        channels = [SimpleNamespace(channel_num=n, type="Main" if n == "1,2,3" else "FiftyAmp")
                    for n in ("1,2,3", "1", "2", "15")]
        return [SimpleNamespace(device_gid=GID, channels=channels, time_zone="Etc/GMT+7",
                                billing_cycle_start_day=19)]

    def get_device_list_usage(self, gids, instant, scale, unit, retries):
        self.calls.append(scale)
        if self.usage.get(scale) is None:
            raise OSError("cloud down")
        chans = {n: chan(n, u) for n, u in self.usage[scale].items()}
        return {GID: SimpleNamespace(channels=chans)}


def uid(scale, ch):
    return f"sensor.emporia_vue.{scale}.{GID}-{ch}"


#: A person chose kW for some power sensors: HA keeps that choice in options.sensor (as the live registry does).
KW = {"sensor": {"unit_of_measurement": "kW"}}

ENTITIES = [
    {"entity_id": "sensor.main_panel_123_1min", "unique_id": uid("instant", "1,2,3"), "unit_of_measurement": "kW",
     "original_device_class": "power", "options": KW},
    {"entity_id": "sensor.ac_unit_1_1min", "unique_id": uid("instant", "1"), "unit_of_measurement": "kW",
     "options": KW},
    {"entity_id": "sensor.ac_unit_2_1min", "unique_id": uid("instant", "2"), "unit_of_measurement": "kW",
     "options": KW},
    {"entity_id": "sensor.main_panel_balance_1min", "unique_id": uid("instant", "Balance"),
     "unit_of_measurement": "W"},
    {"entity_id": "sensor.main_panel_123_1d", "unique_id": uid("1D", "1,2,3"), "unit_of_measurement": "kWh",
     "original_device_class": "energy"},
    {"entity_id": "sensor.ac_unit_1_1d", "unique_id": uid("1D", "1"), "unit_of_measurement": "kWh"},
    {"entity_id": "sensor.main_panel_123_1mon", "unique_id": uid("1MON", "1,2,3"), "unit_of_measurement": "kWh"},
    {"entity_id": "sensor.main_panel_15_1min", "unique_id": uid("instant", "15"), "unit_of_measurement": "W"},
]
DATA = {"email": "someone@example.com", "password": "x", "enable_1m": True, "enable_1d": True,
        "enable_1mon": True}


async def setup(vue=None, data=DATA):
    hub, entry = make_hub("emporia_vue", data, ENTITIES)
    driver = EmporiaDriver(hub, entry)
    vue = vue or FakeVue()
    driver._new_client = lambda: vue
    await driver.async_setup()
    return hub, driver, vue


def val(hub, eid):
    return hub.states.get(eid).state


def test_unique_id_parsing():
    assert parse_unique_id("sensor.emporia_vue.instant.100001-1,2,3") == ("1MIN", GID, "1,2,3")
    assert parse_unique_id("sensor.emporia_vue.1MON.100001-Balance") == ("1MON", GID, "Balance")
    assert parse_unique_id("sensor.emporia_vue.1D.100001-10") == ("1D", GID, "10")
    assert parse_unique_id("sensor.something_else") is None


def test_month_reset_uses_the_billing_day():
    tz = em.ZoneInfo("Etc/GMT+7")
    assert month_reset(datetime(2026, 11, 11, 12, tzinfo=tz), 19) == datetime(2026, 10, 19, tzinfo=tz)
    assert month_reset(datetime(2026, 11, 20, 12, tzinfo=tz), 19) == datetime(2026, 11, 19, tzinfo=tz)
    assert month_reset(datetime(2026, 1, 5, tzinfo=tz), 19) == datetime(2025, 12, 19, tzinfo=tz)
    assert month_reset(datetime(2026, 3, 1, tzinfo=tz), 31) == datetime(2026, 2, 28, tzinfo=tz)


async def test_power_in_the_unit_ha_reported():
    hub, _driver, _vue = await setup()
    # kWh in the minute * 60 * 1000 = W; the kW-overridden sensors report kW, as in HA.
    assert float(val(hub, "sensor.main_panel_123_1min")) == pytest.approx(2.39041495869954)
    assert hub.states.get("sensor.main_panel_123_1min").attributes["unit_of_measurement"] == "kW"
    assert hub.states.get("sensor.main_panel_123_1min").attributes["state_class"] == "measurement"
    assert hub.states.get("sensor.main_panel_123_1min").attributes["device_class"] == "power"
    assert float(val(hub, "sensor.main_panel_balance_1min")) == pytest.approx(739.533572896321)
    assert hub.states.get("sensor.main_panel_balance_1min").attributes["unit_of_measurement"] == "W"
    # a circuit is made positive, as the integration does
    assert float(val(hub, "sensor.ac_unit_1_1min")) == pytest.approx(3.0)
    assert float(val(hub, "sensor.ac_unit_2_1min")) == 0.0
    # an unused input the reply leaves out reads 0, not unknown
    assert float(val(hub, "sensor.main_panel_15_1min")) == 0.0


async def test_energy_sensors():
    hub, _driver, _vue = await setup()
    d = hub.states.get("sensor.main_panel_123_1d")
    assert float(d.state) == pytest.approx(37.9)
    assert d.attributes["state_class"] == "total" and d.attributes["unit_of_measurement"] == "kWh"
    assert d.attributes["device_class"] == "energy" and d.attributes["last_reset"]
    assert d.attributes["last_reset"].endswith("T00:00:00-07:00")
    m = hub.states.get("sensor.main_panel_123_1mon")
    assert float(m.state) == pytest.approx(2498.2)
    assert m.attributes["last_reset"].endswith("-19T00:00:00-07:00")


async def test_day_integrates_minutes_between_fetches_and_resets_at_midnight():
    hub, driver, vue = await setup()
    vue.calls.clear()
    await driver.async_poll()
    assert vue.calls == ["1MIN"]  # day and month not re-fetched within their windows
    expected = 37.9 + 2.39041495869954 / 60
    assert float(val(hub, "sensor.main_panel_123_1d")) == pytest.approx(expected)
    # pretend the last reset was yesterday: the next minute starts the day from zero
    driver.day_reset[GID] = driver.day_reset[GID] - timedelta(days=1)
    await driver.async_poll()
    assert float(val(hub, "sensor.main_panel_123_1d")) == pytest.approx(2.39041495869954 / 60)
    # after 15 minutes the API value replaces the running sum
    driver.day_fetched = datetime.now(UTC) - timedelta(minutes=16)
    await driver.async_poll()
    assert float(val(hub, "sensor.main_panel_123_1d")) == pytest.approx(37.9)


async def test_missing_minute_repeats_last_value():
    hub, driver, vue = await setup()
    vue.usage["1MIN"]["1,2,3"] = None
    await driver.async_poll()
    assert float(val(hub, "sensor.main_panel_123_1min")) == pytest.approx(2.39041495869954)


async def test_cloud_failure_marks_unavailable_without_raising():
    hub, driver, vue = await setup()
    vue.usage["1MIN"] = None
    await driver.async_poll()
    assert val(hub, "sensor.main_panel_123_1min") == "unavailable"
    assert float(val(hub, "sensor.main_panel_123_1d")) == pytest.approx(37.9)  # day still known
    vue.usage["1MIN"] = {"1,2,3": 0.05}
    await driver.async_poll()
    assert float(val(hub, "sensor.main_panel_123_1min")) == pytest.approx(3.0)


@pytest.mark.parametrize("result", [False, RuntimeError("NotAuthorized")])
async def test_login_failure_raises_for_retry(result, caplog):
    with pytest.raises(em.HubError):
        await setup(FakeVue(ok=result))
    assert "Emporia cloud login" in caplog.text


async def test_disabled_scales_are_not_fetched():
    data = dict(DATA, enable_1mon=False)
    _hub, _driver, vue = await setup(data=data)
    assert "1MON" not in vue.calls

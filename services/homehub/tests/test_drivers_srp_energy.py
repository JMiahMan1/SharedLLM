"""SRP Energy driver against a fake srpenergy client (no network)."""
from __future__ import annotations

import types

import pytest

from services.homehub.drivers import srp_energy as srp
from services.homehub.drivers.srp_energy import SrpEnergyDriver, daily_usage
from services.homehub.tests.conftest import make_hub

ENTRY_ID = "01J5YSDXYK9R57Q5HTQDS96FQK"
ENTITIES = [{"entity_id": "sensor.srp_energy_home_energy_usage", "unique_id": f"{ENTRY_ID}_total_usage",
             "unit_of_measurement": "kWh", "original_device_class": "energy",
             "capabilities": {"state_class": "total_increasing"}}]
DATA = {"id": "123456789", "is_tou": True, "name": "Home", "username": "u", "password": "p"}

ROWS = [("11/10/2026", "1:00 PM", "2026-11-10T13:00:00", 1.25, 0.2),
        ("11/10/2026", "2:00 PM", "2026-11-10T14:00:00", "2.05", 0.3)]


class FakeClient:
    def __init__(self, rows=ROWS):
        self.rows = rows
        self.calls = []

    def usage(self, start, end, is_tou=False):
        self.calls.append((start, end, is_tou))
        if isinstance(self.rows, Exception):
            raise self.rows
        return self.rows


async def setup(client):
    hub, entry = make_hub("srp_energy", DATA, ENTITIES, entry_id=ENTRY_ID)
    driver = SrpEnergyDriver(hub, entry)
    driver._new_client = lambda: client
    return hub, driver


def test_daily_usage_sums_kwh():
    assert daily_usage(ROWS) == pytest.approx(3.3)
    assert daily_usage([]) == 0


async def test_publishes_last_day_in_kwh():
    client = FakeClient()
    hub, driver = await setup(client)
    await driver.async_setup()
    st = hub.states.get("sensor.srp_energy_home_energy_usage")
    assert st.state == "3.3"
    assert st.attributes["unit_of_measurement"] == "kWh"
    assert st.attributes["state_class"] == "total_increasing"
    start, end, is_tou = client.calls[0]
    assert is_tou is True
    assert (end - start).days == 1 and str(end.tzinfo) == "America/Phoenix"


async def test_login_failure_raises_on_setup_and_marks_unavailable(caplog):
    hub, driver = await setup(FakeClient(RuntimeError("Access denied (403) during 'login/authorize'")))
    with pytest.raises(srp.HubError):
        await driver.async_setup()
    assert hub.states.get("sensor.srp_energy_home_energy_usage").state == "unavailable"
    assert "SRP usage fetch failed" in caplog.text


async def test_poll_failure_does_not_raise_and_recovers():
    client = FakeClient()
    hub, driver = await setup(client)
    await driver.async_setup()
    client.rows = OSError("timeout")
    await driver.async_poll()
    assert hub.states.get("sensor.srp_energy_home_energy_usage").state == "unavailable"
    client.rows = ROWS[:1]
    await driver.async_poll()
    assert hub.states.get("sensor.srp_energy_home_energy_usage").state == "1.25"


async def test_invalid_account_id_is_a_clear_error():
    hub, entry = make_hub("srp_energy", dict(DATA, id="12"), ENTITIES)
    if srp.srp_client is None:
        pytest.skip("srpenergy not installed")
    with pytest.raises(srp.HubError):
        await SrpEnergyDriver(hub, entry).async_setup()


def test_timeout_shim_puts_a_timeout_on_every_request(monkeypatch):
    module = types.SimpleNamespace(requests=None)
    srp._with_timeouts(module)
    session = module.requests.Session()
    seen = {}

    def fake_request(self, method, url, **kwargs):
        seen.update(kwargs)
        return "resp"

    monkeypatch.setattr(srp.requests.Session, "request", fake_request)
    assert session.post("https://example.invalid/x", data={}) == "resp"
    assert seen["timeout"] == srp.REQUEST_TIMEOUT
    assert module.requests.HTTPError is srp.requests.HTTPError
    # the real requests module is untouched
    assert srp.requests.Session is not srp._TimeoutSession


# -- hourly statistics ---------------------------------------------------------------
def _raw(day: str, hours: range, peak: range) -> list[dict]:
    out = []
    for h in hours:
        kwh = 2.0 if h in peak else 1.0
        out.append({"date": f"{day}T{h:02d}:00:00", "totalKwh": 0, "totalCost": 0,
                    "onPeakKwh": kwh if h in peak else 0.0, "offPeakKwh": 0.0 if h in peak else kwh,
                    "shoulderKwh": 0.0, "superOffPeakKwh": 0.0,
                    "onPeakCost": kwh * 0.2 if h in peak else 0.0, "offPeakCost": 0.0 if h in peak else kwh * 0.1,
                    "shoulderCost": 0.0, "superOffPeakCost": 0.0})
    return out


def test_normalize_builds_totals_from_the_period_columns():
    (row,) = srp.normalize_hourly(_raw("2026-07-06", range(15, 16), range(14, 20)))
    assert row["kwh"] == 2.0 and row["cost"] == pytest.approx(0.4)
    assert row["periods"]["on_peak"] == 2.0 and str(row["start"].tzinfo) == "America/Phoenix"


def test_published_holds_back_unpublished_hours():
    from datetime import datetime, timedelta

    rows = srp.normalize_hourly(_raw("2026-07-06", range(0, 24), range(14, 20)))
    for r in rows[20:]:
        r["kwh"] = 0.0  # SRP has not filled these in yet
    now = rows[-1]["start"] + timedelta(hours=1)
    kept = srp.published(rows, now)
    assert len(kept) == 20 and kept[-1]["start"].hour == 19
    assert srp.published(rows, datetime(2026, 7, 6, 5, tzinfo=srp.PHOENIX)) == rows[:3]


async def test_statistics_written_with_the_utilitys_own_periods(tmp_path, monkeypatch):
    from services.homehub.statistics import Statistics

    stats = Statistics(str(tmp_path / "s.db"), None, time_zone="America/Phoenix")
    hub, driver = await setup(FakeClient())
    hub.statistics = stats
    raw = _raw("2026-07-06", range(0, 24), range(14, 20)) + _raw("2026-07-07", range(0, 24), range(14, 20))
    calls = []

    def fake_fetch(client, start, end):
        calls.append((start, end))
        return [r for r in srp.normalize_hourly(raw) if start.date() <= r["start"].date() <= end.date()]

    monkeypatch.setattr(srp, "fetch_hourly", fake_fetch)
    import datetime as dt_mod

    class FrozenDT(dt_mod.datetime):
        @classmethod
        def now(cls, tz=None):
            return dt_mod.datetime(2026, 7, 8, 9, 30, tzinfo=tz)

    monkeypatch.setattr(srp, "datetime", FrozenDT)
    await driver.async_setup()
    acct = DATA["id"]
    meta = {m["statistic_id"] for m in stats.all_meta()}
    assert meta == {f"srp_energy:{acct}_energy_consumption", f"srp_energy:{acct}_energy_cost",
                    f"srp_energy:{acct}_energy_consumption_on_peak", f"srp_energy:{acct}_energy_consumption_off_peak"}
    on = stats.during_period(dt_mod.datetime(2026, 7, 6, tzinfo=srp.PHOENIX), None,
                             [f"srp_energy:{acct}_energy_consumption_on_peak"], "day", ["change"])
    assert [d["change"] for d in on[f"srp_energy:{acct}_energy_consumption_on_peak"]] == [12.0, 12.0]
    # The first fetch reaches back a year for the time-of-use periods, a month per request;
    # once caught up, only the sensor's day is fetched.
    assert (calls[-1][1] - calls[0][0]).days == 401 and len(calls) == 13
    assert all((e - s).days <= 31 for s, e in calls)
    first = len(calls)
    await driver.async_poll()
    assert calls[first:] == [(dt_mod.datetime(2026, 7, 7, 9, 30, tzinfo=srp.PHOENIX),
                              dt_mod.datetime(2026, 7, 8, 9, 30, tzinfo=srp.PHOENIX))]
    # The sensor keeps HA's meaning: yesterday's and today's hours only.
    assert hub.states.get("sensor.srp_energy_home_energy_usage").state == str(round(18 * 1.0 + 6 * 2.0, 2))
    stats.close()


async def test_statistics_can_be_turned_off(tmp_path, monkeypatch):
    from services.homehub.statistics import Statistics

    stats = Statistics(str(tmp_path / "s.db"), None)
    hub, driver = await setup(FakeClient())
    driver.entry.options["statistics"] = False
    hub.statistics = stats
    await driver.async_setup()
    assert stats.all_meta() == []
    stats.close()


async def test_an_account_billed_in_no_period_backfills_once(tmp_path, monkeypatch):
    import datetime as dt_mod

    from services.homehub.statistics import Statistics

    stats = Statistics(str(tmp_path / "s.db"), None, time_zone="America/Phoenix")
    hub, driver = await setup(FakeClient())
    hub.statistics = stats
    raw = _raw("2026-07-07", range(0, 24), range(0))
    for r in raw:
        r["onPeakKwh"] = r["offPeakKwh"] = 0.0
        r["totalKwh"] = 1.0
    calls = []

    def fake_fetch(client, start, end):
        calls.append(start)
        return srp.normalize_hourly(raw)

    class FrozenDT(dt_mod.datetime):
        @classmethod
        def now(cls, tz=None):
            return dt_mod.datetime(2026, 7, 8, 9, 30, tzinfo=tz)

    monkeypatch.setattr(srp, "fetch_hourly", fake_fetch)
    monkeypatch.setattr(srp, "datetime", FrozenDT)
    await driver.async_setup()
    first = len(calls)
    await driver.async_poll()
    assert first > 1 and len(calls) == first + 1
    stats.close()

"""The household time zone resolver: one order, no guessed default."""
from __future__ import annotations

import pytest

from services.common import household_tz as htz


@pytest.fixture(autouse=True)
def _fresh_cache():
    htz.reset_cache()
    yield
    htz.reset_cache()


def test_the_household_setting_wins_over_tz():
    assert htz.resolve({"timezone": "Europe/Berlin"}, env={"TZ": "America/Denver"}) == ("Europe/Berlin", htz.SOURCE_SETTING)


def test_tz_is_used_when_the_setting_is_empty():
    assert htz.resolve({"timezone": ""}, env={"TZ": "America/Denver"}) == ("America/Denver", htz.SOURCE_ENV)


def test_the_older_timezone_variable_is_an_alias_for_tz():
    assert htz.resolve(None, env={"TIMEZONE": "Asia/Tokyo"}) == ("Asia/Tokyo", htz.SOURCE_ENV)


def test_an_invalid_setting_is_skipped_and_reported_if_nothing_else_works():
    assert htz.resolve({"timezone": "Mars/Base"}, env={"TZ": "America/Denver"}) == ("America/Denver", htz.SOURCE_ENV)
    with pytest.raises(htz.HouseholdTimeZoneError, match="'Mars/Base' is not a valid"):
        htz.resolve({"timezone": "Mars/Base"}, env={})


def test_location_is_used_only_when_the_admin_turned_it_on(monkeypatch):
    monkeypatch.setattr(htz, "zone_from_location", lambda lat, lon: "America/Denver")
    loc = '{"name": "Home", "latitude": 39.7392, "longitude": -104.9903}'
    with pytest.raises(htz.HouseholdTimeZoneError):
        htz.resolve({"weather_location": loc}, env={})
    assert htz.resolve({"weather_location": loc, "timezone_from_location": "true"}, env={}) == (
        "America/Denver", htz.SOURCE_LOCATION,
    )


def test_location_comes_after_tz(monkeypatch):
    monkeypatch.setattr(htz, "zone_from_location", lambda lat, lon: "America/Denver")
    settings = {"timezone_from_location": "true", "weather_location": {"latitude": 39.7, "longitude": -105.0}}
    assert htz.resolve(settings, env={"TZ": "Europe/Berlin"}) == ("Europe/Berlin", htz.SOURCE_ENV)


def test_a_home_point_from_the_caller_counts_as_the_location(monkeypatch):
    monkeypatch.setattr(htz, "zone_from_location", lambda lat, lon: "America/Denver")
    assert htz.resolve({"timezone_from_location": "on"}, env={}, home={"latitude": 39.7, "longitude": -105.0})[0] == "America/Denver"


def test_derive_on_without_a_location_says_so():
    with pytest.raises(htz.HouseholdTimeZoneError, match="no home location is set"):
        htz.resolve({"timezone_from_location": "true"}, env={})


def test_nothing_configured_fails_and_names_all_three_ways():
    with pytest.raises(htz.HouseholdTimeZoneError) as err:
        htz.resolve({}, env={})
    text = str(err.value)
    assert "Settings > Household time zone" in text
    assert "TZ" in text
    assert "Derive from home location" in text


def test_refresh_reports_unreadable_settings_and_continues_with_tz(monkeypatch, caplog):
    monkeypatch.setenv("TZ", "America/Denver")

    def boom():
        raise OSError("identity down")

    caplog.set_level("INFO", logger="household_tz")
    assert htz.refresh_sync("svc", boom) == ("America/Denver", htz.SOURCE_ENV)
    assert "could not read the household time zone setting" in caplog.text
    assert "svc: time zone America/Denver (from TZ in .env)" in caplog.text
    assert htz.current() == ("America/Denver", htz.SOURCE_ENV)


async def test_async_refresh_uses_the_setting(monkeypatch):
    monkeypatch.delenv("TZ", raising=False)

    async def fetch():
        return {"timezone": "Europe/Berlin"}

    assert await htz.refresh("svc", fetch) == ("Europe/Berlin", htz.SOURCE_SETTING)
    assert htz.zoneinfo().key == "Europe/Berlin"


def test_current_without_any_zone_fails_fast(monkeypatch):
    monkeypatch.delenv("TZ", raising=False)
    monkeypatch.delenv("TIMEZONE", raising=False)
    with pytest.raises(htz.HouseholdTimeZoneError):
        htz.current()


def test_missing_timezonefinder_is_a_descriptive_error(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "timezonefinder":
            raise ImportError("nope")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(htz.HouseholdTimeZoneError, match="no timezonefinder"):
        htz.zone_from_location(39.7, -105.0)

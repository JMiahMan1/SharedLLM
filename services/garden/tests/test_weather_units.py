"""Weather units in a Garden forecast payload.

``services/common/weather.py`` reports units as a mapping
(``{"system": "imperial", "temp": "°F", "wind": "mph", "precip": "in"}``), so
``(forecast.get("units") or "imperial").lower()`` raised
``'dict' object has no attribute 'lower'`` on every poller cycle and every
``/today`` with weather configured -- which silently stopped rain-skip and the
frost/heat/wind alerts. These pin the reader that replaced it.
"""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]

from services.garden import weather_care as wc  # noqa: E402

METRIC_PAYLOAD = {
    "units": {"system": "metric", "temp": "°C", "wind": "km/h", "precip": "mm", "snow": "cm"},
    "current": {"label": "Clear", "temp": 21.0},
    "hourly": [{"precip": 1.0, "gust": 10.0, "temp": 20.0}],
    "daily": [{"low": 8.0, "high": 22.0}],
}


def test_the_mapping_the_weather_service_sends_is_read():
    assert wc._forecast_units({"units": {"system": "imperial"}}) == "imperial"
    assert wc._forecast_units({"units": {"system": "metric"}}) == "metric"


@pytest.mark.parametrize(
    "forecast, expected",
    [
        ({"units": "metric"}, "metric"),
        ({"units": "imperial"}, "imperial"),
        ({"units": "METRIC"}, "metric"),
        ({"units": None}, "imperial"),
        ({}, "imperial"),
        (None, "imperial"),
        # Anything unrecognised is imperial, never an exception.
        ({"units": {"system": "kelvin"}}, "imperial"),
        ({"units": {"temp": "°F"}}, "imperial"),
        ({"units": 5}, "imperial"),
        ({"units": ["metric"]}, "imperial"),
    ],
)
def test_unknown_or_odd_payloads_fall_back_to_imperial(forecast, expected):
    assert wc._forecast_units(forecast) == expected


@pytest.mark.asyncio
async def test_a_metric_forecast_no_longer_breaks_the_summary(monkeypatch):
    """The poller path: metric values are converted, not crashed on."""

    async def fake_forecast():
        return METRIC_PAYLOAD

    async def no_rain_skips(*_a, **_k):
        return []

    async def no_alerts(*_a, **_k):
        return None

    monkeypatch.setattr(wc, "_fetch_forecast", fake_forecast)
    monkeypatch.setattr(wc, "apply_rain_skip", no_rain_skips)
    monkeypatch.setattr(wc, "check_frost_alerts", no_rain_skips)
    monkeypatch.setattr(wc, "check_heat_advisory", no_alerts)
    monkeypatch.setattr(wc, "check_wind_advisory", no_alerts)

    # Nothing is relevant, so the summary is None -- reached without the units
    # read raising on the mapping, which is the whole point.
    assert await wc.build_weather_summary(_NoProfileSession(), None) is None


class _NoProfileSession:
    """Just enough Session for the profile lookup."""

    def exec(self, *_a, **_k):
        return self

    def first(self):
        return None

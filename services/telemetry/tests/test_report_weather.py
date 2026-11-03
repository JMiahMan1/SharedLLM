"""Reports carry the window's weather when there is any (docs/WEATHER.md)."""
from services.common import weather_client
from services.telemetry import reports


async def test_weather_stats_come_from_the_correlations(monkeypatch):
    asked = []

    async def correlations(days=60, metric=None, **kw):
        asked.append(days)
        return {"units": "°F", "insights": ["Steps: fewer when it rains."], "days": [
            {"date": "2026-11-02", "weather": {"mean": 40.0, "min": 30.0, "max": 50.0, "precip": 0.3,
                                               "hdd": 25.0, "cdd": 0.0, "wet_hours": 3}, "metrics": {}}]}

    monkeypatch.setattr(weather_client, "correlations", correlations)
    stats = await reports.collect_weather_stats("weekly")
    assert asked == [8]
    assert stats == {"units": "°F", "insights": ["Steps: fewer when it rains."], "days": [
        {"date": "2026-11-02", "mean": 40.0, "min": 30.0, "max": 50.0, "precip": 0.3, "hdd": 25.0, "cdd": 0.0}]}


async def test_no_weather_leaves_the_report_alone(monkeypatch):
    async def none(**kw):
        return None

    monkeypatch.setattr(weather_client, "correlations", none)
    assert await reports.collect_weather_stats("daily") is None

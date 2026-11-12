"""Open-Meteo and NWS weather entities, NWS sensors and weather.get_forecasts, with canned API answers."""
from __future__ import annotations

import time

import pytest

from services.homehub.core import HubError
from services.homehub.drivers import weather
from services.homehub.tests.conftest import make_hub

OFFSET = -7 * 3600


def open_meteo_payload(is_day=1, code=0):
    # The current hour at call time, not import time: a long suite crossing an
    # hour boundary otherwise makes the driver drop one more "past" hour.
    now_hour = int(time.time()) // 3600 * 3600
    day0 = now_hour - (now_hour + OFFSET) % 86400  # local midnight, as epoch
    return {
        "utc_offset_seconds": OFFSET,
        "current": {"temperature_2m": 71.3, "relative_humidity_2m": 23, "apparent_temperature": 69.0, "is_day": is_day,
                    "weather_code": code, "cloud_cover": 5, "pressure_msl": 1016.0, "wind_speed_10m": 4.2,
                    "wind_direction_10m": 210, "wind_gusts_10m": 9.1, "dew_point_2m": 32.0},
        "hourly": {"time": [now_hour - 3600, now_hour, now_hour + 3600],
                   "temperature_2m": [70, 71, 72], "relative_humidity_2m": [20, 21, 22],
                   "precipitation": [0, 0, 0.1], "precipitation_probability": [0, 5, 40],
                   "weather_code": [0, 0, 61], "wind_speed_10m": [3, 4, 5], "wind_direction_10m": [200, 210, 220],
                   "is_day": [1, 1, 0]},
        "daily": {"time": [day0, day0 + 86400], "weather_code": [0, 63], "temperature_2m_max": [75, 68],
                  "temperature_2m_min": [50, 49], "precipitation_sum": [0, 0.3],
                  "precipitation_probability_max": [0, 70], "wind_speed_10m_max": [8, 14],
                  "wind_direction_10m_dominant": [200, 250]},
    }


def om_hub():
    hub, entry = make_hub("open_meteo", data={"zone": "zone.home"},
                          entities=[{"entity_id": "weather.home", "unique_id": "01J0000000000000000000WTHR"}])
    hub.location["unit_system_v2"] = "us_customary"
    return hub, entry


async def test_open_meteo_publishes_ha_weather_and_uses_the_zone(monkeypatch):
    hub, entry = om_hub()
    hub.states.set("zone.home", "0", {"latitude": 39.7425, "longitude": -104.9857, "radius": 40})
    drv = weather.OpenMeteoDriver(hub, entry)
    seen = {}

    async def fake_get(url, params=None, headers=None):
        seen.update(params)
        return open_meteo_payload()

    monkeypatch.setattr(drv, "_get_json", fake_get)
    await drv.async_setup()
    assert seen["latitude"] == 39.7425 and seen["temperature_unit"] == "fahrenheit"
    st = hub.states.get("weather.home")
    assert st.state == "sunny"
    a = st.attributes
    assert a["temperature"] == 71.3 and a["humidity"] == 23 and a["wind_bearing"] == 210
    assert a["temperature_unit"] == "°F" and a["pressure_unit"] == "inHg" and a["wind_speed_unit"] == "mph"
    assert a["pressure"] == pytest.approx(30.0, abs=0.01)
    assert a["supported_features"] == 3
    assert a["forecast"][1]["condition"] == "rainy" and a["forecast"][1]["templow"] == 49


async def test_open_meteo_clear_night_and_location_fallback(monkeypatch):
    hub, entry = om_hub()
    drv = weather.OpenMeteoDriver(hub, entry)
    assert drv.location() == (39.9725, -105.3257)  # no zone state: the core config location

    async def fake_get(url, params=None, headers=None):
        return open_meteo_payload(is_day=0)

    monkeypatch.setattr(drv, "_get_json", fake_get)
    await drv.async_setup()
    assert hub.states.get("weather.home").state == "clear-night"


async def test_get_forecasts_service_daily_and_hourly(monkeypatch):
    hub, entry = om_hub()
    drv = weather.OpenMeteoDriver(hub, entry)

    async def fake_get(url, params=None, headers=None):
        return open_meteo_payload()

    monkeypatch.setattr(drv, "_get_json", fake_get)
    await drv.async_setup()
    hub.drivers[entry.entry_id] = drv
    res = await hub.call_service("weather", "get_forecasts", {"entity_id": "weather.home", "type": "daily"},
                                 return_response=True)
    daily = res["response"]["weather.home"]["forecast"]
    assert len(daily) == 2
    # The automation feeding the smart speaker reads [1].datetime and [1].condition.
    assert daily[1]["condition"] == "rainy" and daily[1]["datetime"].endswith("-07:00")
    res = await hub.call_service("weather", "get_forecasts", {"target": {"entity_id": "weather.home"},
                                                              "type": "hourly"}, return_response=True)
    hourly = res["response"]["weather.home"]["forecast"]
    assert len(hourly) == 2  # the past hour is dropped
    assert hourly[1]["condition"] == "rainy"
    with pytest.raises(HubError):
        await hub.call_service("weather", "get_forecasts", {"entity_id": "weather.home", "type": "twice_daily"})
    with pytest.raises(HubError):
        await hub.call_service("weather", "get_forecasts", {"entity_id": "weather.home", "type": "weekly"})


async def test_forecast_service_is_registered_once():
    hub, _entry = om_hub()
    weather.ensure_forecast_service(hub)
    handler = hub.services[("weather", "get_forecasts")]
    weather.ensure_forecast_service(hub)
    assert hub.services[("weather", "get_forecasts")] is handler


def test_wmo_and_nws_conditions():
    assert weather.condition_from_wmo(65, True) == "pouring"
    assert weather.condition_from_wmo(95, False) == "lightning"
    assert weather.condition_from_wmo(None, True) is None
    assert weather.nws_condition("https://api.weather.gov/icons/land/night/skc?size=medium") == "clear-night"
    assert weather.nws_condition("https://api.weather.gov/icons/land/day/rain_showers,20/tsra,60?size=small") \
        == "lightning-rainy"
    assert weather.nws_condition("https://api.weather.gov/icons/land/day/wind_bkn") == "windy-variant"
    assert weather.nws_condition(None) is None
    assert weather.parse_wind_speed("5 to 10 mph") == 7.5


# -- NWS ----------------------------------------------------------------------------

LAT, LON = 39.739226063935485, -104.99029569931032
P = f"{LAT}_{LON}"

OBSERVATION = {"properties": {
    "icon": "https://api.weather.gov/icons/land/day/few?size=medium",
    "temperature": {"unitCode": "wmoUnit:degC", "value": 25.0},
    "dewpoint": {"unitCode": "wmoUnit:degC", "value": 0.0},
    "relativeHumidity": {"unitCode": "wmoUnit:percent", "value": 19.6},
    "windSpeed": {"unitCode": "wmoUnit:km_h-1", "value": 16.09344},
    "windDirection": {"unitCode": "wmoUnit:degree_(angle)", "value": 270},
    "barometricPressure": {"unitCode": "wmoUnit:Pa", "value": 101592},
    "seaLevelPressure": {"unitCode": "wmoUnit:Pa", "value": None},
    "visibility": {"unitCode": "wmoUnit:m", "value": 16090},
}}
PERIOD = {"startTime": "2026-11-11T18:00:00-07:00", "isDaytime": False, "temperature": 52, "temperatureUnit": "F",
          "probabilityOfPrecipitation": {"value": 10}, "windSpeed": "5 to 10 mph", "windDirection": "SW",
          "icon": "https://api.weather.gov/icons/land/night/sct?size=medium", "detailedForecast": "Partly cloudy."}


def nws_hub():
    ents = [{"entity_id": "weather.forecast", "unique_id": f"{P}_daynight"},
            {"entity_id": "sensor.outside_temperature", "unique_id": f"{P}_temperature"},
            {"entity_id": "sensor.dew_point", "unique_id": f"{P}_dewpoint"},
            {"entity_id": "sensor.relative_humidity", "unique_id": f"{P}_relativeHumidity"},
            {"entity_id": "sensor.wind_speed", "unique_id": f"{P}_windSpeed"},
            {"entity_id": "sensor.barometric_pressure", "unique_id": f"{P}_barometricPressure"}]
    hub, entry = make_hub("nws", data={"api_key": "someone@example.com", "station": "KIWA",
                                       "location": {"latitude": LAT, "longitude": LON}}, entities=ents)
    hub.location["unit_system_v2"] = "us_customary"
    return hub, entry


def nws_routes(calls):
    async def fake_get(url, params=None, headers=None):
        calls.append((url, headers))
        if url.endswith("/stations/KIWA/observations/latest"):
            return OBSERVATION
        if "/points/" in url:
            return {"properties": {"forecast": "https://nws.test/fc", "forecastHourly": "https://nws.test/fch"}}
        if url == "https://nws.test/fc":
            return {"properties": {"periods": [PERIOD]}}
        if url == "https://nws.test/fch":
            return {"properties": {"periods": [dict(PERIOD, isDaytime=True)]}}
        raise AssertionError(url)

    return fake_get


async def test_nws_weather_and_sensors(monkeypatch):
    hub, entry = nws_hub()
    drv = weather.NWSDriver(hub, entry)
    calls = []
    monkeypatch.setattr(drv, "_get_json", nws_routes(calls))
    await drv.async_setup()
    assert calls[0][1]["User-Agent"] == "(homehub, someone@example.com)"
    assert any(f"/points/{LAT:.4f},{LON:.4f}" in c[0] for c in calls)
    st = hub.states.get("weather.forecast")
    assert st.state == "partlycloudy"
    assert st.attributes["temperature"] == 77.0 and st.attributes["supported_features"] == 6
    assert st.attributes["pressure"] == pytest.approx(30.0, abs=0.01)  # station pressure when no sea level
    assert hub.states.get("sensor.outside_temperature").state == "77.0"
    assert hub.states.get("sensor.outside_temperature").attributes["unit_of_measurement"] == "°F"
    assert hub.states.get("sensor.dew_point").state == "32.0"
    assert hub.states.get("sensor.relative_humidity").state == "19.6"
    assert hub.states.get("sensor.wind_speed").state == "10.0"
    assert hub.states.get("sensor.barometric_pressure").state == "30.0"
    assert hub.states.get("sensor.barometric_pressure").attributes["device_class"] == "pressure"


async def test_nws_forecasts(monkeypatch):
    hub, entry = nws_hub()
    drv = weather.NWSDriver(hub, entry)
    monkeypatch.setattr(drv, "_get_json", nws_routes([]))
    await drv.async_setup()
    hub.drivers[entry.entry_id] = drv
    res = await hub.call_service("weather", "get_forecasts", {"entity_id": "weather.forecast",
                                                              "type": "twice_daily"}, return_response=True)
    period = res["response"]["weather.forecast"]["forecast"][0]
    assert period["condition"] == "partlycloudy" and period["is_daytime"] is False
    assert period["temperature"] == 52 and period["wind_bearing"] == 225.0 and period["wind_speed"] == 7.5
    assert period["precipitation_probability"] == 10
    with pytest.raises(HubError):
        await hub.call_service("weather", "get_forecasts", {"entity_id": "weather.forecast", "type": "daily"})


async def test_nws_sensor_honours_a_metric_unit_override(monkeypatch):
    hub, entry = nws_hub()
    hub.entities["sensor.outside_temperature"].unit_of_measurement = "°C"
    drv = weather.NWSDriver(hub, entry)
    monkeypatch.setattr(drv, "_get_json", nws_routes([]))
    await drv.async_setup()
    assert hub.states.get("sensor.outside_temperature").state == "25.0"


async def test_nws_total_failure_raises_for_a_retry(monkeypatch):
    from services.homehub.core import DeviceUnavailable

    hub, entry = nws_hub()
    drv = weather.NWSDriver(hub, entry)

    async def down(url, params=None, headers=None):
        raise DeviceUnavailable("api.weather.gov answered HTTP 503")

    monkeypatch.setattr(drv, "_get_json", down)
    with pytest.raises(DeviceUnavailable):
        await drv.async_setup()

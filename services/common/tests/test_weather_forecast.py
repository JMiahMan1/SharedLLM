"""Open-Meteo forecast parsing and the wording every reader shares (docs/WEATHER.md)."""
from datetime import UTC, datetime

import pytest

from services.common import weather as wx

OFFSET = -6 * 3600  # America/Chicago in November


def local_ts(day: int, hour: int) -> float:
    """Epoch seconds for a local wall-clock hour at UTC-6."""
    return datetime(2026, 11, day, hour, tzinfo=UTC).timestamp() - OFFSET


def raw(hours=48):
    """An Open-Meteo answer shaped like the live one (2026-11-03, UTC-6)."""
    times = [f"2026-11-{3 + h // 24:02d}T{h % 24:02d}:00" for h in range(hours)]
    return {
        "latitude": 35.997, "longitude": -86.788, "elevation": 209.0,
        "utc_offset_seconds": OFFSET, "timezone": "America/Chicago",
        "current": {"time": "2026-11-03T07:15", "temperature_2m": 59.9, "apparent_temperature": 60.0,
                    "relative_humidity_2m": 83, "precipitation": 0.0, "weather_code": 2,
                    "wind_speed_10m": 3.5, "wind_gusts_10m": 14.1, "is_day": 1},
        "hourly": {
            "time": times,
            "temperature_2m": [50 + h % 24 for h in range(hours)],
            "precipitation_probability": [80 if h == 15 else 5 for h in range(hours)],
            "precipitation": [0.2 if h == 15 else 0.0 for h in range(hours)],
            "snowfall": [0.0] * hours,
            "weather_code": [63 if h == 15 else 1 for h in range(hours)],
            "wind_speed_10m": [5.0] * hours,
            "wind_gusts_10m": [12.0] * hours,
        },
        "daily": {
            "time": ["2026-11-03", "2026-11-04"],
            "weather_code": [63, 0], "temperature_2m_max": [78.0, 67.9], "temperature_2m_min": [57.2, 56.0],
            "precipitation_sum": [0.2, 0.0], "precipitation_probability_max": [80, 5], "snowfall_sum": [0, 0],
            "wind_gusts_10m_max": [39.6, 20.0], "sunrise": ["2026-11-03T06:11", "2026-11-04T06:12"],
            "sunset": ["2026-11-03T16:49", "2026-11-04T16:48"],
        },
    }


NOW = local_ts(3, 7) + 15 * 60  # 7:15 local


def test_parse_keeps_hours_from_the_current_one_and_converts_local_times():
    fc = wx.parse_forecast(raw(), units="imperial", now=NOW, name="Farm")
    assert fc["location"] == {"name": "Farm", "latitude": 35.997, "longitude": -86.788, "elevation": 209.0}
    assert fc["units"] == {"system": "imperial", "temp": "°F", "wind": "mph", "precip": "in", "snow": "in"}
    assert fc["hourly"][0]["time"] == "2026-11-03T07:00"
    assert fc["hourly"][0]["ts"] == local_ts(3, 7)
    assert len(fc["hourly"]) == 41  # 07:00 on the 3rd to the end of the 4th
    assert fc["current"]["label"] == "Partly cloudy" and fc["current"]["icon"] == "partly_cloudy"
    assert fc["current"]["temp"] == 59.9 and fc["current"]["is_day"] is True
    rain = next(h for h in fc["hourly"] if h["time"] == "2026-11-03T15:00")
    assert (rain["label"], rain["icon"], rain["precip_prob"]) == ("Rain", "rain", 80)
    assert fc["daily"][0]["high"] == 78.0 and fc["daily"][0]["label"] == "Rain"


def test_hourly_is_capped_at_48_hours():
    fc = wx.parse_forecast(raw(hours=96), now=local_ts(3, 0))
    assert len(fc["hourly"]) == wx.HOURLY_HOURS


def test_unknown_codes_and_units_fall_back():
    assert wx.describe(None) == ("Unknown", "cloudy")
    assert wx.describe(1234) == ("Unknown", "cloudy")
    assert wx.units_for("kelvin") == "imperial"
    assert wx.request_params(36.0, -86.8, "metric")["temperature_unit"] == "celsius"
    assert wx.request_params(36.0, -86.8)["precipitation_unit"] == "inch"


def test_degree_days_use_65F_or_18C():
    assert wx.degree_days(40) == (25.0, 0.0)
    assert wx.degree_days(80) == (0.0, 15.0)
    assert wx.degree_days(10, "metric") == (8.0, 0.0)
    assert wx.degree_days(None) == (0.0, 0.0)


def test_summary_line_names_the_coming_rain():
    fc = wx.parse_forecast(raw(), now=NOW)
    assert wx.summary_line(fc) == "Partly cloudy, 60°F. High 78°, low 57°. Rain likely from 3 PM (80%)."
    assert wx.summary_line({}) == ""


def test_context_text_carries_now_and_every_day():
    text = wx.context_text(wx.parse_forecast(raw(), now=NOW))
    assert "Open-Meteo" in text and "Now: Partly cloudy, 60°F" in text
    assert "2026-11-04: Clear, high 68°F, low 56°F" in text


def test_event_note_only_when_weather_is_worth_saying():
    fc = wx.parse_forecast(raw(), now=NOW)
    assert wx.event_note(fc, local_ts(3, 15) + 600) == "Rain likely (80%), 65°F."
    assert wx.event_note(fc, local_ts(3, 12)) == ""
    assert wx.event_note(fc, local_ts(9, 12)) == ""  # beyond the forecast


def test_each_night_carries_its_cloud_cover_for_stargazing():
    data = raw()
    # Overcast until 22:00 on the 3rd, clear from 23:00 on.
    data["hourly"]["cloud_cover"] = [100 if h < 23 else 0 for h in range(48)]
    data["daily"]["cloud_cover_mean"] = [90, 10]
    fc = wx.parse_forecast(data, now=NOW)
    third, fourth = fc["daily"]
    # The night of the 3rd: 21, 22 overcast; 23, 00, 01 clear.
    assert third["night_cloud"] == 40
    assert third["cloud"] == 90
    # The night of the 4th has 21:00-23:00 only (3 of 5 hours), all clear.
    assert fourth["night_cloud"] == 0
    assert fc["hourly"][0]["cloud"] == 100


def test_a_night_mostly_past_the_forecast_has_no_cloud_figure():
    data = raw(hours=46)  # ends at 21:00 on the 4th
    data["hourly"]["cloud_cover"] = [50] * 46
    fc = wx.parse_forecast(data, now=NOW)
    assert fc["daily"][1]["night_cloud"] is None
    assert fc["daily"][1]["cloud"] is None  # no daily mean was sent


# --- spoken weather questions ----------------------------------------------------------


@pytest.mark.parametrize("query, when, rain", [
    ("What's the weather going to be today?", "today", False),
    ("what's the forecast", "today", False),
    ("Will it rain tomorrow?", "tomorrow", True),
    ("how cold will it be tonight", "tonight", False),
    ("Do I need an umbrella on Friday?", "friday", True),
    ("what's the weather like this weekend", "weekend", False),
    ("Is it going to snow tomorrow night?", "tomorrow night", True),
    ("what's the weather in the morning", "today", False),
])
def test_plain_weather_questions_are_recognised(query, when, rain):
    assert wx.weather_question(query) == {"when": when, "rain": rain}


@pytest.mark.parametrize("query", [
    "What's the weather in Seattle?",
    "weather in denver tomorrow",
    "remind me to bring an umbrella if it rains",
    "turn on the fan, it's hot",
    "is the weather station battery low",
    "what was the weather yesterday",
    "turn off the piano lamp",
    "",
])
def test_other_requests_go_to_the_model(query):
    assert wx.weather_question(query) is None


def test_today_answers_from_the_forecast_with_now_and_the_rain_hour():
    fc = wx.parse_forecast(raw(), now=NOW)
    text = wx.answer_weather(fc, "What's the weather going to be today?", now=NOW)
    assert text.startswith("Right now it's partly cloudy and 60°F.")
    assert "Today: rain, high 78°F, low 57°F, 80% chance of rain." in text
    assert "Rain is likely from 3 PM." in text


def test_tomorrow_and_a_rain_question_lead_with_the_verdict():
    fc = wx.parse_forecast(raw(), now=NOW)
    assert wx.answer_weather(fc, "will it rain tomorrow", now=NOW) == \
        "Rain is unlikely. Tomorrow: clear, high 68°F, low 56°F, 5% chance of rain."


def test_tonight_uses_the_evening_hours():
    fc = wx.parse_forecast(raw(), now=NOW)
    text = wx.answer_weather(fc, "how cold will it be tonight", now=NOW)
    # 6 PM to 6 AM on the 3rd: temperatures 50 + hour of day, so the coldest is 50 at midnight.
    assert text == "Tonight: mostly clear, down to 50°F, 5% chance of rain."


def test_a_day_past_the_forecast_or_another_place_is_left_to_the_model():
    fc = wx.parse_forecast(raw(), now=NOW)
    assert wx.answer_weather(fc, "what's the weather on Saturday", now=NOW) == ""
    assert wx.answer_weather(fc, "what's the weather in Seattle", now=NOW) == ""
    assert wx.answer_weather({}, "what's the weather", now=NOW) == ""

"""Weather from Open-Meteo: one forecast shape for every reader.

Open-Meteo (https://open-meteo.com, AGPL-3.0 server, CC BY 4.0 data) needs no
API key and can be self-hosted; the ``weather_api_url`` setting points at a
self-hosted copy instead of the public ``api.open-meteo.com``. This module only
talks to it and turns the answer into plain dicts; caching, the household's
location, reminders and the energy rollup live in ``services/execution/weather.py``.

Everything here except ``fetch_forecast`` is pure, so it is tested without a
network.

Units are the household's choice (``weather_units``): ``imperial`` asks for
°F, mph and inches, ``metric`` for °C, km/h and millimetres. Open-Meteo does
the conversion, so a threshold in a reminder is always in the units shown.
"""
from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import aiohttp

log = logging.getLogger("weather")

DEFAULT_API_URL = "https://api.open-meteo.com"
#: Hours of hourly forecast kept (reminders look at most this far ahead).
HOURLY_HOURS = 48
FORECAST_DAYS = 7
_TIMEOUT = aiohttp.ClientTimeout(total=15)

UNITS = {
    "imperial": {"temperature_unit": "fahrenheit", "wind_speed_unit": "mph", "precipitation_unit": "inch",
                 "temp": "°F", "wind": "mph", "precip": "in", "snow": "in"},
    "metric": {"temperature_unit": "celsius", "wind_speed_unit": "kmh", "precipitation_unit": "mm",
               "temp": "°C", "wind": "km/h", "precip": "mm", "snow": "cm"},
}
# Open-Meteo gives snowfall in cm with metric units (not mm like rain), and in
# inches with imperial ones.

#: metric value = imperial value * factor (temperature is handled on its own).
_TO_METRIC = {"wind": 1.609344, "precip": 25.4, "snow": 2.54}


def convert(measure: str, value: float, src: str, dst: str) -> float:
    """``value`` of ``measure`` (temp, wind, precip, snow, %) from ``src`` units to ``dst``."""
    src, dst = units_for(src), units_for(dst)
    if src == dst or measure not in ("temp", *_TO_METRIC):
        return value
    if measure == "temp":
        out = (value - 32) * 5 / 9 if dst == "metric" else value * 9 / 5 + 32
    else:
        out = value * _TO_METRIC[measure] if dst == "metric" else value / _TO_METRIC[measure]
    return round(out, 1)

_CURRENT = ("temperature_2m", "apparent_temperature", "relative_humidity_2m", "precipitation",
            "weather_code", "wind_speed_10m", "wind_gusts_10m", "is_day")
_HOURLY = ("temperature_2m", "precipitation_probability", "precipitation", "snowfall",
           "weather_code", "wind_speed_10m", "wind_gusts_10m", "cloud_cover")
_DAILY = ("weather_code", "temperature_2m_max", "temperature_2m_min", "precipitation_sum",
          "precipitation_probability_max", "snowfall_sum", "wind_gusts_10m_max", "sunrise", "sunset",
          "cloud_cover_mean")

#: The night a day's ``night_cloud`` describes: 9 PM that evening to 2 AM the
#: next morning, local time (the hours someone would be out under the stars).
NIGHT_HOURS = (21, 22, 23, 0, 1)

#: WMO weather interpretation codes (Open-Meteo's ``weather_code``) as a short
#: label and an icon key the clients map to their own icon set.
WMO: dict[int, tuple[str, str]] = {
    0: ("Clear", "clear"),
    1: ("Mostly clear", "clear"),
    2: ("Partly cloudy", "partly_cloudy"),
    3: ("Overcast", "cloudy"),
    45: ("Fog", "fog"),
    48: ("Freezing fog", "fog"),
    51: ("Light drizzle", "drizzle"),
    53: ("Drizzle", "drizzle"),
    55: ("Heavy drizzle", "drizzle"),
    56: ("Freezing drizzle", "sleet"),
    57: ("Freezing drizzle", "sleet"),
    61: ("Light rain", "rain"),
    63: ("Rain", "rain"),
    65: ("Heavy rain", "rain"),
    66: ("Freezing rain", "sleet"),
    67: ("Freezing rain", "sleet"),
    71: ("Light snow", "snow"),
    73: ("Snow", "snow"),
    75: ("Heavy snow", "snow"),
    77: ("Snow grains", "snow"),
    80: ("Light showers", "rain"),
    81: ("Showers", "rain"),
    82: ("Heavy showers", "rain"),
    85: ("Snow showers", "snow"),
    86: ("Heavy snow showers", "snow"),
    95: ("Thunderstorm", "storm"),
    96: ("Thunderstorm with hail", "storm"),
    99: ("Thunderstorm with hail", "storm"),
}
#: Codes that mean something is falling from the sky.
WET_CODES = frozenset(c for c, (_, icon) in WMO.items() if icon in {"drizzle", "rain", "sleet", "snow", "storm"})


def describe(code: Any) -> tuple[str, str]:
    """(label, icon) for a WMO code; unknown codes read as "Unknown"."""
    try:
        return WMO.get(int(code), ("Unknown", "cloudy"))
    except (TypeError, ValueError):
        return ("Unknown", "cloudy")


def units_for(name: str | None) -> str:
    return name if name in UNITS else "imperial"


def _num(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _epoch(local_iso: str, offset_s: int) -> float | None:
    """Open-Meteo's local wall-clock time (no offset) as epoch seconds."""
    try:
        naive = datetime.fromisoformat(str(local_iso))
    except (TypeError, ValueError):
        return None
    return naive.replace(tzinfo=timezone(timedelta(seconds=offset_s))).timestamp()


def _night_cloud(hourly: dict, day: str) -> float | None:
    """Mean cloud cover (%) over ``NIGHT_HOURS`` of the night starting on
    ``day``, from the whole hourly answer (not the 48 hours kept), or None
    when fewer than half of those hours are in the forecast."""
    try:
        evening = datetime.strptime(day, "%Y-%m-%d")
    except (TypeError, ValueError):
        return None
    wanted = set()
    for hour in NIGHT_HOURS:
        when = evening + timedelta(days=1 if hour < 12 else 0, hours=hour)
        wanted.add(when.strftime("%Y-%m-%dT%H:%M"))
    cover = hourly.get("cloud_cover") or []
    values = [v for i, t in enumerate(hourly.get("time") or [])
              if t in wanted and i < len(cover) and (v := _num(cover[i])) is not None]
    if len(values) * 2 < len(NIGHT_HOURS):
        return None
    return round(sum(values) / len(values))


def parse_forecast(raw: dict, *, units: str = "imperial", now: float | None = None,
                   name: str | None = None) -> dict:
    """Open-Meteo's answer as the forecast shape every reader uses.

    ``hourly`` starts at the current hour and runs ``HOURLY_HOURS``; each
    hour and day carries ``ts`` (epoch seconds) next to its local time, so a
    reader never has to know the offset.
    """
    offset = int(raw.get("utc_offset_seconds") or 0)
    now = now if now is not None else datetime.now(UTC).timestamp()
    cur = raw.get("current") or {}
    label, icon = describe(cur.get("weather_code"))
    current = {
        "time": cur.get("time"),
        "ts": _epoch(cur.get("time"), offset) if cur.get("time") else None,
        "temp": _num(cur.get("temperature_2m")),
        "feels_like": _num(cur.get("apparent_temperature")),
        "humidity": _num(cur.get("relative_humidity_2m")),
        "precip": _num(cur.get("precipitation")),
        "code": cur.get("weather_code"),
        "label": label,
        "icon": icon,
        "wind": _num(cur.get("wind_speed_10m")),
        "gust": _num(cur.get("wind_gusts_10m")),
        "is_day": bool(cur.get("is_day", 1)),
    }

    hourly = []
    h = raw.get("hourly") or {}
    hour_start = now - (now % 3600)
    for i, t in enumerate(h.get("time") or []):
        ts = _epoch(t, offset)
        if ts is None or ts < hour_start or ts >= hour_start + HOURLY_HOURS * 3600:
            continue

        def col(key, i=i):
            values = h.get(key) or []
            return values[i] if i < len(values) else None

        hl, hi = describe(col("weather_code"))
        hourly.append({
            "time": t,
            "ts": ts,
            "temp": _num(col("temperature_2m")),
            "precip_prob": _num(col("precipitation_probability")),
            "precip": _num(col("precipitation")),
            "snow": _num(col("snowfall")),
            "code": col("weather_code"),
            "label": hl,
            "icon": hi,
            "wind": _num(col("wind_speed_10m")),
            "gust": _num(col("wind_gusts_10m")),
            "cloud": _num(col("cloud_cover")),
        })

    daily = []
    d = raw.get("daily") or {}
    for i, day in enumerate(d.get("time") or []):

        def dcol(key, i=i):
            values = d.get(key) or []
            return values[i] if i < len(values) else None

        dl, di = describe(dcol("weather_code"))
        daily.append({
            "date": day,
            "ts": _epoch(f"{day}T00:00", offset),
            "high": _num(dcol("temperature_2m_max")),
            "low": _num(dcol("temperature_2m_min")),
            "precip": _num(dcol("precipitation_sum")),
            "precip_prob": _num(dcol("precipitation_probability_max")),
            "snow": _num(dcol("snowfall_sum")),
            "gust": _num(dcol("wind_gusts_10m_max")),
            "code": dcol("weather_code"),
            "label": dl,
            "icon": di,
            "sunrise": dcol("sunrise"),
            "sunset": dcol("sunset"),
            "cloud": _num(dcol("cloud_cover_mean")),
            "night_cloud": _night_cloud(h, day),
        })

    u = UNITS[units_for(units)]
    return {
        "source": "open-meteo",
        "location": {
            "name": name or "Home",
            "latitude": _num(raw.get("latitude")),
            "longitude": _num(raw.get("longitude")),
            "elevation": _num(raw.get("elevation")),
        },
        "timezone": raw.get("timezone"),
        "utc_offset_seconds": offset,
        "units": {"system": units_for(units), "temp": u["temp"], "wind": u["wind"], "precip": u["precip"],
                  "snow": u["snow"]},
        "current": current,
        "hourly": hourly,
        "daily": daily,
        "fetched_at": now,
    }


def request_params(lat: float, lon: float, units: str = "imperial") -> dict:
    u = UNITS[units_for(units)]
    return {
        "latitude": round(float(lat), 4),
        "longitude": round(float(lon), 4),
        "current": ",".join(_CURRENT),
        "hourly": ",".join(_HOURLY),
        "daily": ",".join(_DAILY),
        "temperature_unit": u["temperature_unit"],
        "wind_speed_unit": u["wind_speed_unit"],
        "precipitation_unit": u["precipitation_unit"],
        "timezone": "auto",
        "forecast_days": FORECAST_DAYS,
    }


async def fetch_forecast(lat: float, lon: float, *, units: str = "imperial", base_url: str | None = None,
                         name: str | None = None, client=None) -> dict:
    """Ask Open-Meteo for the forecast at ``lat``, ``lon``; raises on failure."""
    base = (base_url or DEFAULT_API_URL).rstrip("/")
    if client is None:
        from services.common.http import get_client

        client = get_client()
    async with client.get(f"{base}/v1/forecast", params=request_params(lat, lon, units), timeout=_TIMEOUT) as resp:
        if resp.status != 200:
            text = (await resp.text())[:200]
            raise RuntimeError(f"Open-Meteo answered HTTP {resp.status}: {text}")
        raw = await resp.json(content_type=None)
    if not isinstance(raw, dict) or raw.get("error"):
        raise RuntimeError(f"Open-Meteo refused the request: {(raw or {}).get('reason') if isinstance(raw, dict) else raw}")
    return parse_forecast(raw, units=units, name=name)


def degree_days(mean_temp: float | None, units: str = "imperial") -> tuple[float, float]:
    """(heating, cooling) degree days for one day's mean outdoor temperature.

    Base 65 °F, or 18 °C in metric: the usual balance point below which a
    house needs heat and above which it needs cooling.
    """
    if mean_temp is None:
        return (0.0, 0.0)
    base = 18.0 if units_for(units) == "metric" else 65.0
    return (round(max(0.0, base - mean_temp), 2), round(max(0.0, mean_temp - base), 2))


def _clock(ts: float, offset_s: int) -> str:
    t = datetime.fromtimestamp(ts, timezone(timedelta(seconds=offset_s)))
    return t.strftime("%I %p").lstrip("0") if t.minute == 0 else t.strftime("%I:%M %p").lstrip("0")


def _r(value: float | None) -> str:
    return "?" if value is None else str(round(value))


def summary_line(forecast: dict) -> str:
    """"Partly cloudy, 61°F. High 78°, low 57°. Rain likely from 3 PM (70%)." """
    if not forecast:
        return ""
    cur = forecast.get("current") or {}
    unit = (forecast.get("units") or {}).get("temp", "°")
    parts = [f"{cur.get('label') or 'Unknown'}, {_r(cur.get('temp'))}{unit}."]
    today = (forecast.get("daily") or [None])[0]
    if today:
        parts.append(f"High {_r(today.get('high'))}°, low {_r(today.get('low'))}°.")
    wet = next((h for h in (forecast.get("hourly") or [])[:12]
                if (h.get("precip_prob") or 0) >= 50 or (h.get("code") in WET_CODES and (h.get("precip") or 0) > 0)), None)
    if wet:
        when = "now" if wet["ts"] <= (forecast.get("fetched_at") or 0) else f"from {_clock(wet['ts'], forecast.get('utc_offset_seconds') or 0)}"
        what = "Snow" if wet.get("icon") == "snow" else "Rain"
        prob = f" ({_r(wet.get('precip_prob'))}%)" if wet.get("precip_prob") is not None else ""
        parts.append(f"{what} likely {when}{prob}.")
    return " ".join(parts)


def context_text(forecast: dict) -> str:
    """The forecast as compact text for the assistant's prompt."""
    if not forecast:
        return ""
    u = forecast.get("units") or {}
    t, w, p = u.get("temp", ""), u.get("wind", ""), u.get("precip", "")
    offset = forecast.get("utc_offset_seconds") or 0
    cur = forecast.get("current") or {}
    loc = (forecast.get("location") or {}).get("name") or "Home"
    lines = [
        f"Source: Open-Meteo forecast for {loc} ({forecast.get('timezone') or 'local time'}).",
        f"Now: {cur.get('label')}, {_r(cur.get('temp'))}{t} (feels like {_r(cur.get('feels_like'))}{t}), "
        f"humidity {_r(cur.get('humidity'))}%, wind {_r(cur.get('wind'))} {w} gusting {_r(cur.get('gust'))} {w}.",
    ]
    hours = (forecast.get("hourly") or [])[:24:3]
    if hours:
        lines.append("Next 24 h: " + "; ".join(
            f"{_clock(h['ts'], offset)} {_r(h.get('temp'))}{t} {h.get('label')} {_r(h.get('precip_prob'))}%"
            for h in hours))
    for day in forecast.get("daily") or []:
        lines.append(
            f"{day.get('date')}: {day.get('label')}, high {_r(day.get('high'))}{t}, low {_r(day.get('low'))}{t}, "
            f"rain chance {_r(day.get('precip_prob'))}%, precipitation {day.get('precip') or 0} {p}, "
            f"gusts {_r(day.get('gust'))} {w}.")
    return "\n".join(lines)


def hour_at(forecast: dict, ts: float) -> dict | None:
    """The forecast hour containing ``ts``, if the forecast reaches it."""
    for h in (forecast or {}).get("hourly") or []:
        if h["ts"] <= ts < h["ts"] + 3600:
            return h
    return None


def event_note(forecast: dict, ts: float) -> str:
    """A short weather note for something happening at ``ts`` ("" when nothing worth saying)."""
    h = hour_at(forecast, ts)
    if not h:
        return ""
    unit = ((forecast.get("units") or {}).get("temp") or "°")
    prob = h.get("precip_prob") or 0
    if h.get("icon") == "snow" and (h.get("snow") or 0) > 0:
        return f"Snow likely ({round(prob)}%), {_r(h.get('temp'))}{unit}."
    if prob >= 50 or h.get("icon") == "storm":
        return f"{'Storms' if h.get('icon') == 'storm' else 'Rain'} likely ({round(prob)}%), {_r(h.get('temp'))}{unit}."
    return ""


# --- Answering a spoken weather question ----------------------------------------------
#
# "What's the weather going to be today?" is answered from the cached forecast,
# without the language model: given the question, the model searched the web,
# read weather.com and was refused by AccuWeather while the forecast sat in its
# own context. Only plain questions about the weather here are answered; one
# about another place, or a request to do something ("remind me if it rains"),
# goes to the model as before.

_WEATHER_WORDS = re.compile(
    r"\b(weather|forecast|temperature|temp outside|umbrella|jacket|coat|"
    r"(will|is|does|did) it (be )?(going to )?(rain|snow|storm|drizzle|hail|freeze|be (hot|cold|warm|windy|sunny|cloudy|nice|chilly))|"
    r"(going|gonna) to (rain|snow)|raining|snowing|chance of (rain|snow)|"
    r"how (hot|cold|warm|windy|chilly) (is it|will it|was it|it is|it'?s)|"
    r"(high|low) (today|tonight|tomorrow))\b",
    re.IGNORECASE,
)
#: Asking for something to be done, not asked about: left to the model.
_NOT_A_QUESTION = re.compile(
    r"\b(remind|reminder|alert|notify|tell me when|let me know|turn|switch|set|schedule|add|create|"
    r"automation|play|open|close|lock|unlock|start|stop|thermostat|station|sensor|battery|widget|history|yesterday|last (week|month|year))\b",
    re.IGNORECASE,
)
#: "weather in Seattle", "forecast for denver": somewhere else, which the cached
#: forecast is not. Only times and near words may follow ("in the morning", "in
#: an hour", "for tomorrow", "at home"); transcripts are often lower case, so
#: capitals cannot be the test.
_ELSEWHERE = re.compile(
    r"\b(in|for|at|near|around) (?!(the|a|an|this|next|today|tonight|tomorrow|here|home|our|my|about|"
    r"morning|afternoon|evening|night|weekend|\d)\b)[a-z]",
    re.IGNORECASE,
)
_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def weather_question(query: str) -> dict | None:
    """``{"when": "today"|"tonight"|"tomorrow"|"weekend"|<weekday>, "rain": bool}`` for a
    plain question about the weather here, else None."""
    q = (query or "").strip()
    if not q or len(q) > 160 or not _WEATHER_WORDS.search(q) or _NOT_A_QUESTION.search(q) or _ELSEWHERE.search(q):
        return None
    low = q.lower()
    when = "today"
    if "tomorrow night" in low:
        when = "tomorrow night"
    elif "tonight" in low or "this evening" in low:
        when = "tonight"
    elif "tomorrow" in low:
        when = "tomorrow"
    elif "weekend" in low:
        when = "weekend"
    else:
        for day in _WEEKDAYS:
            if re.search(rf"\b{day}\b", low):
                when = day
                break
    rain = bool(re.search(r"\b(rain|raining|snow|snowing|storm|drizzle|umbrella|wet|precipitation)\b", low))
    return {"when": when, "rain": rain}


def _day_sentence(day: dict, unit: str, name: str) -> str:
    prob = day.get("precip_prob")
    rain = f", {_r(prob)}% chance of rain" if prob is not None else ""
    return f"{name}: {(day.get('label') or 'Unknown').lower()}, high {_r(day.get('high'))}{unit}, low {_r(day.get('low'))}{unit}{rain}."


def _night(forecast: dict, start_ts: float) -> dict | None:
    """6 PM to 6 AM from ``start_ts`` (that evening's 6 PM): the low, the wettest hour and the sky."""
    hours = [h for h in forecast.get("hourly") or [] if start_ts <= (h.get("ts") or 0) < start_ts + 12 * 3600]
    if not hours:
        return None
    temps = [h["temp"] for h in hours if h.get("temp") is not None]
    probs = [h["precip_prob"] for h in hours if h.get("precip_prob") is not None]
    labels = [h.get("label") for h in hours if h.get("label")]
    return {
        "low": min(temps) if temps else None,
        "precip_prob": max(probs) if probs else None,
        "label": max(set(labels), key=labels.count) if labels else "Unknown",
    }


def answer_weather(forecast: dict, query: str, now: float | None = None) -> str:
    """A spoken answer from the forecast, or "" when the question is not one it can
    answer (see ``weather_question``) or the day is past the forecast."""
    asked = weather_question(query)
    if not asked or not forecast:
        return ""
    now = now if now is not None else datetime.now(UTC).timestamp()
    offset = int(forecast.get("utc_offset_seconds") or 0)
    tz = timezone(timedelta(seconds=offset))
    today = datetime.fromtimestamp(now, tz).date()
    unit = (forecast.get("units") or {}).get("temp", "°")
    daily = {d.get("date"): d for d in forecast.get("daily") or [] if d.get("date")}

    def day_at(n: int) -> dict | None:
        return daily.get((today + timedelta(days=n)).isoformat())

    def evening(n: int) -> float:
        d = today + timedelta(days=n)
        return datetime(d.year, d.month, d.day, 18, tzinfo=tz).timestamp()

    when = asked["when"]
    parts: list[str] = []
    if when in ("tonight", "tomorrow night"):
        night = _night(forecast, evening(0 if when == "tonight" else 1))
        if not night:
            return ""
        name = "Tonight" if when == "tonight" else "Tomorrow night"
        prob = night["precip_prob"]
        rain = f", {_r(prob)}% chance of rain" if prob is not None else ""
        parts.append(f"{name}: {night['label'].lower()}, down to {_r(night['low'])}{unit}{rain}.")
        chance = prob
    elif when == "weekend":
        ahead = (5 - today.weekday()) % 7  # Saturday
        days = [(ahead + i, day_at(ahead + i)) for i in (0, 1)] if today.weekday() < 5 else \
            [(0, day_at(0)), (1, day_at(1))] if today.weekday() == 5 else [(0, day_at(0))]
        found = [(n, d) for n, d in days if d]
        if not found:
            return ""
        for n, d in found:
            parts.append(_day_sentence(d, unit, (today + timedelta(days=n)).strftime("%A")))
        chance = max((d.get("precip_prob") or 0) for _, d in found)
    else:
        if when == "today":
            n = 0
        elif when == "tomorrow":
            n = 1
        else:
            n = (_WEEKDAYS.index(when) - today.weekday()) % 7
        day = day_at(n)
        if not day:
            return ""
        if n == 0:
            cur = forecast.get("current") or {}
            if cur.get("temp") is not None:
                parts.append(f"Right now it's {(cur.get('label') or 'unknown').lower()} and {_r(cur.get('temp'))}{unit}.")
            name = "Today"
        elif n == 1:
            name = "Tomorrow"
        else:
            name = (today + timedelta(days=n)).strftime("%A")
        parts.append(_day_sentence(day, unit, name))
        chance = day.get("precip_prob")
        if n == 0:
            wet = next((h for h in (forecast.get("hourly") or [])[:18]
                        if (h.get("precip_prob") or 0) >= 50 and (h.get("ts") or 0) >= now - 3600), None)
            if wet:
                parts.append(f"Rain is likely from {_clock(wet['ts'], offset)}.")
    if asked["rain"] and chance is not None:
        verdict = "likely" if chance >= 50 else "possible" if chance >= 25 else "unlikely"
        parts.insert(0, f"Rain is {verdict}.")
    return " ".join(parts)

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
           "weather_code", "wind_speed_10m", "wind_gusts_10m")
_DAILY = ("weather_code", "temperature_2m_max", "temperature_2m_min", "precipitation_sum",
          "precipitation_probability_max", "snowfall_sum", "wind_gusts_10m_max", "sunrise", "sunset")

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

"""Weather: Open-Meteo (``weather.home``) and the National Weather Service (``weather.forecast`` + sensors).

Both publish HA's weather entity: the condition as the state (``sunny``,
``clear-night``, ``partlycloudy``, ``cloudy``, ``rainy``, ``pouring``, ...)
with ``temperature``, ``humidity``, ``pressure``, ``wind_speed``,
``wind_bearing`` and their ``*_unit`` attributes, in the household's unit
system (core config ``unit_system_v2``: ``us_customary`` gives F, inHg, mph).
Forecasts come from ``weather.get_forecasts`` (HA's service, with
``return_response``): ``{entity_id: {"forecast": [...]}}``. The state also
carries a ``forecast`` attribute (the first daily or twice-daily periods),
which HA dropped in 2024 but Jarvis's gateway still reads when execution's
own Open-Meteo summary is missing.

Open-Meteo: entry data ``zone`` (``zone.home``); the location is that zone's
state when it has one, else the hub's core config location. Supports
``daily`` and ``hourly``, as HA's integration does.

NWS: entry data ``api_key`` (HA sends it as the contact in the User-Agent the
NWS API asks for), ``location`` ``{latitude, longitude}`` and ``station``
(``KIWA``). Supports ``hourly`` and ``twice_daily``, as HA's does. Its sensors
(unique ids ``{lat}_{lon}_{key}``) are the latest station observation.
"""
from __future__ import annotations

import math
import re
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import aiohttp

from services.homehub.core import UNAVAILABLE, DeviceUnavailable, Driver, Hub, HubError, ServiceCall

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
NWS_URL = "https://api.weather.gov"
HTTP_TIMEOUT = aiohttp.ClientTimeout(total=20, sock_connect=10)
NWS_ATTRIBUTION = "Data from National Weather Service/NOAA"
OPEN_METEO_ATTRIBUTION = "Weather data provided by Open-Meteo"

# HA's WeatherEntityFeature.
FORECAST_DAILY, FORECAST_HOURLY, FORECAST_TWICE_DAILY = 1, 2, 4
FORECAST_TYPES = {"daily": FORECAST_DAILY, "hourly": FORECAST_HOURLY, "twice_daily": FORECAST_TWICE_DAILY}

#: HA's open_meteo WMO_TO_HA_CONDITION_MAP.
WMO_CONDITION = {
    0: "sunny", 1: "sunny", 2: "partlycloudy", 3: "cloudy", 45: "fog", 48: "fog",
    51: "rainy", 53: "rainy", 55: "rainy", 56: "rainy", 57: "rainy",
    61: "rainy", 63: "rainy", 65: "pouring", 66: "rainy", 67: "rainy",
    71: "snowy", 73: "snowy", 75: "snowy", 77: "snowy",
    80: "rainy", 81: "pouring", 82: "pouring", 85: "snowy", 86: "snowy",
    95: "lightning", 96: "lightning", 99: "lightning",
}

#: NWS icon codes (api.weather.gov/icons) -> HA condition, as HA's nws CONDITION_CLASSES.
NWS_CONDITION = {
    "skc": "sunny", "few": "partlycloudy", "sct": "partlycloudy", "bkn": "cloudy", "ovc": "cloudy",
    "wind_skc": "windy", "wind_few": "windy", "wind_sct": "windy",
    "wind_bkn": "windy-variant", "wind_ovc": "windy-variant",
    "snow": "snowy", "sleet": "snowy", "snow_sleet": "snowy", "blizzard": "snowy",
    "rain_snow": "snowy-rainy", "rain_sleet": "snowy-rainy", "fzra": "snowy-rainy",
    "rain_fzra": "snowy-rainy", "snow_fzra": "snowy-rainy",
    "rain": "rainy", "rain_showers": "rainy", "rain_showers_hi": "rainy",
    "tsra": "lightning-rainy", "tsra_sct": "lightning-rainy", "tsra_hi": "lightning-rainy",
    "fog": "fog",
    "tornado": "exceptional", "hurricane": "exceptional", "tropical_storm": "exceptional",
    "dust": "exceptional", "smoke": "exceptional", "haze": "exceptional", "hot": "exceptional",
    "cold": "exceptional",
}

CARDINAL = {d: i * 22.5 for i, d in enumerate(
    ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"])}

UNITS = {
    "imperial": {"temperature_unit": "°F", "pressure_unit": "inHg", "wind_speed_unit": "mph",
                 "visibility_unit": "mi", "precipitation_unit": "in"},
    "metric": {"temperature_unit": "°C", "pressure_unit": "hPa", "wind_speed_unit": "km/h",
               "visibility_unit": "km", "precipitation_unit": "mm"},
}


def unit_system(hub: Hub) -> str:
    raw = str((hub.location or {}).get("unit_system_v2") or (hub.location or {}).get("unit_system") or "")
    return "metric" if raw == "metric" else "imperial"


# -- unit conversion (SI in, display unit out) --------------------------------

def c_to(value: float, unit: str) -> float:
    return value * 9 / 5 + 32 if unit in ("°F", "F") else value


def kmh_to(value: float, unit: str) -> float:
    return {"mph": value / 1.609344, "m/s": value / 3.6, "kn": value / 1.852}.get(unit, value)


def pa_to(value: float, unit: str) -> float:
    return {"inHg": value / 3386.389, "hPa": value / 100, "mbar": value / 100, "kPa": value / 1000,
            "mmHg": value / 133.322, "psi": value / 6894.757}.get(unit, value)


def m_to(value: float, unit: str) -> float:
    return {"mi": value / 1609.344, "km": value / 1000, "ft": value * 3.28084}.get(unit, value)


def _num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def _r(v: float | None, nd: int = 1) -> float | None:
    return None if v is None else round(v, nd)


# -- the service ----------------------------------------------------------------

async def _get_forecasts(hub: Hub, call: ServiceCall) -> dict[str, Any]:
    ftype = str(call.data.get("type") or "")
    if ftype not in FORECAST_TYPES:
        raise HubError(f"weather.get_forecasts: type must be one of {', '.join(FORECAST_TYPES)}")
    if not call.entity_ids:
        raise HubError("weather.get_forecasts needs a target weather entity")
    out: dict[str, Any] = {}
    for eid in call.entity_ids:
        ent = hub.entities.get(eid)
        driver = hub.drivers.get(ent.config_entry_id or "") if ent is not None else None
        if not isinstance(driver, WeatherDriver):
            if eid.startswith("weather."):
                raise HubError(f"{eid}: no weather driver is running for it")
            continue
        out[eid] = {"forecast": await driver.async_forecast(ftype)}
    return out


def ensure_forecast_service(hub: Hub) -> None:
    """Register ``weather.get_forecasts`` once per hub, whichever driver starts first."""
    if ("weather", "get_forecasts") not in hub.services:
        hub.register_service("weather", "get_forecasts", _get_forecasts,
                             {"type": {"required": True, "selector": {"select": {"options": list(FORECAST_TYPES)}}}})


class WeatherDriver(Driver):
    """What both weather drivers share: the HA entity and the forecast lookup."""

    poll_interval = 600.0
    supported_forecasts = 0
    attribution = ""

    def __init__(self, hub: Hub, entry) -> None:
        super().__init__(hub, entry)
        self.forecasts: dict[str, list[dict[str, Any]]] = {}
        self.current: dict[str, Any] = {}
        self.condition: str | None = None
        self.units = UNITS[unit_system(hub)]

    async def _get_json(self, url: str, params: dict[str, Any] | None = None,
                        headers: dict[str, str] | None = None) -> Any:
        from services.common.http import get_client

        try:
            async with get_client().get(url, params=params, headers=headers, timeout=HTTP_TIMEOUT) as resp:
                if resp.status != 200:
                    raise DeviceUnavailable(f"{url} answered HTTP {resp.status}")
                return await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise DeviceUnavailable(f"{url}: {type(exc).__name__}: {exc}") from exc

    async def async_setup(self) -> None:
        ensure_forecast_service(self.hub)
        await self.refresh()

    async def async_poll(self) -> None:
        # A failed refresh raises and leaves the last forecast published: weather
        # a few polls old is more use than none.
        await self.refresh()

    async def refresh(self) -> None:
        raise NotImplementedError

    async def async_forecast(self, ftype: str) -> list[dict[str, Any]]:
        if not self.supported_forecasts & FORECAST_TYPES.get(ftype, 0):
            raise HubError(f"{self.entry.title}: forecast type {ftype} is not supported")
        return list(self.forecasts.get(ftype) or [])

    def weather_entities(self):
        return [e for e in self.entities if e.domain == "weather"]

    def publish_weather(self, summary_type: str) -> None:
        attrs: dict[str, Any] = {k: v for k, v in self.current.items() if v is not None}
        attrs.update(self.units)
        attrs["attribution"] = self.attribution
        attrs["supported_features"] = self.supported_forecasts
        attrs["forecast"] = (self.forecasts.get(summary_type) or [])[:7]
        for ent in self.weather_entities():
            self.publish(ent, self.condition or "unknown", attrs)


# -- Open-Meteo -----------------------------------------------------------------

class OpenMeteoDriver(WeatherDriver):
    platform = "open_meteo"
    supported_forecasts = FORECAST_DAILY | FORECAST_HOURLY
    attribution = OPEN_METEO_ATTRIBUTION

    def location(self) -> tuple[float, float]:
        zone = self.hub.states.get(str(self.entry.data.get("zone") or "zone.home"))
        attrs = zone.attributes if zone is not None else {}
        lat, lon = _num(attrs.get("latitude")), _num(attrs.get("longitude"))
        if lat is None or lon is None:
            lat, lon = _num(self.hub.location.get("latitude")), _num(self.hub.location.get("longitude"))
        if lat is None or lon is None:
            raise HubError("open_meteo: no location (zone or core config)")
        return lat, lon

    def params(self, lat: float, lon: float) -> dict[str, Any]:
        imperial = self.units["temperature_unit"] == "°F"
        return {
            "latitude": round(lat, 4), "longitude": round(lon, 4),
            "current": "temperature_2m,relative_humidity_2m,apparent_temperature,is_day,weather_code,cloud_cover,"
                       "pressure_msl,wind_speed_10m,wind_direction_10m,wind_gusts_10m,dew_point_2m",
            "hourly": "temperature_2m,relative_humidity_2m,precipitation,precipitation_probability,weather_code,"
                      "wind_speed_10m,wind_direction_10m,is_day",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,"
                     "precipitation_probability_max,wind_speed_10m_max,wind_direction_10m_dominant",
            "temperature_unit": "fahrenheit" if imperial else "celsius",
            "wind_speed_unit": "mph" if imperial else "kmh",
            "precipitation_unit": "inch" if imperial else "mm",
            "timezone": "auto", "timeformat": "unixtime", "forecast_days": 7,
        }

    async def refresh(self) -> None:
        lat, lon = self.location()
        raw = await self._get_json(OPEN_METEO_URL, self.params(lat, lon))
        if not isinstance(raw, dict) or raw.get("error"):
            raise DeviceUnavailable(f"Open-Meteo refused the request: {raw}")
        self.apply(raw)
        self.publish_weather("daily")

    def apply(self, raw: dict[str, Any]) -> None:
        offset = timezone(timedelta(seconds=int(raw.get("utc_offset_seconds") or 0)))
        cur = raw.get("current") or {}
        code = cur.get("weather_code")
        self.condition = condition_from_wmo(code, bool(cur.get("is_day", 1)))
        pressure = _num(cur.get("pressure_msl"))
        self.current = {
            "temperature": _r(_num(cur.get("temperature_2m"))),
            "apparent_temperature": _r(_num(cur.get("apparent_temperature"))),
            "dew_point": _r(_num(cur.get("dew_point_2m"))),
            "humidity": _r(_num(cur.get("relative_humidity_2m")), 0),
            "cloud_coverage": _r(_num(cur.get("cloud_cover")), 0),
            "pressure": _r(pa_to(pressure * 100, self.units["pressure_unit"]), 2) if pressure is not None else None,
            "wind_speed": _r(_num(cur.get("wind_speed_10m"))),
            "wind_gust_speed": _r(_num(cur.get("wind_gusts_10m"))),
            "wind_bearing": _num(cur.get("wind_direction_10m")),
        }

        def col(block: dict, key: str, i: int) -> Any:
            values = block.get(key) or []
            return values[i] if i < len(values) else None

        daily = []
        d = raw.get("daily") or {}
        for i, ts in enumerate(d.get("time") or []):
            daily.append(_clean({
                "datetime": datetime.fromtimestamp(int(ts), offset).isoformat(),
                "condition": condition_from_wmo(col(d, "weather_code", i), True),
                "temperature": _num(col(d, "temperature_2m_max", i)),
                "templow": _num(col(d, "temperature_2m_min", i)),
                "precipitation": _num(col(d, "precipitation_sum", i)),
                "precipitation_probability": _num(col(d, "precipitation_probability_max", i)),
                "wind_speed": _num(col(d, "wind_speed_10m_max", i)),
                "wind_bearing": _num(col(d, "wind_direction_10m_dominant", i)),
            }))
        hourly = []
        h = raw.get("hourly") or {}
        hour_now = datetime.now(UTC).timestamp() // 3600 * 3600
        for i, ts in enumerate(h.get("time") or []):
            if int(ts) < hour_now:
                continue
            hourly.append(_clean({
                "datetime": datetime.fromtimestamp(int(ts), UTC).isoformat(),
                "condition": condition_from_wmo(col(h, "weather_code", i), bool(col(h, "is_day", i) if
                                                                             col(h, "is_day", i) is not None else 1)),
                "temperature": _num(col(h, "temperature_2m", i)),
                "humidity": _num(col(h, "relative_humidity_2m", i)),
                "precipitation": _num(col(h, "precipitation", i)),
                "precipitation_probability": _num(col(h, "precipitation_probability", i)),
                "wind_speed": _num(col(h, "wind_speed_10m", i)),
                "wind_bearing": _num(col(h, "wind_direction_10m", i)),
            }))
        self.forecasts = {"daily": daily, "hourly": hourly}


def condition_from_wmo(code: Any, is_day: bool) -> str | None:
    try:
        cond = WMO_CONDITION.get(int(code))
    except (TypeError, ValueError):
        return None
    if cond == "sunny" and not is_day:
        return "clear-night"
    return cond


def _clean(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if v is not None}


# -- NWS --------------------------------------------------------------------------

#: NWS observation key -> what it measures (picks the conversion and HA's device_class).
NWS_SENSORS = {
    "temperature": "temperature", "dewpoint": "temperature", "windChill": "temperature",
    "heatIndex": "temperature", "relativeHumidity": "humidity", "windSpeed": "wind_speed",
    "windGust": "wind_speed", "windDirection": "angle", "barometricPressure": "pressure",
    "seaLevelPressure": "pressure", "visibility": "distance",
}
DEVICE_CLASS = {"temperature": "temperature", "humidity": "humidity", "wind_speed": "wind_speed",
                "pressure": "pressure", "distance": "distance"}


def obs_value(obs: dict[str, Any], key: str) -> tuple[float | None, str]:
    """An observation value in SI (degC, km/h, Pa, m, %, degrees) and its NWS unit code."""
    item = obs.get(key) or {}
    value = _num(item.get("value")) if isinstance(item, dict) else None
    code = str(item.get("unitCode") or "") if isinstance(item, dict) else ""
    if value is None:
        return None, code
    if code.endswith("m_s-1"):
        value *= 3.6
    elif code.endswith("degF"):
        value = (value - 32) * 5 / 9
    elif code.endswith(":hPa"):
        value *= 100
    return value, code


def convert(kind: str, si_value: float, unit: str) -> float:
    if kind == "temperature":
        return round(c_to(si_value, unit), 1)
    if kind == "wind_speed":
        return round(kmh_to(si_value, unit), 1)
    if kind == "pressure":
        return round(pa_to(si_value, unit), 2)
    if kind == "distance":
        return round(m_to(si_value, unit), 1)
    return round(si_value, 1)


def nws_condition(icon: str | None, is_day: bool = True) -> str | None:
    """HA's condition from an NWS icon URL (``.../icons/land/day/tsra,40/rain,20?size=medium``)."""
    if not icon:
        return None
    path = icon.split("?", 1)[0]
    parts = path.split("/")
    try:
        idx = max(i for i, p in enumerate(parts) if p in ("day", "night"))
    except ValueError:
        return None
    is_day = parts[idx] == "day"
    best, best_prob = None, -1
    for part in parts[idx + 1:]:
        code, _, prob = part.partition(",")
        p = int(prob) if prob.isdigit() else 0
        if code in NWS_CONDITION and p > best_prob:
            best, best_prob = code, p
    cond = NWS_CONDITION.get(best or "")
    if cond == "sunny" and not is_day:
        return "clear-night"
    return cond


def parse_wind_speed(text: Any) -> float | None:
    """'5 to 10 mph' -> 7.5 (HA averages the range)."""
    nums = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", str(text or ""))]
    return sum(nums) / len(nums) if nums else None


class NWSDriver(WeatherDriver):
    platform = "nws"
    supported_forecasts = FORECAST_HOURLY | FORECAST_TWICE_DAILY
    attribution = NWS_ATTRIBUTION

    def __init__(self, hub: Hub, entry) -> None:
        super().__init__(hub, entry)
        self._points: dict[str, Any] | None = None
        self.observation: dict[str, Any] = {}

    def location(self) -> tuple[float, float]:
        data = self.entry.data
        loc = data.get("location") if isinstance(data.get("location"), dict) else data
        lat, lon = _num(loc.get("latitude")), _num(loc.get("longitude"))
        if lat is None or lon is None:
            lat, lon = _num(self.hub.location.get("latitude")), _num(self.hub.location.get("longitude"))
        if lat is None or lon is None:
            raise HubError("nws: no location")
        return lat, lon

    @property
    def headers(self) -> dict[str, str]:
        # api.weather.gov wants a contact in the User-Agent; HA's config flow
        # calls it the "API key" and uses it for exactly that.
        contact = str(self.entry.data.get("api_key") or "homehub")
        return {"User-Agent": f"(homehub, {contact})", "Accept": "application/geo+json"}

    async def _points_props(self) -> dict[str, Any]:
        if self._points is None:
            lat, lon = self.location()
            raw = await self._get_json(f"{NWS_URL}/points/{lat:.4f},{lon:.4f}", headers=self.headers)
            self._points = (raw or {}).get("properties") or {}
        return self._points

    async def refresh(self) -> None:
        station = str(self.entry.data.get("station") or "")
        errors = []
        if station:
            try:
                raw = await self._get_json(f"{NWS_URL}/stations/{station}/observations/latest", headers=self.headers)
                self.observation = (raw or {}).get("properties") or {}
            except DeviceUnavailable as exc:
                errors.append(str(exc))
        try:
            props = await self._points_props()
            for ftype, key in (("twice_daily", "forecast"), ("hourly", "forecastHourly")):
                if props.get(key):
                    raw = await self._get_json(str(props[key]), headers=self.headers)
                    periods = ((raw or {}).get("properties") or {}).get("periods") or []
                    self.forecasts[ftype] = [self.period(p, ftype) for p in periods]
        except DeviceUnavailable as exc:
            errors.append(str(exc))
        if errors and not self.observation and not self.forecasts:
            raise DeviceUnavailable("; ".join(errors))
        self.apply_observation()
        self.publish_weather("twice_daily")
        self.publish_sensors()
        if errors:
            self.log.warning("NWS partly failed: %s", "; ".join(errors))

    def period(self, p: dict[str, Any], ftype: str) -> dict[str, Any]:
        is_day = bool(p.get("isDaytime", True))
        temp = _num(p.get("temperature"))
        if temp is not None and str(p.get("temperatureUnit") or "F") == "F" and self.units["temperature_unit"] == "°C":
            temp = round((temp - 32) * 5 / 9, 1)
        wind = parse_wind_speed(p.get("windSpeed"))
        if wind is not None:
            kmh = wind if "km/h" in str(p.get("windSpeed")) else wind * 1.609344
            wind = round(kmh_to(kmh, self.units["wind_speed_unit"]), 1)
        dew = (p.get("dewpoint") or {}).get("value") if isinstance(p.get("dewpoint"), dict) else None
        out = {
            "datetime": p.get("startTime"),
            "condition": nws_condition(p.get("icon"), is_day),
            "temperature": temp,
            "precipitation_probability": _num((p.get("probabilityOfPrecipitation") or {}).get("value")),
            "humidity": _num((p.get("relativeHumidity") or {}).get("value")),
            "dew_point": _r(c_to(float(dew), self.units["temperature_unit"])) if _num(dew) is not None else None,
            "wind_speed": wind,
            "wind_bearing": CARDINAL.get(str(p.get("windDirection") or "")),
        }
        if ftype == "twice_daily":
            out["is_daytime"] = is_day
            out["detailed_description"] = p.get("detailedForecast")
        return _clean(out)

    def apply_observation(self) -> None:
        obs = self.observation
        u = self.units
        values = {k: obs_value(obs, k)[0] for k in NWS_SENSORS}

        def disp(key: str, unit: str) -> float | None:
            v = values.get(key)
            return None if v is None else convert(NWS_SENSORS[key], v, unit)

        pressure = disp("seaLevelPressure", u["pressure_unit"])
        if pressure is None:
            pressure = disp("barometricPressure", u["pressure_unit"])
        self.current = {
            "temperature": disp("temperature", u["temperature_unit"]),
            "dew_point": disp("dewpoint", u["temperature_unit"]),
            "humidity": _r(values.get("relativeHumidity"), 0),
            "pressure": pressure,
            "wind_speed": disp("windSpeed", u["wind_speed_unit"]),
            "wind_gust_speed": disp("windGust", u["wind_speed_unit"]),
            "wind_bearing": values.get("windDirection"),
            "visibility": disp("visibility", u["visibility_unit"]),
        }
        cond = nws_condition(obs.get("icon"))
        if cond is None:
            hourly = self.forecasts.get("hourly") or []
            cond = hourly[0].get("condition") if hourly else None
        self.condition = cond

    def default_unit(self, kind: str) -> str | None:
        u = self.units
        return {"temperature": u["temperature_unit"], "humidity": "%", "wind_speed": u["wind_speed_unit"],
                "pressure": u["pressure_unit"], "distance": u["visibility_unit"], "angle": "°"}.get(kind)

    def publish_sensors(self) -> None:
        for ent in self.entities:
            if ent.domain != "sensor":
                continue
            key = ent.unique_id.rsplit("_", 1)[-1]
            kind = NWS_SENSORS.get(key)
            if kind is None:
                continue
            value, _code = obs_value(self.observation, key)
            unit = ent.unit_of_measurement or (ent.options.get("sensor") or {}).get("unit_of_measurement") \
                or self.default_unit(kind)
            attrs: dict[str, Any] = {"unit_of_measurement": unit, "state_class": "measurement",
                                     "attribution": NWS_ATTRIBUTION}
            if kind in DEVICE_CLASS:
                attrs["device_class"] = DEVICE_CLASS[kind]
            state: Any = UNAVAILABLE if not self.observation else "unknown"
            if value is not None:
                state = convert(kind, value, unit or "")
            self.publish(ent, state, attrs)

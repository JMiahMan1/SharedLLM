"""The weather, for any service (docs/WEATHER.md, "Using weather from a service").

Execution owns the forecast cache, the day store and the event stream; this
is the one way other services reach them, so a new service needs nothing but
this import::

    from services.common import weather_client as weather

    now = await weather.current()                     # {"temp", "label", "icon", ...} or None
    fc = await weather.forecast()                     # the full forecast (home, or lat/lon)
    day = await weather.day("2026-11-02")             # that day's weather and metrics
    rep = await weather.correlations(days=60)         # every metric against the weather
    await weather.push_metric("eggs", 11, unit="eggs")  # analysed against the weather from now on
    events, last = await weather.events(after=last)   # forecast.updated, alert.started, ...

Every call returns None (or an empty result) rather than raising when
execution or Open-Meteo is away, so weather is always an optional extra.
"""
from __future__ import annotations

import logging
from typing import Any

import aiohttp

log = logging.getLogger("weather_client")

_TIMEOUT = aiohttp.ClientTimeout(total=15)
#: The Redis stream execution publishes to (``services/execution/weather_bus.py``).
STREAM = "weather:events"
EVENTS = ("forecast.updated", "condition.changed", "alert.started", "day.completed")


async def _call(method: str, path: str, *, params: dict | None = None, json: dict | None = None,
                client=None, base: str | None = None, secret: str | None = None) -> Any:
    if base is None or secret is None:
        from services import config

        base = base or config.EXECUTION_SVC_URL
        secret = secret or config.INTERNAL_SECRET
    if client is None:
        from services.common.http import get_client

        client = get_client()
    try:
        async with client.request(method, f"{base.rstrip('/')}/weather{path}", params=params, json=json,
                                  headers={"X-Internal-Secret": secret}, timeout=_TIMEOUT) as resp:
            if resp.status != 200:
                log.info("weather %s %s: HTTP %s", method, path, resp.status)
                return None
            return await resp.json(content_type=None)
    except Exception as e:
        log.info("weather %s %s failed: %s: %s", method, path, type(e).__name__, e)
        return None


async def forecast(latitude: float | None = None, longitude: float | None = None, name: str | None = None,
                   **kw) -> dict | None:
    """The forecast at home, or at ``latitude``/``longitude``."""
    params = {k: v for k, v in (("latitude", latitude), ("longitude", longitude), ("name", name)) if v is not None}
    return await _call("GET", "/forecast", params=params or None, **kw)


async def current(**kw) -> dict | None:
    fc = await forecast(**kw)
    return (fc or {}).get("current") or None


async def summary(**kw) -> str:
    """One line for a prompt or a notice ("" when there is no forecast)."""
    return ((await _call("GET", "/context", **kw)) or {}).get("summary") or ""


async def day(date: str, **kw) -> dict | None:
    return await _call("GET", f"/day/{date}", **kw)


async def correlations(days: int = 60, metric: str | None = None, **kw) -> dict | None:
    params: dict[str, Any] = {"days": days}
    if metric:
        params["metric"] = metric
    return await _call("GET", "/correlations", params=params, **kw)


async def push_metric(name: str, value: float, *, ts: float | None = None, agg: str = "sum",
                      label: str | None = None, unit: str | None = None, span_s: float | None = None,
                      **kw) -> bool:
    """Record one value to be analysed against the weather; True when stored.

    ``agg`` says how a day's values combine: sum, mean, max, min or delta
    (last minus first, for a meter that only goes up).
    """
    body = {"name": name, "value": value, "ts": ts, "agg": agg, "label": label, "unit": unit, "span_s": span_s}
    return bool(await _call("POST", "/metrics", json={k: v for k, v in body.items() if v is not None}, **kw))


async def events(after: str = "-", count: int = 100, **kw) -> tuple[list[dict], str]:
    """Events after the stream id ``after``, and the id to pass next time."""
    got = ((await _call("GET", "/events", params={"after": after, "count": count}, **kw)) or {}).get("events") or []
    return got, (got[-1]["id"] if got else after)

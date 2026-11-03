"""Weather for the app (docs/WEATHER.md).

Anyone signed in reads the household forecast and their own "heads up" (the
weather reminders that name them, or everyone); the weather-vs-energy report
and the reminder test are for admins. Everything comes from execution's
``/weather/*`` routes, which own the Open-Meteo cache.
"""
from __future__ import annotations

from contextlib import suppress
from typing import Any

import aiohttp
from fastapi import APIRouter, HTTPException, Request

from services.gateway.config import EXECUTION_SVC, INTERNAL_SECRET

router = APIRouter(prefix="/api/weather")

_HEADERS = {"X-Internal-Secret": INTERNAL_SECRET}
_TIMEOUT = aiohttp.ClientTimeout(total=25)


async def _call(method: str, path: str, *, params: dict | None = None, json: dict | None = None) -> Any:
    from services.gateway.main import shared_http_client

    async with shared_http_client() as client, client.request(
        method, f"{EXECUTION_SVC.rstrip('/')}/weather{path}", params=params, json=json,
        headers=_HEADERS, timeout=_TIMEOUT,
    ) as resp:
        if resp.status != 200:
            detail = "The weather service did not answer"
            with suppress(Exception):
                detail = (await resp.json(content_type=None)).get("detail") or detail
            raise HTTPException(status_code=resp.status if resp.status in (404, 422) else 502, detail=detail)
        return await resp.json(content_type=None)


@router.get("")
async def weather(request: Request, household: bool = False):
    """The forecast for the caller and their heads-up.

    Their own place from Settings > Weather, else the household's (execution
    decides, so the dashboard and reminder notes agree). ``household=true`` is
    always the home forecast, for readings about the house itself, like the
    Climate widget's outdoor temperature.
    """
    from services.gateway import main as gateway

    user = await gateway._require_authenticated(request)
    if household:
        return {**await _call("GET", "/forecast"), "personal": False, "heads_up": []}
    forecast = await _call("GET", "/forecast", params={"user": user})
    try:
        heads = (await _call("GET", "/heads-up", params={"user": user})).get("items", [])
    except HTTPException:
        heads = []  # the forecast alone is still worth showing
    return {**forecast, "heads_up": heads}


@router.get("/energy")
async def weather_energy(request: Request, days: int = 30):
    from services.gateway import main as gateway

    await gateway._require_admin(request)
    return await _call("GET", "/energy", params={"days": max(1, min(days, 400))})


@router.post("/reminders/test")
async def test_reminder(request: Request):
    """Send one reminder rule to the admin who asked, now."""
    from services.gateway import main as gateway

    user = await gateway._require_admin(request)
    body = await request.json()
    rule = body.get("rule") if isinstance(body, dict) else None
    if not isinstance(rule, dict):
        raise HTTPException(status_code=422, detail="rule is required")
    return await _call("POST", "/reminders/test", json={"rule": rule, "user": user})


@router.get("/correlations")
async def weather_correlations(request: Request, days: int = 60, metric: str | None = None):
    """Admins: every tracked metric against the weather (docs/WEATHER.md)."""
    from services.gateway import main as gateway

    await gateway._require_admin(request)
    params: dict[str, Any] = {"days": max(1, min(days, 400))}
    if metric:
        params["metric"] = metric
    return await _call("GET", "/correlations", params=params)


@router.get("/integrations")
async def weather_integrations(request: Request):
    """Admins: the events, services, metric modes and metrics an integration may name."""
    from services.gateway import main as gateway

    await gateway._require_admin(request)
    info = await _call("GET", "/integrations")
    info["metrics"] = (await _call("GET", "/metrics")).get("metrics", [])
    return info

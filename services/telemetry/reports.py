# services/telemetry/reports.py
"""Report generation: collect the window's data, then analyze it.

Analysis always goes through the Gateway with the ``telemetry`` model role, so
report generation never shares the voice assistant's model. The request carries
``queue_timeout`` so Alpaca's slot queue waits rather than failing.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import aiohttp

from services.telemetry.config import (
    GATEWAY_INTERNAL_URL,
    GEO_SVC,
    IDENTITY_SVC,
    INTERNAL_SECRET,
    LLM_QUEUE_TIMEOUT_SECONDS,
    LLM_TIMEOUT_SECONDS,
)
from services.telemetry.schedule import PERIOD_DAYS, window_for
from services.telemetry.store import internal_headers, new_id, utcnow_iso

log = logging.getLogger("telemetry.reports")

PERIOD_LABELS = {
    "daily": "today",
    "weekly": "the last 7 days",
    "monthly": "the last 30 days",
    "yearly": "the last 365 days",
}


async def _get_json(session: aiohttp.ClientSession, url: str, params: dict | None = None) -> Any:
    async with session.get(url, params=params, headers=internal_headers()) as resp:
        if resp.status != 200:
            raise RuntimeError(f"GET {url} returned {resp.status}")
        return await resp.json(content_type=None)


async def collect_health_stats(
    session: aiohttp.ClientSession, user: str, period: str, tz: str
) -> dict[str, Any]:
    days = PERIOD_DAYS.get(period, 1)
    steps = await _get_json(
        session, f"{GEO_SVC}/steps", {"user_id": user, "days": max(days, 1)}
    )
    workouts = await _get_json(
        session, f"{GEO_SVC}/workouts", {"user_id": user, "limit": 100}
    )
    daily = steps.get("daily_steps") or {}
    values = [float(v) for v in daily.values() if isinstance(v, (int, float))]
    workout_list = workouts.get("workouts") or []
    return {
        "source": "geo",
        "steps_today": steps.get("today"),
        "steps_goal": steps.get("goal"),
        "days_with_data": len(values),
        "steps_total": sum(values),
        "steps_avg": (sum(values) / len(values)) if values else None,
        "steps_max": max(values) if values else None,
        "workout_count": len(workout_list),
        "workout_types": sorted({w.get("activity_type", "unknown") for w in workout_list}),
        "workout_distance_miles": sum(
            float(w.get("distance_miles") or 0) for w in workout_list
        ),
    }


async def collect_power_stats(session: aiohttp.ClientSession, period: str) -> dict[str, Any]:
    enrollments = await _get_json(session, f"{IDENTITY_SVC}/api/telemetry/enroll")
    entities = enrollments if isinstance(enrollments, list) else enrollments.get("enrollments", [])
    summaries: list[dict[str, Any]] = []
    for entity in entities:
        entity_id = entity.get("entity_id") if isinstance(entity, dict) else None
        if not entity_id:
            continue
        try:
            summary = await _get_json(session, f"{IDENTITY_SVC}/api/telemetry/summary/{entity_id}")
        except Exception as e:
            log.warning("power summary unavailable for %s: %s", entity_id, e)
            continue
        summaries.append({"entity_id": entity_id, **(summary if isinstance(summary, dict) else {})})
    return {
        "source": "identity",
        "entity_count": len(summaries),
        "entities": summaries,
    }


def build_prompt(
    report_type: str, period: str, stats: dict[str, Any], start: datetime, end: datetime
) -> str:
    label = PERIOD_LABELS.get(period, period)
    window = f"{start.isoformat()} to {end.isoformat()}"
    if report_type == "power":
        focus = (
            "Focus on energy use: totals, peak periods, standby/always-on waste, "
            "and the single highest-impact change to make next."
        )
    else:
        focus = (
            "Focus on activity: step consistency, workout mix, goal progress, "
            "and one concrete, achievable suggestion. Do not give medical advice."
        )
    return (
        f"Write a {period} {report_type} report for the {label} (window {window}).\n"
        f"Data:\n{stats}\n\n{focus}\n"
        "Keep it under 200 words, lead with the headline number, and use short bullets."
    )


async def analyze(
    session: aiohttp.ClientSession,
    report_type: str,
    period: str,
    stats: dict[str, Any],
    start: datetime,
    end: datetime,
    user: str,
) -> str:
    payload = {
        "model": "telemetry",
        "messages": [
            {
                "role": "system",
                "content": "You write concise personal telemetry reports from raw data.",
            },
            {"role": "user", "content": build_prompt(report_type, period, stats, start, end)},
        ],
        "rag_user": user,
        "think": False,
        "enable_thinking": False,
        "queue_timeout": LLM_QUEUE_TIMEOUT_SECONDS,
    }
    async with session.post(
        f"{GATEWAY_INTERNAL_URL}/v1/chat/completions",
        json=payload,
        headers=internal_headers(),
        timeout=aiohttp.ClientTimeout(total=LLM_TIMEOUT_SECONDS),
    ) as resp:
        if resp.status != 200:
            body = await resp.text()
            raise RuntimeError(f"analysis failed HTTP {resp.status}: {body[:200]}")
        data = await resp.json(content_type=None)
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError("analysis returned no choices")
    message = choices[0].get("message") or {}
    content = (message.get("content") or "").strip()
    if not content:
        raise RuntimeError("analysis returned empty content")
    return content


async def generate_report(
    session: aiohttp.ClientSession, user: str, report_type: str, period: str, tz: str
) -> dict[str, Any]:
    start, end = window_for(period, tz=tz)
    if report_type == "power":
        stats = await collect_power_stats(session, period)
    else:
        stats = await collect_health_stats(session, user, period, tz)
    if not stats or (report_type == "health" and not stats.get("days_with_data")):
        return {
            "id": new_id(),
            "user": user,
            "type": report_type,
            "period": period,
            "status": "no_data",
            "analysis": None,
            "stats": stats or {},
            "window_start": start.isoformat(),
            "window_end": end.isoformat(),
            "generated_at": utcnow_iso(),
            "generated_ts": int(end.timestamp()),
        }
    analysis = await analyze(session, report_type, period, stats, start, end, user)
    return {
        "id": new_id(),
        "user": user,
        "type": report_type,
        "period": period,
        "status": "ready",
        "analysis": analysis,
        "stats": stats,
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "generated_at": utcnow_iso(),
        "generated_ts": int(end.timestamp()),
    }

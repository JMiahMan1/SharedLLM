"""Watching (Phase 11, step 6): content monitoring and the parents' weekly recap.

Every activity event recorded for a child is scanned as it lands
(``services/common/concern_monitor.py``); a concern is kept for the recap, and
one that the child's sensitivity says pages a parent is sent to both parents
at once (deduplicated per child, category and day). On Sunday evening in the
household's time zone each child's recap (``services/common/guardian_recap.py``)
is built from the week's data, kept, and summarised to both parents.

Only minors with parents are watched; an adult's events are never scanned.

Second pass: each phrase-list hit is read in context by the household's own
model (``llm_local_url`` with the ``guardian_monitor_model`` setting) before
anyone is paged (``services/common/concern_review.py``). Without a model, or
when it cannot be reached, the phrase list decides alone and the reason is
logged and kept on the hit.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Awaitable, Callable
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import aiohttp

from services.common import concern_monitor, concern_review, guardian_recap
from services.telemetry import notices, store

log = logging.getLogger("telemetry.guardian")

CHILDREN_CACHE_SECONDS = 300
RECAP_HOUR = 18  # Sunday, household time
RECAP_TICK_SECONDS = 900

_children_cache: tuple[float, dict[str, dict[str, Any]]] | None = None
SETTINGS_CACHE_SECONDS = 60
MODEL_TIMEOUT_SECONDS = 25
_settings_cache: tuple[float, dict[str, str]] | None = None

Deliver = Callable[[dict[str, Any]], Awaitable[Any]]


def household_now() -> datetime:
    try:
        tz = ZoneInfo(os.getenv("TIMEZONE") or "UTC")
    except Exception:
        tz = ZoneInfo("UTC")
    return datetime.now(tz)


async def children(session) -> dict[str, dict[str, Any]]:
    """Minors with parents, by username (identity owns the rule)."""
    global _children_cache
    if _children_cache and _children_cache[0] > time.monotonic():
        return _children_cache[1]
    data = await notices._identity_get(session, "/api/internal/guardian/children")
    by_name = {c["username"]: c for c in (data or {}).get("children") or [] if c.get("username")}
    _children_cache = (time.monotonic() + CHILDREN_CACHE_SECONDS, by_name)
    return by_name


def _concern_notice(parent: str, child: dict[str, Any], concern: dict[str, Any], day: str) -> dict[str, Any]:
    name = child.get("display_name") or child["username"]
    priority = 5 if concern["category"] in concern_monitor.ALWAYS_ON else 4 if concern["level"] == "severe" else 3
    review = concern.get("review") or {}
    body = concern.get("excerpt") or ""
    if review.get("by") == "model" and review.get("reason"):
        body = f"{body}\n{'Checked' if review.get('verdict') == 'confirmed' else 'Unsure'}: {review['reason']}"
    return {
        "user": parent,
        "category": "family_safety",
        "kind": "concern",
        "priority": priority,
        "title": f"{concern['label']} on {name}'s phone",
        "body": body,
        "tag": f"concern:{child['username']}:{concern['category']}",
        "once": f"concern:{child['username']}:{concern['category']}:{day}",
        "data": {"url": f"/guardian?child={child['username']}", "child": child["username"], "level": concern["level"]},
    }


async def _model_settings(session) -> dict[str, str]:
    """llm_local_url and guardian_monitor_model from identity (cached a minute)."""
    global _settings_cache
    if _settings_cache and _settings_cache[0] > time.monotonic():
        return _settings_cache[1]
    rows = await notices._identity_get(session, "/api/settings")
    wanted = {"llm_local_url", "guardian_monitor_model"}
    found = {r["key"]: str(r.get("value") or "").strip() for r in rows or [] if isinstance(r, dict) and r.get("key") in wanted}
    _settings_cache = (time.monotonic() + SETTINGS_CACHE_SECONDS, found)
    return found


async def _ask_model(session, url: str, model: str, concern: dict[str, Any], text: str) -> dict[str, Any]:
    payload = {
        "model": model,
        "messages": concern_review.messages(concern, text),
        "temperature": 0,
        "max_tokens": 200,
        "stream": False,
    }
    async with session.post(
        f"{url.rstrip('/')}/v1/chat/completions",
        json=payload,
        timeout=aiohttp.ClientTimeout(total=MODEL_TIMEOUT_SECONDS),
    ) as resp:
        if resp.status != 200:
            raise RuntimeError(f"the local model answered HTTP {resp.status}")
        data = await resp.json(content_type=None)
    content = (((data or {}).get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    return concern_review.parse_verdict(content)


async def second_pass(session, event: dict[str, Any], found: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each candidate after the local model's look, or as the phrase list found it
    (with the reason) when the model cannot weigh in. Never raises, never drops."""
    try:
        settings = await _model_settings(session)
    except Exception as exc:
        reason = f"model settings could not be read: {exc}"
        log.warning("content monitoring: %s; using the phrase list", reason)
        return [concern_review.phrase_only(c, reason) for c in found]
    url, model = settings.get("llm_local_url") or "", settings.get("guardian_monitor_model") or ""
    if not url or not model:
        reason = "no local model configured (llm_local_url and guardian_monitor_model)"
        log.info("content monitoring: %s; using the phrase list", reason)
        return [concern_review.phrase_only(c, reason) for c in found]
    text = concern_monitor.event_text(event)
    out = []
    for concern in found:
        try:
            verdict = await _ask_model(session, url, model, concern, text)
            out.append(concern_review.apply(concern, verdict, model))
        except Exception as exc:
            reason = f"local model unavailable: {exc or type(exc).__name__}"
            log.warning("content monitoring: %s; using the phrase list for %s", reason, concern.get("category"))
            out.append(concern_review.phrase_only(concern, reason))
    return out


async def on_activity(rc, session, event: dict[str, Any], deliver: Deliver) -> list[dict[str, Any]]:
    """Scan one recorded event; keep and page what it finds. Never raises."""
    try:
        child = (await children(session)).get(str(event.get("user") or ""))
        if not child:
            return []
        found = concern_monitor.scan_event(event)
        if not found:
            return []
        found = await second_pass(session, event, found)
        await store.add_concerns(rc, child["username"], found)
        day = household_now().date().isoformat()
        for concern in found:
            if not concern_monitor.pages_parent(concern, child.get("monitoring")):
                continue
            for parent in child.get("parents") or []:
                try:
                    await deliver(_concern_notice(parent, child, concern, day))
                except Exception as exc:  # one parent's failure never blocks the other
                    log.warning("concern notice to %s failed: %s", parent, exc)
        return found
    except Exception as exc:
        log.warning("content monitoring skipped an event: %s", exc)
        return []


async def build_recap(rc, username: str, day: date) -> dict[str, Any]:
    start, end = guardian_recap.week_bounds(day)
    usage = await store.get_usage(rc, username, 21)
    events = await store.list_activity(rc, username, 500)
    concerns = await store.list_concerns(rc, username)
    return guardian_recap.build(usage, events, concerns, start, end, household_now().tzinfo.key)


async def send_weekly_recaps(rc, session, deliver: Deliver, now: datetime) -> int:
    """Sunday from 18:00: build, keep and send each child's recap once."""
    if now.weekday() != 6 or now.hour < RECAP_HOUR:
        return 0
    sent = 0
    week = guardian_recap.week_bounds(now.date())[0].isoformat()
    for child in (await children(session)).values():
        if not await rc.set(f"tel:recap_sent:{child['username']}:{week}", "1", nx=True, ex=8 * 86400):
            continue
        recap = await build_recap(rc, child["username"], now.date())
        await store.save_recap(rc, child["username"], week, recap)
        line = guardian_recap.summary_line(child.get("display_name") or child["username"], recap)
        for parent in child.get("parents") or []:
            try:
                await deliver({
                    "user": parent,
                    "category": "family_safety",
                    "kind": "recap",
                    "title": f"{child.get('display_name') or child['username']}'s week",
                    "body": line,
                    "tag": f"recap:{child['username']}:{week}",
                    "data": {"url": f"/guardian?child={child['username']}", "child": child["username"], "week": week},
                })
            except Exception as exc:
                log.warning("recap notice to %s failed: %s", parent, exc)
        sent += 1
    return sent


async def recap_loop(redis_factory, session_factory, deliver: Deliver) -> None:
    rc = redis_factory()
    while True:
        try:
            await send_weekly_recaps(rc, session_factory(), deliver, household_now())
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.error("weekly recap tick failed: %s", exc)
        await asyncio.sleep(RECAP_TICK_SECONDS)

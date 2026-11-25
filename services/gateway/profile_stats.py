"""Profile stats: one page per person across Jarvis, and the admin's view of it
(docs/PROFILE_STATS.md).

Two reads, each one fan-out to the services that already keep the numbers:

* ``GET /api/profile/stats/{username}`` (``me`` for the caller): the person's
  profile page. A person always sees all of their own. Anyone else in the
  family sees an area only when its owner shares it, and the switch that
  decides is the one that area already has: health's per-viewer shares,
  identity's activity sharing (activity, Wander, Bible), and identity's
  profile sharing for the areas with no switch of their own (garden,
  recipes, Jarvis use). Game scores are on the family scoreboard already, so
  they stay visible; the Bible daily quiz follows its leaderboard's rule.
* ``GET /api/profile/admin/{username}``: the same picture without the
  opt-in, plus the things that help an admin fix Jarvis and look after the
  family: devices and app versions, collector and permission health,
  features used, things asked for that Jarvis cannot do, assistant use by
  day (counts only), screen time, the Guardian's recap for a child, errors
  logged against the person, and trend flags. Admins only, checked here.

``GET``/``PUT /api/profile/sharing`` reads and changes the caller's own
choice, together with the state of the switches that already exist.

Every source is asked at once with its own time limit
(``PROFILE_STATS_SOURCE_TIMEOUT_S``); one that fails or is slow makes its
card ``unavailable`` and never fails the page. Answers are cached briefly
per (viewer, person) (``PROFILE_STATS_CACHE_S``, ``ADMIN_STATS_CACHE_S``);
``?fresh=1`` asks again. All three are read once at startup.

Never on any page: end-to-end encrypted content (the parents' private chat,
sealed secrets). Nothing here reads it, not even as counts.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from collections.abc import Awaitable, Callable
from datetime import date, timedelta
from typing import Any
from urllib.parse import quote

import aiohttp
from fastapi import APIRouter, Body, HTTPException, Request

from services.common.http import get_client
from services.gateway.config import INTERNAL_SECRET, TELEMETRY_SVC

log = logging.getLogger("gateway.profile_stats")

router = APIRouter()

SOURCE_TIMEOUT_S = float(os.getenv("PROFILE_STATS_SOURCE_TIMEOUT_S", "4"))
PROFILE_CACHE_S = float(os.getenv("PROFILE_STATS_CACHE_S", "60"))
ADMIN_CACHE_S = float(os.getenv("ADMIN_STATS_CACHE_S", "30"))

_USERNAME = re.compile(r"[a-z0-9_.-]{1,64}")
#: Areas whose only switch is identity's profile sharing (identity/profile_sharing.py).
PROFILE_SHARE_AREAS = ("garden", "recipes", "jarvis_use")
#: Every card of the profile page, in page order.
PROFILE_AREAS = ("bible", "activity", "health", "garden", "recipes", "games", "jarvis_use")

_cache: dict[tuple[str, str, str], tuple[float, dict]] = {}


class SourceError(Exception):
    """A source answered with something other than its data."""


# --------------------------------------------------------------------------- plumbing


def _svc(attr: str) -> str:
    from services.gateway import main as gateway

    return str(getattr(gateway, attr, "") or "").rstrip("/")


async def http_get(url: str, *, params: dict | None = None, headers: dict | None = None) -> Any:
    """One internal GET. Tests replace this function."""
    sent = {"X-Internal-Secret": INTERNAL_SECRET, **(headers or {})}
    clean = {k: v for k, v in (params or {}).items() if v is not None}
    async with get_client().get(url, params=clean, headers=sent,
                                timeout=aiohttp.ClientTimeout(total=SOURCE_TIMEOUT_S + 1)) as resp:
        if resp.status == 404:
            return None
        if resp.status != 200:
            raise SourceError(f"HTTP {resp.status}")
        return await resp.json(content_type=None)


async def _gather(sources: dict[str, Callable[[], Awaitable[Any]]]) -> dict[str, tuple[bool, Any]]:
    """Every source at once, each with its own limit: name -> (ok, value or reason)."""

    async def one(name: str, make: Callable[[], Awaitable[Any]]) -> tuple[str, tuple[bool, Any]]:
        try:
            return name, (True, await asyncio.wait_for(make(), timeout=SOURCE_TIMEOUT_S))
        except TimeoutError:
            log.warning("[profile-stats] %s timed out after %.1fs", name, SOURCE_TIMEOUT_S)
            return name, (False, "took too long to answer")
        except Exception as exc:  # one source never fails the page
            log.warning("[profile-stats] %s failed: %s", name, exc)
            return name, (False, "is not answering")

    pairs = await asyncio.gather(*(one(n, m) for n, m in sources.items()))
    return dict(pairs)


def _card(area: str, state: str, data: Any = None, reason: str | None = None) -> dict:
    out: dict[str, Any] = {"area": area, "state": state, "data": data}
    if reason:
        out["reason"] = reason
    return out


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _cached(key: tuple[str, str, str], ttl: float, fresh: bool) -> dict | None:
    hit = _cache.get(key)
    if hit and not fresh and hit[0] > time.monotonic():
        return hit[1]
    return None


def _store(key: tuple[str, str, str], ttl: float, value: dict) -> dict:
    if len(_cache) > 500:
        now = time.monotonic()
        for k in [k for k, (exp, _) in _cache.items() if exp <= now]:
            _cache.pop(k, None)
    _cache[key] = (time.monotonic() + ttl, value)
    return value


def clear_cache() -> None:
    _cache.clear()
    _roster_cache.clear()


# --------------------------------------------------------------------------- who


async def _viewer(request: Request) -> dict:
    from services.gateway import main as gateway

    ident = await gateway._acting_identity(request)
    if not ident:
        raise HTTPException(status_code=401, detail="Authentication required")
    return {"user": str(ident["user"]).strip().lower(), "is_admin": bool(ident.get("is_admin"))}


_roster_cache: list[Any] = []
ROSTER_CACHE_S = 300.0


async def _roster() -> dict[str, dict]:
    """Everyone in the household by username: their names (identity, internal),
    kept a few minutes: the household changes rarely and every page asks."""
    if _roster_cache and _roster_cache[0] > time.monotonic():
        return _roster_cache[1]
    rows = await http_get(f"{_svc('IDENTITY_SVC')}/api/internal/name-roster")
    people = {
        str(r.get("username") or "").lower(): {"username": str(r.get("username") or "").lower(),
                                                "display_name": r.get("display_name") or r.get("username")}
        for r in (rows or [])
        if isinstance(r, dict) and r.get("username")
    }
    _roster_cache[:] = [time.monotonic() + ROSTER_CACHE_S, people]
    return people


async def _subject(username: str, viewer: str) -> tuple[str, dict]:
    name = viewer if username in ("me", "") else username.strip().lower()
    if not _USERNAME.fullmatch(name):
        raise HTTPException(status_code=422, detail="A username is required")
    try:
        roster = await asyncio.wait_for(_roster(), timeout=SOURCE_TIMEOUT_S)
    except Exception as exc:
        log.warning("[profile-stats] identity roster unavailable: %s", exc)
        raise HTTPException(status_code=503, detail="The identity service is not answering; try again shortly") from exc
    person = roster.get(name)
    if person is None:
        raise HTTPException(status_code=404, detail="No one in the family has that name")
    return name, person


# --------------------------------------------------------------------------- the areas


def _bible_card(own: bool, stats: Any, summary: Any, feed: Any, board: Any, subject: str) -> dict:
    if own:
        if not isinstance(stats, dict):
            return _card("bible", "empty")
        metrics = stats.get("metrics") or {}
        position = stats.get("position") or {}
        data = {
            "streak_current": _int(stats.get("read_streak_current")),
            "streak_longest": _int(stats.get("read_streak_longest")),
            "days_read": _int(stats.get("days_read")),
            "chapters_read": _int(metrics.get("chapters_total")),
            "books_read": _int(metrics.get("books_read")),
            "last_read_at": stats.get("last_read_at"),
            "reference": position.get("reference") if isinstance(position, dict) else None,
            "window": [{"day": d, "count": _int(n)} for d, n in sorted((stats.get("window") or {}).items())],
            "achievements": [a.get("name") for a in ((summary or {}).get("achievements") or []) if isinstance(a, dict)],
            "points": _int((summary or {}).get("points")),
        }
    else:
        entry = next((e for e in ((feed or {}).get("entries") or [])
                      if isinstance(e, dict) and str(e.get("username") or "").lower() == subject), None)
        if entry is None:
            return _card("bible", "hidden")
        data = {
            "streak_current": _int(entry.get("read_streak_current")),
            "streak_longest": _int(entry.get("read_streak_longest")),
            "days_read": _int(entry.get("days_read")),
            "chapters_read": _int(entry.get("chapters_read")),
            "books_read": _int(entry.get("books_read")),
            "achievements": [a.get("name") for a in (entry.get("achievements") or []) if isinstance(a, dict)],
            "points": _int(entry.get("points")),
        }
    data["quiz_today"] = _quiz_entry(board, subject)
    return _card("bible", "ok", data)


def _quiz_entry(board: Any, subject: str) -> dict | None:
    for e in (board or {}).get("entries") or []:
        if isinstance(e, dict) and str(e.get("username") or "").lower() == subject:
            return {"correct": _int(e.get("correct")), "total": _int(e.get("total")),
                    "duration_ms": _int(e.get("duration_ms")), "day": (board or {}).get("day")}
    return None


_ACTIVITY_KEYS = ("steps_today", "steps_average", "steps_total", "workout_count", "workout_distance_miles",
                  "drive_distance_miles", "points", "achievements_earned")


def _activity_card(own: bool, summary: Any, feed: Any, subject: str) -> dict:
    if own:
        entry = summary if isinstance(summary, dict) else None
    else:
        entry = next((u for u in ((feed or {}).get("users") or [])
                      if isinstance(u, dict) and str(u.get("username") or "").lower() == subject), None)
    if entry is None:
        return _card("activity", "empty" if own else "hidden")
    return _card("activity", "ok", {k: entry.get(k) for k in _ACTIVITY_KEYS if k in entry} | {"window": entry.get("window", "week")})


def _health_card(own: bool, streaks: Any, weekly: Any, family: Any, subject: str) -> dict:
    if own:
        habits = [
            {"key": h.get("key"), "title": h.get("title"), "current": _int(h.get("current")), "best": _int(h.get("best")),
             "today_done": bool(h.get("today_done"))}
            for h in ((streaks or {}).get("habits") or []) if isinstance(h, dict)
        ]
        week = weekly if isinstance(weekly, dict) else {}
        data = {
            "habits": habits,
            "week": {
                "days_logged": _int(week.get("days_logged")),
                "averages": week.get("averages") or {},
                "changes": week.get("changes") or [],
                "best_sleep": week.get("best_sleep"),
            },
        }
        if not habits and not data["week"]["days_logged"]:
            return _card("health", "empty", data)
        return _card("health", "ok", data)
    entry = next((p for p in ((family or {}).get("people") or [])
                  if isinstance(p, dict) and str(p.get("user_id") or "").lower() == subject), None)
    if entry is None:
        return _card("health", "hidden")
    shared = {k: entry[k] for k in ("sleep", "nutrition", "fasting") if k in entry}
    return _card("health", "ok", {"scopes": entry.get("scopes") or [], **shared})


def _garden_card(streaks: Any) -> dict:
    s = streaks if isinstance(streaks, dict) else {}
    earned = [a.get("name") for a in (s.get("earned") or []) if isinstance(a, dict) and a.get("name")]
    data = {
        "streak_current": _int(s.get("current_streak")),
        "streak_longest": _int(s.get("longest_streak")),
        "care_logs": _int(s.get("total_logs")),
        "achievements": earned,
        "points": _int(s.get("points")),
    }
    return _card("garden", "ok" if data["care_logs"] or earned else "empty", data)


def _recipe_title(recipe_id: str) -> str:
    base = recipe_id.rsplit("/", 1)[-1]
    base = re.sub(r"\.(md|markdown|json|txt)$", "", base, flags=re.I)
    return re.sub(r"[-_]+", " ", base).strip().title() or recipe_id


def _recipes_card(stats: Any) -> dict:
    recipes = (stats or {}).get("recipes") or {}
    made, rated = [], []
    for rid, s in recipes.items():
        if not isinstance(s, dict):
            continue
        item = {"id": rid, "title": _recipe_title(str(rid)), "made_by_me": _int(s.get("made_by_me")),
                "mine": s.get("mine"), "rating": s.get("rating"), "last_made": s.get("last_made")}
        if item["made_by_me"]:
            made.append(item)
        if item["mine"]:
            rated.append(item)
    made.sort(key=lambda r: (-r["made_by_me"], r["title"]))
    rated.sort(key=lambda r: (-_int(r["mine"]), r["title"]))
    data = {
        "times_cooked": sum(r["made_by_me"] for r in made),
        "recipes_cooked": len(made),
        "ratings_given": len(rated),
        "most_made": made[:5],
        "favourites": [r for r in rated if _int(r["mine"]) >= 5][:5],
    }
    return _card("recipes", "ok" if made or rated else "empty", data)


def _games_card(arcade: Any, family: Any, board: Any, subject: str) -> dict:
    games: list[dict] = []
    totals = {"best_score": 0, "crowns": 0, "podiums": 0, "total_games": 0}
    for collection, card in (("family", family), ("arcade", arcade)):
        player = (card or {}).get("player") or card or {}
        if not isinstance(player, dict):
            continue
        for key in totals:
            totals[key] = max(totals[key], _int(player.get(key))) if key == "best_score" else totals[key] + _int(player.get(key))
        for g in player.get("per_game") or []:
            if not isinstance(g, dict) or not g.get("slug") or g.get("my_best") is None:
                continue
            games.append({
                "slug": str(g["slug"]), "title": str(g.get("title") or g["slug"]), "collection": collection,
                "best": _int(g.get("my_best")), "rank": g.get("my_rank"), "plays": _int(g.get("my_plays")),
                "top_score": g.get("top_score"), "top_initials": g.get("top_initials"),
            })
    games.sort(key=lambda g: (g["rank"] if isinstance(g["rank"], int) else 999, -g["best"]))
    quiz = _quiz_entry(board, subject)
    data = {**totals, "games": games, "bible_quiz_today": quiz}
    if not games and quiz is None:
        return _card("games", "empty", data)
    return _card("games", "ok", data)


def _use_card(use: Any) -> dict:
    u = (use or {}).get("use") or {}
    if not u.get("has_data"):
        return _card("jarvis_use", "empty", {"days": u.get("days"), "total": 0, "top": []})
    return _card("jarvis_use", "ok", {"days": u.get("days"), "total": _int(u.get("total")), "top": u.get("top") or [],
                                       "window_start": u.get("window_start")})


# --------------------------------------------------------------------------- the profile page


def _health_headers(user: str) -> dict:
    # Kid mode is the health service's own business; "unknown" keeps a minor safe.
    return {"X-User-Id": user, "X-User-Is-Admin": "false", "X-User-Is-Minor": "unknown"}


async def _arcade_player(username: str, collection: str) -> Any:
    from services.gateway import main as gateway

    initials = gateway._arcade_initials(username)
    return await http_get(f"{gateway._arcade_url()}/api/players/{initials}",
                          params={"player_id": username, "collection": collection})


async def build_profile(viewer: str, subject: str) -> dict:
    """Every card of ``subject``'s profile as ``viewer`` may see it."""
    own = viewer == subject
    identity, bible, geo, health, garden = (_svc(a) for a in ("IDENTITY_SVC", "BIBLE_SVC", "GEO_SVC", "HEALTH_SVC", "GARDEN_SVC"))
    sources: dict[str, Callable[[], Awaitable[Any]]] = {
        "quiz": lambda: http_get(f"{bible}/games/leaderboard", params={"viewer": viewer}),
        "arcade": lambda: _arcade_player(subject, "arcade"),
        "family_games": lambda: _arcade_player(subject, "family"),
    }
    if own:
        sources |= {
            "bible_stats": lambda: http_get(f"{bible}/stats", params={"username": subject, "days": 30}),
            "bible_summary": lambda: http_get(f"{bible}/activity/summary", params={"username": subject, "target": subject}),
            "activity": lambda: http_get(f"{geo}/activity/summary", params={"user_id": subject, "viewer": subject, "window": "week"}),
            "health_streaks": lambda: http_get(f"{health}/streaks", headers=_health_headers(subject)),
            "health_weekly": lambda: http_get(f"{health}/weekly", headers=_health_headers(subject)),
            "garden": lambda: http_get(f"{garden}/streaks", headers={"X-User-Id": subject}),
            "recipes": lambda: http_get(f"{health}/recipes/stats", headers=_health_headers(subject)),
            "use": lambda: http_get(f"{TELEMETRY_SVC}/api/telemetry/use/{subject}", params={"days": 30}),
            "my_sharing": lambda: sharing_state(subject),
        }
    else:
        sources |= {
            "bible_feed": lambda: http_get(f"{bible}/activity/feed", params={"viewer": viewer, "limit": 100}),
            "activity_feed": lambda: http_get(f"{geo}/activity/feed", params={"viewer": viewer, "window": "week"}),
            "health_family": lambda: http_get(f"{health}/family", headers=_health_headers(viewer)),
            "profile_sharing": lambda: http_get(f"{identity}/api/internal/profile-sharing", params={"username": subject}),
        }
    got = await _gather(sources)

    def value(name: str) -> Any:
        ok, v = got.get(name, (False, None))
        return v if ok else None

    def failed(*names: str) -> str | None:
        for n in names:
            ok, v = got.get(n, (True, None))
            if not ok:
                return str(v)
        return None

    cards: list[dict] = []
    shared_areas: set[str] = set(PROFILE_SHARE_AREAS) if own else set()
    sharing_unknown = (not own) and failed("profile_sharing") is not None
    if not own and not sharing_unknown:
        shared_areas = set(((value("profile_sharing") or {}).get("areas")) or [])

    def add(area: str, sources_used: tuple[str, ...], build: Callable[[], dict]) -> None:
        reason = failed(*sources_used)
        if reason:
            cards.append(_card(area, "unavailable", reason=f"This part of Jarvis {reason}."))
        else:
            cards.append(build())

    if own:
        add("bible", ("bible_stats",), lambda: _bible_card(True, value("bible_stats"), value("bible_summary"), None, value("quiz"), subject))
        add("activity", ("activity",), lambda: _activity_card(True, value("activity"), None, subject))
        add("health", ("health_streaks", "health_weekly"), lambda: _health_card(True, value("health_streaks"), value("health_weekly"), None, subject))
    else:
        add("bible", ("bible_feed",), lambda: _bible_card(False, None, None, value("bible_feed"), value("quiz"), subject))
        add("activity", ("activity_feed",), lambda: _activity_card(False, None, value("activity_feed"), subject))
        add("health", ("health_family",), lambda: _health_card(False, None, None, value("health_family"), subject))
    # Areas another person shares are only fetched once we know they share them.
    later: dict[str, Callable[[], Awaitable[Any]]] = {}
    shapers: dict[str, Callable[[Any], dict]] = {"garden": _garden_card, "recipes": _recipes_card, "jarvis_use": _use_card}
    own_sources = {"garden": "garden", "recipes": "recipes", "jarvis_use": "use"}
    for area in ("garden", "recipes", "jarvis_use"):
        if sharing_unknown:
            cards.append(_card(area, "unavailable", reason="What is shared could not be checked just now."))
        elif area not in shared_areas:
            cards.append(_card(area, "hidden"))
        elif own:
            add(area, (own_sources[area],), lambda a=area: shapers[a](value(own_sources[a])))
        else:
            later[area] = lambda a=area: _fetch_shared(a, subject)
    # The arcade is a separate machine; while it is off the Bible quiz still shows.
    games = _games_card(value("arcade"), value("family_games"), value("quiz"), subject)
    if failed("arcade") and failed("family_games"):
        if games["state"] == "empty":
            games = _card("games", "unavailable", reason=f"The arcade {failed('arcade')}.")
        else:
            games["data"]["arcade_unavailable"] = True
    cards.append(games)
    if later:
        for area, (ok, v) in (await _gather(later)).items():
            cards.append(shapers[area](v) if ok else _card(area, "unavailable", reason=f"This part of Jarvis {v}."))
    order = {a: i for i, a in enumerate(PROFILE_AREAS)}
    cards.sort(key=lambda c: order.get(c["area"], 99))
    out: dict[str, Any] = {"username": subject, "own": own, "cards": cards}
    if own:
        ok, state = got.get("my_sharing", (False, None))
        out["sharing"] = state if ok else None
    return out


async def _fetch_shared(area: str, subject: str) -> Any:
    if area == "garden":
        return await http_get(f"{_svc('GARDEN_SVC')}/streaks", headers={"X-User-Id": subject})
    if area == "recipes":
        return await http_get(f"{_svc('HEALTH_SVC')}/recipes/stats", headers=_health_headers(subject))
    return await http_get(f"{TELEMETRY_SVC}/api/telemetry/use/{subject}", params={"days": 30})


async def sharing_state(username: str) -> dict:
    """The person's own choice plus the switches other areas already have."""
    identity, health = _svc("IDENTITY_SVC"), _svc("HEALTH_SVC")
    got = await _gather({
        "profile": lambda: http_get(f"{identity}/api/internal/profile-sharing", params={"username": username}),
        "activity": lambda: http_get(f"{identity}/api/internal/activity-sharing"),
        "health": lambda: http_get(f"{health}/shares", headers=_health_headers(username)),
    })
    profile_ok, profile = got["profile"]
    activity_ok, activity = got["activity"]
    health_ok, health_shares = got["health"]
    row = next((u for u in ((activity or {}).get("users") or []) if isinstance(u, dict)
                and str(u.get("username") or "").lower() == username), None) if activity_ok else None
    return {
        "areas": (profile or {}).get("areas", []) if profile_ok else None,
        "available": list(PROFILE_SHARE_AREAS),
        # The switches that already exist, so the page shows them instead of a second one.
        "activity_sharing": None if not activity_ok else {
            "enabled": bool(row and row.get("enabled")),
            "audience": (row or {}).get("audience", "circle"),
            "share": (row or {}).get("share", []) if row else [],
        },
        "health_shares": None if not health_ok else [
            {"viewer": s.get("viewer"), "scope": s.get("scope")} for s in ((health_shares or {}).get("shares") or [])
            if isinstance(s, dict)
        ],
    }


# --------------------------------------------------------------------------- the admin page


#: Share of a person's requests that may fail before the admin page flags it.
VOICE_FAILURE_FLAG = float(os.getenv("PROFILE_STATS_VOICE_FAILURE_FLAG", "0.1"))


def _flags(profile_cards: dict[str, dict], usage: Any, screen: Any, voice: Any = None) -> list[dict]:
    """Plain trend signals for the admin: what changed, said once."""
    flags: list[dict] = []
    if isinstance(voice, dict) and _int(voice.get("total")) >= 10:
        outcomes = voice.get("outcomes") or {}
        failed = sum(_int(outcomes.get(k)) for k in ("timeout", "error", "tool_failure", "offline"))
        total = _int(voice.get("total"))
        if failed / total >= VOICE_FAILURE_FLAG:
            worst = (voice.get("tools") or [{}])[0].get("tool")
            flags.append({"area": "jarvis", "level": "fix",
                          "text": f"{round(failed * 100 / total)}% of requests to Jarvis did not go through"
                                  + (f" (most often {worst})." if worst else ".")})
    bible = (profile_cards.get("bible") or {}).get("data") or {}
    if bible and not bible.get("streak_current") and _int(bible.get("streak_longest")) >= 3:
        flags.append({"area": "bible", "level": "watch",
                      "text": f"Bible reading streak has stopped (best was {bible['streak_longest']} days)."})
    window = bible.get("window") or []
    if window:
        recent = sum(_int(d.get("count")) for d in window[-7:])
        before = sum(_int(d.get("count")) for d in window[-14:-7])
        if before >= 3 and recent < before / 2:
            flags.append({"area": "bible", "level": "watch", "text": f"Bible reading fell from {before} to {recent} this week."})
    health = (profile_cards.get("health") or {}).get("data") or {}
    for change in ((health.get("week") or {}).get("changes") or []):
        if isinstance(change, dict) and change.get("metric") in ("sleep_minutes", "sleep_score", "mood", "energy") \
                and change.get("direction") == "down":
            flags.append({"area": "health", "level": "watch",
                          "text": f"{str(change['metric']).replace('_', ' ').capitalize()} is down on last week."})
    days = [d for d in ((screen or {}).get("days") or []) if isinstance(d, dict)]
    if len(days) >= 8:
        days.sort(key=lambda d: str(d.get("day")))
        this = sum(_int(d.get("millis")) for d in days[-7:]) / 7
        last = sum(_int(d.get("millis")) for d in days[-14:-7]) / max(1, len(days[-14:-7]))
        if last and this > last * 1.3 and this - last > 30 * 60000:
            flags.append({"area": "screen", "level": "watch",
                          "text": f"Screen time is up {round((this - last) / 60000)} min a day on last week."})
    gaps = [g for g in ((usage or {}).get("capability_gaps") or []) if isinstance(g, dict) and not g.get("resolved")]
    if gaps:
        flags.append({"area": "jarvis", "level": "fix",
                      "text": f"{len(gaps)} thing{'s' if len(gaps) != 1 else ''} asked for that Jarvis cannot do yet."})
    return flags


def _collector(health: Any) -> dict:
    devices = [d for d in ((health or {}).get("devices") or []) if isinstance(d, dict)]
    return {
        "devices": [{"device_name": d.get("device_name"), "app_version": d.get("app_version"),
                     "reported_at": d.get("reported_at") or d.get("received_at"),
                     "problems": d.get("problems") or []} for d in devices],
        "staleness": (health or {}).get("staleness") or {},
        "problem_count": sum(len(d.get("problems") or []) for d in devices),
    }


def _screen(usage: Any) -> dict:
    days = sorted((d for d in ((usage or {}).get("days") or []) if isinstance(d, dict)), key=lambda d: str(d.get("day")))
    return {"days": [{"day": d.get("day"), "minutes": round(_int(d.get("millis")) / 60000),
                      "sessions": _int(d.get("sessions"))} for d in days]}


def _guardian(recap: Any, concerns: Any) -> dict:
    items = [c for c in ((concerns or {}).get("concerns") or []) if isinstance(c, dict)]
    levels: dict[str, int] = {}
    for c in items:
        lvl = str(c.get("level") or c.get("severity") or "info")
        levels[lvl] = levels.get(lvl, 0) + 1
    r = (recap or {}).get("recap") or {}
    return {
        "concerns": len(items),
        "levels": levels,
        "recap_week": r.get("week") or r.get("week_start"),
        "recap_headline": r.get("headline") or r.get("summary"),
    }


def _logs(logs: Any) -> dict:
    entries = logs if isinstance(logs, list) else ((logs or {}).get("logs") or (logs or {}).get("entries") or [])
    by_level: dict[str, int] = {}
    recent: list[dict] = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        level = str(e.get("level") or "INFO").upper()
        by_level[level] = by_level.get(level, 0) + 1
        if level in ("ERROR", "CRITICAL", "WARNING") and len(recent) < 10:
            recent.append({"level": level, "service": e.get("service"), "message": str(e.get("message") or "")[:240],
                           "timestamp": e.get("timestamp")})
    return {"by_level": by_level, "recent": recent}


async def build_admin(subject: str) -> dict:
    identity = _svc("IDENTITY_SVC")
    logging_svc = _svc("LOGGING_SVC")
    since = (date.today() - timedelta(days=30)).isoformat()
    profile_task = asyncio.create_task(build_profile(subject, subject))
    got = await _gather({
        "usage": lambda: http_get(f"{identity}/api/internal/people/{quote(subject)}/usage", params={"days": 30}),
        "collector": lambda: http_get(f"{TELEMETRY_SVC}/api/telemetry/device_health/{subject}"),
        "features": lambda: http_get(f"{TELEMETRY_SVC}/api/telemetry/activity/{subject}/counts", params={"since": since}),
        "screen": lambda: http_get(f"{TELEMETRY_SVC}/api/telemetry/usage/{subject}", params={"days": 14}),
        "recap": lambda: http_get(f"{TELEMETRY_SVC}/api/telemetry/recap/{subject}"),
        "concerns": lambda: http_get(f"{TELEMETRY_SVC}/api/telemetry/concerns/{subject}", params={"limit": 100}),
        "logs": lambda: http_get(f"{logging_svc}/api/logs", params={"user_id": subject, "limit": 300}),
        "voice": lambda: voice_outcomes(subject),
    })
    profile = await profile_task

    def section(name: str, shape: Callable[[Any], Any]) -> dict:
        ok, v = got[name]
        if not ok:
            return {"state": "unavailable", "reason": f"This part of Jarvis {v}."}
        return {"state": "ok", "data": shape(v)}

    by_area = {c["area"]: c for c in profile["cards"]}
    usage_ok, usage = got["usage"]
    screen_ok, screen = got["screen"]
    return {
        "username": subject,
        "profile": profile,
        "flags": _flags(by_area, usage if usage_ok else None, screen if screen_ok else None,
                        got["voice"][1] if got["voice"][0] else None),
        "sections": {
            "usage": section("usage", lambda v: {k: v.get(k) for k in ("devices", "features", "capability_gaps", "conversations",
                                                                        "events", "app_opens_per_day")} if isinstance(v, dict) else {}),
            "collector": section("collector", _collector),
            "features": section("features", lambda v: {"since": since, "counts": (v or {}).get("counts") or {}}),
            "screen": section("screen", _screen),
            "guardian": {"state": "ok", "data": _guardian(got["recap"][1] if got["recap"][0] else None,
                                                          got["concerns"][1] if got["concerns"][0] else None)}
            if got["recap"][0] or got["concerns"][0] else {"state": "unavailable", "reason": f"The Guardian {got['recap'][1]}."},
            "logs": section("logs", _logs),
            "voice": section("voice", lambda v: v),
        },
    }


async def voice_outcomes(subject: str) -> dict | None:
    """How the person's assistant requests went (services/gateway/voice_outcomes.py,
    when present). None means nothing is recorded for them yet."""
    try:
        from services.gateway import voice_outcomes as outcomes
    except ImportError:
        return None
    return await outcomes.summary(subject)


# --------------------------------------------------------------------------- routes


@router.get("/api/profile/stats/{username}")
async def profile_stats(request: Request, username: str, fresh: bool = False):
    me = await _viewer(request)
    subject, person = await _subject(username, me["user"])
    key = ("profile", me["user"], subject)
    hit = _cached(key, PROFILE_CACHE_S, fresh)
    if hit is not None:
        return hit
    body = await build_profile(me["user"], subject)
    body["person"] = person
    return _store(key, PROFILE_CACHE_S, body)


@router.get("/api/profile/admin/{username}")
async def admin_person_stats(request: Request, username: str, fresh: bool = False):
    me = await _viewer(request)
    if not me["is_admin"]:
        raise HTTPException(status_code=403, detail="Admin only")
    subject, person = await _subject(username, me["user"])
    key = ("admin", "", subject)
    hit = _cached(key, ADMIN_CACHE_S, fresh)
    if hit is not None:
        return hit
    body = await build_admin(subject)
    body["person"] = person
    return _store(key, ADMIN_CACHE_S, body)


@router.get("/api/profile/sharing")
async def get_sharing(request: Request):
    me = await _viewer(request)
    return await sharing_state(me["user"])


@router.put("/api/profile/sharing")
async def put_sharing(request: Request, body: dict = Body(...)):
    """Change which of the switch-less areas the family may see. The caller's own
    key goes to identity, so identity decides whose choice it is."""
    me = await _viewer(request)
    auth = request.headers.get("Authorization") or ""
    if not auth:
        raise HTTPException(status_code=401, detail="Authentication required")
    async with get_client().put(
        f"{_svc('IDENTITY_SVC')}/api/users/me/profile-sharing",
        json={"areas": body.get("areas")}, headers={"Authorization": auth},
        timeout=aiohttp.ClientTimeout(total=10),
    ) as resp:
        answer = await resp.json(content_type=None)
        if resp.status != 200:
            raise HTTPException(status_code=resp.status, detail=(answer or {}).get("detail") or "Could not save")
    for k in [k for k in _cache if k[2] == me["user"]]:
        _cache.pop(k, None)
    return await sharing_state(me["user"])

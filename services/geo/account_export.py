"""The person's places, trips, workouts and steps for the account export (docs/ACCOUNT_EXPORT.md).

Geo keeps everything in Redis, keyed by the person's lowercased name. These
routes sit under ``/internal/account`` behind the service's internal-secret
middleware:

* ``GET  /internal/account/export/{user}``: everything but the location trail.
* ``GET  /internal/account/export/{user}/location-history?start=&count=``: the
  trail (up to 30 days of fixes), one page at a time, oldest first, so the
  gateway never holds it whole.
* ``POST /internal/account/import/{user}``: merge places, reminders, trips,
  workouts, daily steps, goals and earned achievements back.

Only saved places the person owns leave (the household's shared places belong
to whoever made them). A restored trip is never shared with anyone: the riders
named on it are dropped, since on another server those names can be other
people. The location trail, visits, driving events and stars are exported for
reading and never restored (they are history the server derives).
"""
from __future__ import annotations

import json
import re
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query

_USER = re.compile(r"^[a-z0-9_.-]{1,64}$")
_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")
PLACES_KEY = "geo:places"
MAX_PAGE = 5000
#: Fields that tie a trip to other people; a restored trip drops them.
_TRIP_SHARE_FIELDS = ("is_shared", "shared_with", "shared_group_id", "shared_manually")
MAX_IMPORT_ITEMS = 50_000


def _text(value: Any) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


def _load(raw: Any) -> dict[str, Any] | None:
    if raw is None:
        return None
    try:
        value = json.loads(_text(raw))
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _user(user: str) -> str:
    clean = (user or "").strip().lower().split(".")[-1]
    if not _USER.match(clean):
        raise HTTPException(status_code=422, detail="user is required")
    return clean


async def _records(r: Any, index_key: str, record_key: str) -> list[dict[str, Any]]:
    out = []
    for item_id in await r.zrange(index_key, 0, -1):
        record = _load(await r.get(record_key.format(id=_text(item_id))))
        if record is not None:
            out.append(record)
    return out


async def _hash_rows(r: Any, key: str, field: str, value: str) -> list[dict[str, Any]]:
    stored = await r.hgetall(key) or {}
    return [{field: _text(k), value: _text(v)} for k, v in sorted(stored.items(), key=lambda kv: _text(kv[0]))]


async def export_user(r: Any, user: str) -> dict[str, list[dict[str, Any]]]:
    places = [p for p in (_load(v) for v in (await r.hgetall(PLACES_KEY) or {}).values()) if p and p.get("owner") == user]
    reminders = [x for x in (_load(v) for v in (await r.hgetall(f"geo:place_reminders:{user}") or {}).values()) if x]
    goal = await r.get(f"geo:steps_goal:{user}")
    return {
        "places": places,
        "place_reminders": reminders,
        "trips": await _records(r, f"geo:trips:user:{user}", "geo:trip:{id}"),
        "workouts": await _records(r, f"geo:workouts:user:{user}", "geo:workout:{id}"),
        "daily_steps": await _hash_rows(r, f"geo:steps:{user}", "day", "steps"),
        "goals": await _hash_rows(r, f"geo:goals:{user}", "goal", "value")
        + ([{"goal": "daily_steps_goal", "value": _text(goal)}] if goal is not None else []),
        "achievements": await _hash_rows(r, f"geo:points:{user}", "achievement_id", "earned_on"),
        "visits": await _hash_rows(r, f"geo:visits:{user}", "spot", "stays"),
    }


async def history_page(r: Any, user: str, start: int, count: int) -> dict[str, Any]:
    key = f"geo:history:{user}"
    total = await r.zcard(key)
    raw = await r.zrange(key, start, start + count - 1)
    points = [p for p in (_load(x) for x in raw) if p]
    following = start + len(raw)
    return {"points": points, "total": total, "next": following if following < total else None}


class _Counts:
    def __init__(self) -> None:
        self.add = self.update = self.conflict = self.unchanged = self.skipped = 0

    def as_dict(self) -> dict[str, Any]:
        return {"add": self.add, "update": self.update, "conflict": self.conflict,
                "unchanged": self.unchanged, "skipped": self.skipped, "errors": []}


def _newer(theirs: dict[str, Any], mine: dict[str, Any]) -> bool:
    try:
        return float(theirs.get("updated_at") or 0) > float(mine.get("updated_at") or 0) > 0
    except (TypeError, ValueError):
        return False


async def _merge_records(
    r: Any, user: str, rows: list[Any], counts: _Counts, *, index: str, record: str, everyone: str,
    clean: Callable[[dict[str, Any]], dict[str, Any]], write: bool, replace: bool,
) -> None:
    mine = {_text(i) for i in await r.zrange(index, 0, -1)}
    for row in rows:
        if not isinstance(row, dict) or not _ID.match(str(row.get("id") or "")):
            counts.skipped += 1
            continue
        item = clean({**row, "user_id": user})
        item_id = str(item["id"])
        current = _load(await r.get(record.format(id=item_id))) if item_id in mine else None
        if current is None:
            if item_id not in mine and await r.get(record.format(id=item_id)) is not None:
                item["id"] = item_id = uuid.uuid4().hex  # the id is someone else's here
            counts.add += 1
            if write:
                await r.set(record.format(id=item_id), json.dumps(item))
                score = float(item.get("start_time") or 0)
                await r.zadd(index, {item_id: score})
                await r.zadd(everyone, {item_id: score})
            continue
        if {k: v for k, v in current.items() if k not in _TRIP_SHARE_FIELDS} == {k: v for k, v in item.items() if k not in _TRIP_SHARE_FIELDS}:
            counts.unchanged += 1
            continue
        if _newer(item, current):
            counts.update += 1
        else:
            counts.conflict += 1
            if not replace:
                continue
        if write:
            # Keep the server's sharing as it is; the file never changes who a trip is shared with.
            kept = {k: current[k] for k in _TRIP_SHARE_FIELDS if k in current}
            await r.set(record.format(id=item_id), json.dumps({**{k: v for k, v in item.items() if k not in _TRIP_SHARE_FIELDS}, **kept}))


def _drop_sharing(trip: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in trip.items() if k not in _TRIP_SHARE_FIELDS}


async def _merge_hash(
    r: Any, key: str, rows: list[Any], field: str, value: str, counts: _Counts, *, write: bool, replace: bool,
) -> None:
    stored = {_text(k): _text(v) for k, v in (await r.hgetall(key) or {}).items()}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get(field), str) or row.get(value) is None:
            counts.skipped += 1
            continue
        name, incoming = row[field], str(row[value])
        if name not in stored:
            counts.add += 1
        elif stored[name] == incoming:
            counts.unchanged += 1
            continue
        else:
            counts.conflict += 1
            if not replace:
                continue
        if write:
            await r.hset(key, name, incoming)


async def import_user(r: Any, user: str, collections: dict[str, Any], *, dry_run: bool, replace: bool) -> dict[str, Any]:
    write = not dry_run
    results: dict[str, Any] = {}

    def rows(name: str) -> list[Any]:
        value = collections.get(name) or []
        return value[:MAX_IMPORT_ITEMS] if isinstance(value, list) else []

    # Saved places: only the person's own, kept as theirs.
    counts = _Counts()
    every = {k: _load(v) for k, v in ((_text(k), v) for k, v in (await r.hgetall(PLACES_KEY) or {}).items())}
    for row in rows("places"):
        if not isinstance(row, dict) or not _ID.match(str(row.get("id") or "")):
            counts.skipped += 1
            continue
        place = {**row, "owner": user}
        current = every.get(place["id"])
        if current is not None and current.get("owner") != user:
            place["id"] = uuid.uuid4().hex
            current = None
        if current is None:
            counts.add += 1
        elif current == place:
            counts.unchanged += 1
            continue
        else:
            counts.conflict += 1
            if not replace:
                continue
        if write:
            await r.hset(PLACES_KEY, place["id"], json.dumps(place))
    results["places"] = counts.as_dict()

    counts = _Counts()
    key = f"geo:place_reminders:{user}"
    stored = {_text(k): _load(v) for k, v in (await r.hgetall(key) or {}).items()}
    for row in rows("place_reminders"):
        if not isinstance(row, dict) or not _ID.match(str(row.get("id") or "")):
            counts.skipped += 1
            continue
        current = stored.get(row["id"])
        if current is None:
            counts.add += 1
        elif current == row:
            counts.unchanged += 1
            continue
        else:
            counts.conflict += 1
            if not replace:
                continue
        if write:
            await r.hset(key, row["id"], json.dumps(row))
    results["place_reminders"] = counts.as_dict()

    for name, index, record, everyone in (
        ("trips", f"geo:trips:user:{user}", "geo:trip:{id}", "geo:trips:all"),
        ("workouts", f"geo:workouts:user:{user}", "geo:workout:{id}", "geo:workouts:all"),
    ):
        counts = _Counts()
        await _merge_records(r, user, rows(name), counts, index=index, record=record, everyone=everyone,
                             clean=_drop_sharing, write=write, replace=replace)
        results[name] = counts.as_dict()

    counts = _Counts()
    await _merge_hash(r, f"geo:steps:{user}", rows("daily_steps"), "day", "steps", counts, write=write, replace=replace)
    results["daily_steps"] = counts.as_dict()

    counts = _Counts()
    goal_rows = [g for g in rows("goals") if isinstance(g, dict) and g.get("goal") != "daily_steps_goal"]
    await _merge_hash(r, f"geo:goals:{user}", goal_rows, "goal", "value", counts, write=write, replace=replace)
    step_goal = next((g for g in rows("goals") if isinstance(g, dict) and g.get("goal") == "daily_steps_goal"), None)
    if step_goal is not None and str(step_goal.get("value") or "").isdigit():
        current = await r.get(f"geo:steps_goal:{user}")
        if current is None:
            counts.add += 1
            if write:
                await r.set(f"geo:steps_goal:{user}", str(step_goal["value"]))
        elif _text(current) == str(step_goal["value"]):
            counts.unchanged += 1
        else:
            counts.conflict += 1
            if write and replace:
                await r.set(f"geo:steps_goal:{user}", str(step_goal["value"]))
    results["goals"] = counts.as_dict()

    counts = _Counts()
    await _merge_hash(r, f"geo:points:{user}", rows("achievements"), "achievement_id", "earned_on", counts, write=write, replace=False)
    results["achievements"] = counts.as_dict()

    for name in ("visits", "location_history"):
        counts = _Counts()
        counts.skipped = len(rows(name))
        results[name] = counts.as_dict()
    return results


def build_router(get_redis: Callable[[], Awaitable[Any]]) -> APIRouter:
    router = APIRouter(prefix="/internal/account")

    async def _r() -> Any:
        r = await get_redis()
        if not r:
            raise HTTPException(status_code=503, detail="Redis unavailable")
        return r

    @router.get("/export/{user}")
    async def export_account(user: str) -> dict[str, Any]:
        return {"service": "geo", "collections": await export_user(await _r(), _user(user))}

    @router.get("/export/{user}/location-history")
    async def export_history(user: str, start: int = Query(0, ge=0), count: int = Query(MAX_PAGE, ge=1, le=MAX_PAGE)) -> dict[str, Any]:
        return await history_page(await _r(), _user(user), start, count)

    @router.post("/import/{user}")
    async def import_account(user: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        collections = body.get("collections")
        if not isinstance(collections, dict):
            raise HTTPException(status_code=422, detail="collections must be an object")
        dry_run = bool(body.get("dry_run", True))
        results = await import_user(await _r(), _user(user), collections, dry_run=dry_run,
                                    replace=bool(body.get("replace_conflicts", False)))
        return {"service": "geo", "dry_run": dry_run, "collections": results}

    return router

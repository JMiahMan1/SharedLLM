"""Home notices from Home Assistant: doorbell, motion while away, garage left
open, a door unlocked at night, washer or dryer done, water leak.

Which entities count, and who hears about each, is the admin's call, never a
guess from entity names: the ``home_alert_entities`` setting (Admin > Users >
Home alerts) is a JSON list of

    {"entity_id": "binary_sensor.front_doorbell", "event": "doorbell",
     "people": [], "minutes": 15, "watts": 10}

``people`` empty means everyone; ``minutes`` and ``watts`` tune the events
that use them (see ``DEFAULT_MINUTES``). Every few seconds the loop reads all
states from Home Assistant with the system account (the same ``/api/states``
call every other reader uses; there is no event subscription here) and
compares each mapped entity with the previous round. "Nobody home" is the
presence tracker's answer (ESPresense rooms, else phone GPS against Home
Assistant's home location). Notices go out with category ``home`` through
telemetry; a water leak is priority 5.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timedelta

import aiohttp

from services.execution.leave_by import _clock

log = logging.getLogger("execution.home_alerts")

INTERVAL_S = 15
SETTING = "home_alert_entities"
#: The mapping is re-read this often, so an edit applies without a restart.
SETTING_TTL_S = 60
EVENTS = ("doorbell", "motion_away", "garage_open", "door_unlocked_night", "appliance_done", "water_leak")
#: garage_open: open this long; door_unlocked_night: unlocked this long at
#: night; appliance_done: power low this long after running.
DEFAULT_MINUTES = {"garage_open": 15, "door_unlocked_night": 5, "appliance_done": 5}
#: appliance_done: above this many watts counts as running.
DEFAULT_WATTS = 10.0
#: appliance_done: a run shorter than this (a standby blip) is not a cycle.
MIN_RUN_S = 5 * 60
#: motion_away: at most one notice per sensor per this many seconds.
MOTION_QUIET_S = 600
#: Night, for door_unlocked_night, in the household's time zone.
NIGHT_START_HOUR = 22
NIGHT_END_HOUR = 6
_UNKNOWN = {"unavailable", "unknown", "", None}
_RUNNING_STATES = {"on", "running", "washing", "drying", "spinning", "rinsing"}


def parse_mapping(raw: str | None) -> list[dict]:
    """The setting as clean entries; malformed ones are dropped and logged."""
    try:
        data = json.loads(raw or "[]")
    except ValueError:
        log.warning(f"[home_alerts] {SETTING} is not valid JSON; no home notices")
        return []
    out = []
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict):
            continue
        eid = str(item.get("entity_id") or "").strip()
        event = item.get("event")
        if "." not in eid or event not in EVENTS:
            log.warning(f"[home_alerts] ignoring mapping entry {item!r}")
            continue
        people = [str(p).strip().lower() for p in (item.get("people") or []) if str(p).strip()]
        out.append({**item, "entity_id": eid, "people": people})
    return out


def _ts(raw) -> float | None:
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def _minutes(entry: dict) -> float:
    try:
        return float(entry.get("minutes"))
    except (TypeError, ValueError):
        return float(DEFAULT_MINUTES.get(entry["event"], 0))


def _running(state: str, watts: float) -> bool:
    try:
        return float(state) > watts
    except (TypeError, ValueError):
        return str(state).lower() in _RUNNING_STATES


def _duration(seconds: float) -> str:
    mins = max(1, round(seconds / 60))
    return f"{mins} min" if mins < 60 else f"{mins // 60} h {mins % 60:02d} min"


class Watcher:
    """Turns successive state snapshots into notices. Holds the previous
    round and each appliance's run, so it lives as long as the loop."""

    def __init__(self):
        self.prev: dict[str, dict] = {}
        self.runs: dict[str, dict] = {}

    def check(self, entries: list[dict], states: dict[str, dict], now: float, nobody_home: bool | None, tz) -> list[tuple[dict, dict]]:
        """(entry, notice) pairs for this round."""
        out = []
        for entry in entries:
            eid = entry["entity_id"]
            cur = states.get(eid)
            if cur is None:
                continue
            prev = self.prev.get(eid)
            note = self._notice(entry, cur, prev, now, nobody_home, tz)
            if note:
                name = (cur.get("attributes") or {}).get("friendly_name") or eid
                note["title"] = note["title"].format(name=name)[:120]
                out.append((entry, {"category": "home", "kind": entry["event"], "priority": 4,
                                    "tag": f"home:{eid}", "data": {"url": "/", "entity_id": eid}, **note}))
        for entry in entries:
            cur = states.get(entry["entity_id"])
            if cur is not None:
                self.prev[entry["entity_id"]] = {"state": cur.get("state"), "last_changed": cur.get("last_changed")}
        return out

    def _notice(self, entry: dict, cur: dict, prev: dict | None, now: float, nobody_home: bool | None, tz) -> dict | None:
        event, eid = entry["event"], entry["entity_id"]
        state = cur.get("state")
        changed_at = cur.get("last_changed")
        if state in _UNKNOWN:
            return None
        changed = prev is not None and changed_at != prev.get("last_changed")
        # Went on since last round: on now, or on and off again in between
        # (a doorbell press is often shorter than a round). An event entity's
        # state is the time it last fired, so any change is a firing.
        rose = changed and (eid.startswith("event.") or state == "on" or prev.get("state") == "off")
        since = _ts(changed_at) or now

        if event == "doorbell" and rose:
            return {"title": "Someone is at the {name}", "body": f"Doorbell at {_clock(since, tz)}.",
                    "once": f"home:doorbell:{eid}:{changed_at}"}
        if event == "motion_away" and rose and nobody_home:
            return {"title": "Motion: {name}", "body": "Nobody is home.",
                    "once": f"home:motion_away:{eid}:{int(now // MOTION_QUIET_S)}"}
        if event == "garage_open" and state in ("open", "on") and now - since >= _minutes(entry) * 60:
            return {"title": "{name} is still open", "body": f"Open for {_duration(now - since)}, since {_clock(since, tz)}.",
                    "once": f"home:garage_open:{eid}:{changed_at}"}
        if event == "door_unlocked_night" and state == "unlocked" and now - since >= _minutes(entry) * 60:
            local = datetime.fromtimestamp(now, tz)
            if local.hour >= NIGHT_START_HOUR or local.hour < NIGHT_END_HOUR:
                night = (local - timedelta(hours=NIGHT_END_HOUR + 1)).date().isoformat()
                return {"title": "{name} is unlocked", "body": f"Unlocked since {_clock(since, tz)}.",
                        "once": f"home:door_unlocked_night:{eid}:{night}:{changed_at}"}
            return None
        if event == "water_leak" and state == "on" and (prev is None or changed):
            return {"title": "Water leak: {name}", "body": f"Water detected at {_clock(since, tz)}.", "priority": 5,
                    "once": f"home:water_leak:{eid}:{changed_at}"}
        if event == "appliance_done":
            return self._appliance(entry, state, now)
        return None

    def _appliance(self, entry: dict, state: str, now: float) -> dict | None:
        eid = entry["entity_id"]
        try:
            watts = float(entry.get("watts"))
        except (TypeError, ValueError):
            watts = DEFAULT_WATTS
        run = self.runs.get(eid)
        if _running(state, watts):
            if run is None:
                self.runs[eid] = {"since": now, "low_since": None}
            else:
                run["low_since"] = None  # a pause mid-cycle, not the end
            return None
        if run is None:
            return None
        if run["low_since"] is None:
            run["low_since"] = now
            return None
        if now - run["low_since"] < _minutes(entry) * 60:
            return None
        del self.runs[eid]
        ran = run["low_since"] - run["since"]
        if ran < MIN_RUN_S:
            return None
        return {"title": "{name} is done", "body": f"Ran for {_duration(ran)}.",
                "once": f"home:appliance_done:{eid}:{int(run['since'])}"}


async def nobody_home() -> bool | None:
    """True when everyone the presence tracker knows is away; None when it knows nobody."""
    from services.execution.presence import get_presence_tracker

    presence = await get_presence_tracker().get_all_presence()
    if not presence:
        return None
    return all((p or {}).get("room") == "not_home" for p in presence.values())


_watcher = Watcher()
_mapping: tuple[float, list[dict]] = (0.0, [])


async def _read_setting() -> str | None:
    from services.config import IDENTITY_SVC_URL, INTERNAL_SECRET

    async with aiohttp.ClientSession() as session, session.get(
        f"{IDENTITY_SVC_URL.rstrip('/')}/api/settings/{SETTING}", headers={"X-Internal-Secret": INTERNAL_SECRET},
        timeout=aiohttp.ClientTimeout(total=5),
    ) as resp:
        if resp.status == 404:
            return None
        resp.raise_for_status()
        return (await resp.json(content_type=None) or {}).get("value")


async def _entries() -> list[dict]:
    """The mapping, re-read at most once a minute."""
    global _mapping
    if time.time() - _mapping[0] < SETTING_TTL_S:
        return _mapping[1]
    try:
        entries = parse_mapping(await _read_setting())
    except Exception as e:  # identity briefly away: keep the last mapping
        log.warning(f"[home_alerts] could not read {SETTING}: {type(e).__name__}: {e}")
        return _mapping[1]
    _mapping = (time.time(), entries)
    return entries


async def check_once(watcher: Watcher | None = None) -> int:
    """One round; returns how many notices were sent."""
    from services.common import notify
    from services.execution import ha_client
    from services.execution.handlers.calendar import _get_local_tz
    from services.execution.main import resolve_first_user

    watcher = watcher or _watcher
    entries = await _entries()
    if not entries:
        return 0
    creds = await resolve_first_user() or {}
    if not creds.get("ha_url") or not creds.get("ha_token"):
        log.warning("[home_alerts] the system account has no Home Assistant login; no home notices")
        return 0
    states = {s.get("entity_id"): s for s in await ha_client.get_all_states(creds["ha_url"], creds["ha_token"]) or []
              if isinstance(s, dict)}
    if not states:
        return 0
    away = await nobody_home() if any(e["event"] == "motion_away" for e in entries) else None
    notices = watcher.check(entries, states, time.time(), away, _get_local_tz())
    if not notices:
        return 0
    sent = 0
    everyone = await notify.family() if any(not e["people"] for e, _ in notices) else []
    for entry, note in notices:
        sent += await notify.send_many(entry["people"] or everyone, lambda user, note=note: {"user": user, **note})
        log.info(f"[home_alerts] {entry['event']} {entry['entity_id']}: {note['title']}")
    return sent


async def home_alert_loop() -> None:
    while True:
        await asyncio.sleep(INTERVAL_S)
        try:
            await check_once()
        except Exception as e:  # keep looping, but never fail silently
            log.warning(f"[home_alerts] pass failed: {type(e).__name__}: {e}")

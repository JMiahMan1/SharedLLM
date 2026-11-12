"""Thermostat schedules, holds, away mode and an energy rule, on top of the scheduler.

A *program* is a scheduler schedule (``switch.schedule_*``, nielsfaber's shape,
kept in the same store) whose timeslots only set thermostats: one setpoint per
slot (hvac mode plus a target or a low/high range, optionally a fan mode)
applied to every thermostat the program names. The household's imported
schedules already have that shape, so they are programs as they stand: nothing
is rewritten to adopt them, and saving one back produces the same timeslots.

What decides a thermostat's setpoint, first match wins:

1. a **hold** ("72 until 6 pm", "until the next slot", or permanent),
2. **away** (a manual vacation with optional dates, or nobody home),
3. the **energy rule** (inside a time-of-use window, power above the limit for
   a while sheds the thermostats; a hot room is brought back once power drops),
4. the **schedule** (the slot in progress).

The scheduler still fires the slots; :meth:`ClimateManager.filter_action` sees
each climate action first and drops or replaces it for a held, away or shed
thermostat. Every thermostat's plan is published as
``sensor.<thermostat>_schedule`` so Jarvis can answer "when does the
downstairs thermostat change?".

Nothing here names a thermostat, a person, a sensor or a rate window: they are
discovered from the hub or stored as settings an admin edits.
"""
from __future__ import annotations

import asyncio
import contextlib
import copy
import logging
from datetime import UTC, datetime, time, timedelta
from typing import Any

from services.homehub.automation import WEEKDAYS, AutomationEngine, Schedule, _entity_list, parse_time
from services.homehub.automation_editor import Editor, EditorError

log = logging.getLogger("homehub.climate_schedule")

SETTINGS_KEY = "climate_settings"
HOLDS_KEY = "climate_holds"
CLIMATE_SERVICES = {"set_temperature", "set_hvac_mode", "set_fan_mode", "turn_off"}
SETPOINT_KEYS = ("hvac_mode", "temperature", "target_temp_low", "target_temp_high", "fan_mode")
#: How often the energy rule looks at the power sensor while a window is open.
ENERGY_TICK_S = 60.0

DEFAULT_SETTINGS: dict[str, Any] = {
    "away": {"enabled": False, "start": None, "end": None, "auto": False, "persons": [], "thermostats": [],
             "setpoint": {"hvac_mode": "off"}, "per_entity": {}},
    "energy": {"enabled": False, "power_sensor": None, "windows": [], "shed_after_min": 5,
               "resume_below_kw": None, "comfort_max_temp": None, "comfort_max_temp_by_entity": {},
               "comfort_setpoint": None, "thermostats": []},
}


# -- setpoints and programs ---------------------------------------------------------------------

def clean_setpoint(raw: Any, *, where: str = "setpoint") -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise EditorError(f"{where} must be an object")
    sp: dict[str, Any] = {}
    mode = raw.get("hvac_mode")
    if mode is not None:
        sp["hvac_mode"] = str(mode)
    for k in ("temperature", "target_temp_low", "target_temp_high"):
        v = raw.get(k)
        if v is None or v == "":
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            raise EditorError(f"{where}.{k} must be a number") from None
        sp[k] = int(f) if f.is_integer() else f
    if raw.get("fan_mode"):
        sp["fan_mode"] = str(raw["fan_mode"])
    if ("target_temp_low" in sp) != ("target_temp_high" in sp):
        raise EditorError(f"{where}: a range needs both target_temp_low and target_temp_high")
    if "target_temp_low" in sp and sp["target_temp_low"] >= sp["target_temp_high"]:
        raise EditorError(f"{where}: target_temp_low must be below target_temp_high")
    if "temperature" in sp and "target_temp_low" in sp:
        raise EditorError(f"{where}: give a temperature or a range, not both")
    if not sp:
        raise EditorError(f"{where} is empty: give an hvac_mode, a temperature or a range")
    return sp


def setpoint_actions(entity_id: Any, sp: dict[str, Any]) -> list[dict[str, Any]]:
    """The scheduler actions that set ``sp``. A temperature carries its hvac mode in the same call."""
    acts: list[dict[str, Any]] = []
    temps = {k: sp[k] for k in ("temperature", "target_temp_low", "target_temp_high") if k in sp}
    if temps:
        data = dict(temps)
        if "hvac_mode" in sp:
            data["hvac_mode"] = sp["hvac_mode"]
        acts.append({"service": "climate.set_temperature", "entity_id": entity_id, "service_data": data})
    elif "hvac_mode" in sp:
        acts.append({"service": "climate.set_hvac_mode", "entity_id": entity_id,
                     "service_data": {"hvac_mode": sp["hvac_mode"]}})
    if "fan_mode" in sp:
        acts.append({"service": "climate.set_fan_mode", "entity_id": entity_id,
                     "service_data": {"fan_mode": sp["fan_mode"]}})
    return acts


def _action_parts(act: dict[str, Any]) -> tuple[str, str, list[str], dict[str, Any]]:
    svc = str(act.get("service") or act.get("action") or "")
    domain, _, service = svc.partition(".")
    target = act.get("target") or {}
    ents = _entity_list(act.get("entity_id") or (target.get("entity_id") if isinstance(target, dict) else None))
    return domain, service, [str(e) for e in ents], dict(act.get("service_data") or act.get("data") or {})


def slot_setpoints(slot: dict[str, Any]) -> dict[str, dict[str, Any]] | None:
    """entity -> setpoint for a timeslot made only of thermostat actions; None if it does anything else."""
    out: dict[str, dict[str, Any]] = {}
    for act in slot.get("actions") or []:
        domain, service, ents, data = _action_parts(act)
        if domain != "climate" or service not in CLIMATE_SERVICES or not ents:
            return None
        part: dict[str, Any] = {}
        if service == "turn_off":
            part["hvac_mode"] = "off"
        else:
            unknown = set(data) - set(SETPOINT_KEYS)
            if unknown:
                return None
            part = {k: data[k] for k in SETPOINT_KEYS if k in data}
        for e in ents:
            out.setdefault(e, {}).update(part)
    return out or None


def program_from_schedule(conf: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    """``(program, None)`` when the schedule is a thermostat program, else ``(None, why not)``."""
    slots_in = conf.get("timeslots") or []
    if not slots_in:
        return None, "no timeslots"
    entity_ids: list[str] | None = None
    slots = []
    for slot in slots_in:
        if slot.get("conditions"):
            return None, "a timeslot has conditions"
        sps = slot_setpoints(slot)
        if sps is None:
            return None, "a timeslot does more than set thermostats"
        order = []
        for act in slot.get("actions") or []:
            for e in _action_parts(act)[2]:
                if e not in order:
                    order.append(e)
        if entity_ids is None:
            entity_ids = order
        elif set(order) != set(entity_ids):
            return None, "its timeslots set different thermostats"
        values = list(sps.values())
        if any(v != values[0] for v in values):
            return None, "its thermostats get different setpoints"
        # A slot whose per-thermostat actions are not in the shape we write would not survive a save.
        if [a for e in order for a in setpoint_actions(e, values[0])] != [_plain(a) for a in slot.get("actions")]:
            return None, "its actions are not in the program shape"
        slots.append({"start": slot.get("start"), "stop": slot.get("stop"), **values[0]})
    return {
        "id": str(conf.get("schedule_id")), "name": conf.get("name") or "", "entity_ids": entity_ids or [],
        "weekdays": list(conf.get("weekdays") or ["daily"]), "start_date": conf.get("start_date"),
        "end_date": conf.get("end_date"), "enabled": bool(conf.get("enabled", True)),
        "repeat_type": conf.get("repeat_type") or "repeat", "slots": slots,
    }, None


def _plain(act: dict[str, Any]) -> dict[str, Any]:
    domain, service, ents, data = _action_parts(act)
    eid: Any = ents[0] if len(ents) == 1 else ents
    return {"service": f"{domain}.{service}", "entity_id": eid, "service_data": data}


def _fmt_time(value: Any) -> str:
    return parse_time(value).strftime("%H:%M:%S")


def schedule_from_program(raw: Any, known_climate: set[str] | None = None) -> dict[str, Any]:
    """A program, validated, as the scheduler schedule that runs it."""
    if not isinstance(raw, dict):
        raise EditorError("A program must be an object")
    name = str(raw.get("name") or "").strip()
    if not name:
        raise EditorError("A program needs a name")
    ents = [str(e) for e in raw.get("entity_ids") or []]
    if not ents or not all(e.startswith("climate.") for e in ents):
        raise EditorError("A program needs at least one thermostat (climate entity)")
    if known_climate is not None:
        missing = [e for e in ents if e not in known_climate]
        if missing:
            raise EditorError(f"Unknown thermostat: {', '.join(missing)}")
    slots_in = raw.get("slots") or []
    if not isinstance(slots_in, list) or not slots_in:
        raise EditorError("A program needs at least one slot")
    parsed = []
    for i, slot in enumerate(slots_in):
        if not isinstance(slot, dict):
            raise EditorError(f"slot {i + 1} must be an object")
        try:
            start = _fmt_time(slot.get("start"))
            stop = _fmt_time(slot["stop"]) if slot.get("stop") else None
        except (ValueError, TypeError) as exc:
            raise EditorError(f"slot {i + 1}: {exc}") from None
        parsed.append((start, stop, clean_setpoint(slot, where=f"slot {i + 1}")))
    starts = [p[0] for p in parsed]
    if len(set(starts)) != len(starts):
        raise EditorError("Two slots start at the same time")
    # Timeline order. A slot without a stop runs until the next slot starts; the last one until the first.
    parsed.sort(key=lambda p: p[0])
    timeslots = []
    for i, (start, stop, sp) in enumerate(parsed):
        if stop is None:
            stop = parsed[(i + 1) % len(parsed)][0] if len(parsed) > 1 else start
        timeslots.append({"start": start, "stop": stop, "conditions": [], "condition_type": None,
                          "track_conditions": False,
                          "actions": [a for e in ents for a in setpoint_actions(e, sp)]})
    return {"name": name, "weekdays": list(raw.get("weekdays") or ["daily"]),
            "start_date": raw.get("start_date") or None, "end_date": raw.get("end_date") or None,
            "repeat_type": raw.get("repeat_type") or "repeat", "enabled": bool(raw.get("enabled", True)),
            "timeslots": timeslots, "tags": list(raw.get("tags") or [])}


def _num(v: Any) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    return str(int(f)) if f.is_integer() else f"{f:.1f}"


def describe(sp: dict[str, Any] | None) -> str:
    if not sp:
        return "nothing"
    mode = sp.get("hvac_mode")
    if mode == "off":
        return "off"
    if "temperature" in sp:
        text = _num(sp["temperature"])
    elif "target_temp_low" in sp:
        text = f"{_num(sp['target_temp_low'])} to {_num(sp['target_temp_high'])}"
    else:
        text = ""
    if mode:
        text = f"{text} {mode}".strip()
    if sp.get("fan_mode"):
        text += f", fan {sp['fan_mode']}"
    return text


def _clock_text(dt: datetime) -> str:
    return dt.strftime("%I:%M %p").lstrip("0")


# -- the manager ------------------------------------------------------------------------------------

class ClimateManager:
    def __init__(self, engine: AutomationEngine, editor: Editor, store: Any = None) -> None:
        self.engine = engine
        self.editor = editor
        self.store = store
        self.hub = engine.hub
        self.settings = self._load_settings()
        self.holds: dict[str, dict[str, Any]] = dict(self._kv(HOLDS_KEY) or {})
        self.shed: list[str] = []
        self.comforted: list[str] = []
        self._above_since: datetime | None = None
        self._window: dict[str, Any] | None = None
        self._away_active = False
        self._cancels: list[Any] = []
        self._hold_timers: dict[str, Any] = {}
        self._unsubs: list[Any] = []
        self._tasks: set[asyncio.Task] = set()
        self._running = False
        # Known before the engine replays the slot in progress at startup, so that replay honours away mode.
        with contextlib.suppress(Exception):
            self._away_active = self.away_state()[0]
        engine.action_filter = self.filter_action

    # -- storage ------------------------------------------------------------------------------
    def _kv(self, key: str) -> Any:
        if self.store is None:
            return None
        try:
            return self.store.get_kv(key)
        except Exception:
            log.exception("store read failed for %s", key)
            return None

    def _save(self, key: str, value: Any) -> None:
        if self.store is None:
            return
        try:
            self.store.set_kv(key, value)
        except Exception:
            log.exception("store write failed for %s", key)

    def _load_settings(self) -> dict[str, Any]:
        merged = copy.deepcopy(DEFAULT_SETTINGS)
        saved = self._kv(SETTINGS_KEY) or {}
        for section in merged:
            if isinstance(saved.get(section), dict):
                merged[section].update(saved[section])
        return merged

    # -- time ---------------------------------------------------------------------------------
    @property
    def tz(self) -> Any:
        return self.engine.tz

    def now(self) -> datetime:
        return self.engine.clock.now()

    def _parse_dt(self, value: Any) -> datetime | None:
        if not value:
            return None
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=self.tz)

    # -- discovery ------------------------------------------------------------------------------
    def thermostats(self) -> list[str]:
        return sorted(s.entity_id for s in self.hub.states.all("climate"))

    def _schedules_for(self, eid: str) -> list[Schedule]:
        out = []
        for s in self.engine.schedules.values():
            if any(eid in _action_parts(a)[2] for slot in s.timeslots for a in slot.get("actions") or []):
                out.append(s)
        return out

    def _slot_sp(self, s: Schedule, idx: int, eid: str) -> dict[str, Any] | None:
        sps = slot_setpoints({"actions": [a for a in s.timeslots[idx].get("actions") or []
                                          if eid in _action_parts(a)[2] and _action_parts(a)[0] == "climate"]})
        return (sps or {}).get(eid)

    def current(self, eid: str, at: datetime | None = None) -> dict[str, Any] | None:
        """The slot in progress for this thermostat (the latest-started one if programs overlap)."""
        at = at or self.now()
        best = None
        for s in self._schedules_for(eid):
            if not s.enabled or s.unsupported_reason:
                continue
            with contextlib.suppress(Exception):
                hit = self.engine.current_slot(s, at)
                if hit is None:
                    continue
                idx, start = hit
                sp = self._slot_sp(s, idx, eid)
                if sp is None:
                    continue
                if best is None or start > best["start"]:
                    end = None
                    stop_v = s.timeslots[idx].get("stop")
                    if stop_v:
                        end = self.engine._slot_instant(stop_v, start.astimezone(self.tz).date())
                        if end is not None and end <= start:
                            end += timedelta(days=1)
                    best = {"program_id": s.id, "program_name": s.name, "slot": idx, "start": start, "end": end,
                            "setpoint": sp}
        return best

    def next_change(self, eid: str, after: datetime | None = None) -> dict[str, Any] | None:
        after = after or self.now()
        best = None
        for s in self._schedules_for(eid):
            if not s.enabled or s.unsupported_reason:
                continue
            cursor = after
            for _ in range(64):  # skip this schedule's slots that do not touch the thermostat
                nxt = self.engine.schedule_next(s, cursor)
                if nxt is None:
                    break
                when, idx = nxt
                sp = self._slot_sp(s, idx, eid)
                if sp is not None:
                    if best is None or when < best["at"]:
                        best = {"program_id": s.id, "program_name": s.name, "slot": idx, "at": when, "setpoint": sp}
                    break
                cursor = when
        return best

    # -- away -----------------------------------------------------------------------------------
    def away_state(self) -> tuple[bool, str | None]:
        away = self.settings["away"]
        now = self.now()
        if away.get("enabled"):
            start, end = self._parse_dt(away.get("start")), self._parse_dt(away.get("end"))
            if (start is None or now >= start) and (end is None or now < end):
                return True, "vacation"
        if away.get("auto"):
            persons = [p for p in (away.get("persons") or []) if p] or [s.entity_id for s in self.hub.states.all("person")]
            states = [self.hub.states.get(p) for p in persons]
            known = [st for st in states if st is not None and st.state not in ("unknown", "unavailable")]
            if known and all(st.state != "home" for st in known):
                return True, "nobody is home"
        return False, None

    def _away_applies(self, eid: str) -> bool:
        ents = self.settings["away"].get("thermostats") or []
        return not ents or eid in ents

    def _away_setpoint(self, eid: str) -> dict[str, Any]:
        away = self.settings["away"]
        return dict((away.get("per_entity") or {}).get(eid) or away.get("setpoint") or {"hvac_mode": "off"})

    # -- precedence -----------------------------------------------------------------------------
    def active_hold(self, eid: str) -> dict[str, Any] | None:
        hold = self.holds.get(eid)
        if hold is None:
            return None
        until = self._parse_dt(hold.get("until"))
        if until is not None and until <= self.now():
            return None
        return hold

    def effective(self, eid: str) -> tuple[str, dict[str, Any] | None]:
        hold = self.active_hold(eid)
        if hold is not None:
            return "hold", dict(hold["setpoint"])
        if self._away_active and self._away_applies(eid):
            return "away", self._away_setpoint(eid)
        if eid in self.comforted and self.settings["energy"].get("comfort_setpoint"):
            return "energy", dict(self.settings["energy"]["comfort_setpoint"])
        if eid in self.shed:
            return "energy", {"hvac_mode": "off"}
        cur = self.current(eid)
        if cur is not None:
            return "schedule", dict(cur["setpoint"])
        return "none", None

    def filter_action(self, s: Schedule, idx: int, act: dict[str, Any]) -> list[dict[str, Any]]:
        """Called by the scheduler for each slot action: holds, away and the energy rule win over it."""
        domain, _service, ents, _data = _action_parts(act)
        if domain != "climate" or not ents:
            return [act]
        keep: list[str] = []
        out: list[dict[str, Any]] = []
        for eid in ents:
            hold = self.holds.get(eid)
            if hold is not None and self.active_hold(eid) is None:
                self._clear_hold(eid)  # it ran out at this very slot: the slot applies
            source, sp = self.effective(eid)
            if source in ("schedule", "none"):
                keep.append(eid)
            elif source in ("away", "energy") and sp is not None:
                # Replace once per thermostat, on its first action of the slot.
                marker = (s.id, idx, eid, self.now().replace(second=0, microsecond=0))
                if marker != getattr(self, "_last_replaced", None):
                    self._last_replaced = marker
                    out += setpoint_actions(eid, sp)
            # a hold: drop it
        if keep:
            if len(keep) == len(ents):
                out.insert(0, act)
            else:
                out.insert(0, {**act, "entity_id": keep if len(keep) > 1 else keep[0]})
        self._refresh_soon()
        return out

    # -- applying -----------------------------------------------------------------------------
    async def apply(self, eid: str, sp: dict[str, Any] | None) -> None:
        if not sp:
            return
        for act in setpoint_actions(eid, sp):
            domain, service = act["service"].split(".", 1)
            try:
                await self.hub.call_service(domain, service, {"entity_id": eid, **act["service_data"]})
            except Exception as exc:
                log.warning("%s %s: %s", act["service"], eid, exc)

    async def apply_effective(self, eid: str) -> None:
        await self.apply(eid, self.effective(eid)[1])

    def _spawn(self, coro: Any) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # -- holds -------------------------------------------------------------------------------------
    def _until(self, eid: str, until: Any) -> tuple[str, datetime | None]:
        if until in (None, "", "permanent"):
            return "permanent", None
        if until == "next_slot":
            nxt = self.next_change(eid)
            if nxt is None:
                raise EditorError("This thermostat has no next slot; hold permanently or until a time")
            return "next_slot", nxt["at"]
        text = str(until)
        try:
            if "T" in text or len(text) > 8:
                when = self._parse_dt(text)
            else:
                t = parse_time(text)
                local = self.now().astimezone(self.tz)
                when = datetime.combine(local.date(), time(t.hour, t.minute, t.second), tzinfo=self.tz)
                if when <= local:
                    when = datetime.combine(local.date() + timedelta(days=1), time(t.hour, t.minute, t.second),
                                            tzinfo=self.tz)
        except (ValueError, TypeError):
            raise EditorError("until must be next_slot, permanent, a time (HH:MM) or a date and time") from None
        if when is None or when <= self.now():
            raise EditorError("A hold must end in the future")
        return "until", when.astimezone(UTC)

    async def set_hold(self, eid: str, setpoint: Any, until: Any, by: str | None = None) -> dict[str, Any]:
        if self.hub.states.get(eid) is None or not eid.startswith("climate."):
            raise EditorError("No such thermostat", 404)
        sp = clean_setpoint(setpoint)
        kind, when = self._until(eid, until)
        self.holds[eid] = {"setpoint": sp, "until": when.isoformat() if when else None, "kind": kind,
                           "set_at": self.now().isoformat(), "by": by}
        self._save(HOLDS_KEY, self.holds)
        self._arm_hold(eid)
        await self.apply(eid, sp)
        self.publish(eid)
        return self.thermostat(eid)

    async def resume(self, eid: str) -> dict[str, Any]:
        if self.hub.states.get(eid) is None:
            raise EditorError("No such thermostat", 404)
        self._clear_hold(eid)
        await self.apply_effective(eid)
        self.publish(eid)
        return self.thermostat(eid)

    def _clear_hold(self, eid: str) -> None:
        if self.holds.pop(eid, None) is not None:
            self._save(HOLDS_KEY, self.holds)
        cancel = self._hold_timers.pop(eid, None)
        if cancel:
            cancel()

    def _arm_hold(self, eid: str) -> None:
        cancel = self._hold_timers.pop(eid, None)
        if cancel:
            cancel()
        hold = self.holds.get(eid)
        until = self._parse_dt(hold.get("until")) if hold else None
        if until is None or not self._running:
            return

        def expire(_when: datetime, eid: str = eid, until: datetime = until) -> None:
            self._hold_timers.pop(eid, None)
            if eid not in self.holds:
                return
            self._clear_hold(eid)
            # A "next slot" hold ends as that slot starts, and the slot itself sets the thermostat.
            cur = self.current(eid)
            if cur is not None and abs((cur["start"] - until).total_seconds()) < 1:
                self.publish(eid)
                return
            self._spawn(self._resume_after_hold(eid))

        self._hold_timers[eid] = self.engine.scheduler.at(until, expire)

    async def _resume_after_hold(self, eid: str) -> None:
        await self.apply_effective(eid)
        self.publish(eid)

    # -- settings --------------------------------------------------------------------------------
    def validate_settings(self, raw: Any) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise EditorError("Settings must be an object")
        out = copy.deepcopy(self.settings)
        if "away" in raw:
            a = raw["away"] or {}
            if not isinstance(a, dict):
                raise EditorError("away must be an object")
            away = out["away"]
            for k in ("enabled", "auto"):
                if k in a:
                    away[k] = bool(a[k])
            for k in ("start", "end"):
                if k in a:
                    try:
                        away[k] = self._parse_dt(a[k]).isoformat() if a[k] else None
                    except (ValueError, TypeError):
                        raise EditorError(f"away.{k} must be a date and time") from None
            if away.get("start") and away.get("end") and self._parse_dt(away["start"]) >= self._parse_dt(away["end"]):
                raise EditorError("away.start must be before away.end")
            for k in ("persons", "thermostats"):
                if k in a:
                    away[k] = [str(x) for x in a[k] or []]
            if "setpoint" in a:
                away["setpoint"] = clean_setpoint(a["setpoint"], where="away.setpoint")
            if "per_entity" in a:
                away["per_entity"] = {str(e): clean_setpoint(sp, where=f"away.per_entity.{e}")
                                      for e, sp in (a["per_entity"] or {}).items()}
        if "energy" in raw:
            e = raw["energy"] or {}
            if not isinstance(e, dict):
                raise EditorError("energy must be an object")
            energy = out["energy"]
            if "enabled" in e:
                energy["enabled"] = bool(e["enabled"])
            if "power_sensor" in e:
                energy["power_sensor"] = str(e["power_sensor"]) if e["power_sensor"] else None
            for k in ("shed_after_min", "resume_below_kw", "comfort_max_temp"):
                if k in e:
                    try:
                        energy[k] = float(e[k]) if e[k] not in (None, "") else None
                    except (TypeError, ValueError):
                        raise EditorError(f"energy.{k} must be a number") from None
            if energy.get("shed_after_min") is None:
                energy["shed_after_min"] = 0.0
            if "comfort_setpoint" in e:
                energy["comfort_setpoint"] = (clean_setpoint(e["comfort_setpoint"], where="energy.comfort_setpoint")
                                              if e["comfort_setpoint"] else None)
            if "thermostats" in e:
                energy["thermostats"] = [str(x) for x in e["thermostats"] or []]
            if "comfort_max_temp_by_entity" in e:
                try:
                    energy["comfort_max_temp_by_entity"] = {
                        str(k): float(v) for k, v in (e["comfort_max_temp_by_entity"] or {}).items()
                        if v not in (None, "")}
                except (TypeError, ValueError, AttributeError):
                    raise EditorError("energy.comfort_max_temp_by_entity maps thermostats to numbers") from None
            if "windows" in e:
                windows = []
                for i, w in enumerate(e["windows"] or []):
                    if not isinstance(w, dict):
                        raise EditorError(f"energy window {i + 1} must be an object")
                    try:
                        start, stop = _fmt_time(w.get("start")), _fmt_time(w.get("stop"))
                    except (ValueError, TypeError):
                        raise EditorError(f"energy window {i + 1}: start and stop must be times") from None
                    months = [int(m) for m in w.get("months") or range(1, 13)]
                    if not all(1 <= m <= 12 for m in months):
                        raise EditorError(f"energy window {i + 1}: months are 1 to 12")
                    days = [str(d) for d in w.get("weekdays") or WEEKDAYS]
                    if not all(d in WEEKDAYS for d in days):
                        raise EditorError(f"energy window {i + 1}: weekdays are mon..sun")
                    limit = w.get("limit_kw")
                    windows.append({"name": str(w.get("name") or f"Window {i + 1}"), "months": months,
                                    "weekdays": days, "start": start, "stop": stop,
                                    "limit_kw": float(limit) if limit not in (None, "") else None})
                energy["windows"] = windows
            if energy["enabled"] and not energy.get("power_sensor") and any(
                    w.get("limit_kw") is not None for w in energy["windows"]):
                raise EditorError("A power limit needs a power sensor")
        return out

    async def save_settings(self, raw: Any) -> dict[str, Any]:
        self.settings = self.validate_settings(raw)
        self._save(SETTINGS_KEY, self.settings)
        if self._running:
            self._resubscribe()
            await self._check_away()
            await self._energy_tick()
            self.publish_all()
        return self.settings

    # -- energy ------------------------------------------------------------------------------------
    def current_window(self, at: datetime | None = None) -> dict[str, Any] | None:
        local = (at or self.now()).astimezone(self.tz)
        day = WEEKDAYS[local.weekday()]
        now_t = local.time()
        for w in self.settings["energy"].get("windows") or []:
            start, stop = parse_time(w["start"]), parse_time(w["stop"])
            if local.month not in w["months"]:
                continue
            if start <= stop:
                inside = day in w["weekdays"] and start <= now_t < stop
            else:  # crosses midnight: the window belongs to the day it started
                prev = WEEKDAYS[(local.weekday() - 1) % 7]
                inside = (day in w["weekdays"] and now_t >= start) or (prev in w["weekdays"] and now_t < stop)
            if inside:
                return w
        return None

    def power_kw(self) -> float | None:
        sensor = self.settings["energy"].get("power_sensor")
        st = self.hub.states.get(sensor) if sensor else None
        if st is None:
            return None
        try:
            value = float(st.state)
        except (TypeError, ValueError):
            return None
        unit = str(st.attributes.get("unit_of_measurement") or "kW")
        return value / 1000.0 if unit == "W" else value

    def _energy_targets(self) -> list[str]:
        ents = self.settings["energy"].get("thermostats") or []
        return [e for e in (ents or self.thermostats()) if self.hub.states.get(e) is not None]

    def _indoor(self, eid: str) -> float | None:
        st = self.hub.states.get(eid)
        try:
            return float(st.attributes.get("current_temperature")) if st else None
        except (TypeError, ValueError):
            return None

    async def _energy_tick(self) -> None:
        energy = self.settings["energy"]
        window = self.current_window() if energy.get("enabled") else None
        self._window = window
        if window is None:
            self._above_since = None
            if self.shed or self.comforted:
                released = list(dict.fromkeys(self.shed + self.comforted))
                self.shed, self.comforted = [], []
                for eid in released:
                    await self.apply_effective(eid)
                self.publish_all()
            return
        power = self.power_kw()
        limit = window.get("limit_kw")
        if limit is None or power is None:
            return
        now = self.now()
        if power > limit:
            self._above_since = self._above_since or now
            waited = (now - self._above_since).total_seconds() / 60.0
            if waited >= float(energy.get("shed_after_min") or 0):
                newly = [e for e in self._energy_targets() if e not in self.shed or e in self.comforted]
                if newly:
                    self.comforted = []
                    for eid in newly:
                        if eid not in self.shed:
                            self.shed.append(eid)
                        if self.effective(eid)[0] == "energy":
                            await self.apply(eid, {"hvac_mode": "off"})
                    log.info("energy rule: %.1f kW above %.1f kW, shed %s", power, limit, ", ".join(newly))
                    self.publish_all()
            return
        self._above_since = None
        resume = energy.get("resume_below_kw")
        sp = energy.get("comfort_setpoint")
        by_entity = energy.get("comfort_max_temp_by_entity") or {}

        def too_hot(eid: str) -> bool:
            limit_t = by_entity.get(eid, energy.get("comfort_max_temp"))
            return limit_t is not None and (self._indoor(eid) or 0) > float(limit_t)

        if not self.shed or resume is None or power >= resume or not sp:
            return
        # One thermostat at a time, hottest first, so the next reading shows what it added.
        hot = sorted((e for e in self.shed if e not in self.comforted and too_hot(e)),
                     key=lambda e: -(self._indoor(e) or 0))
        if hot:
            self.comforted.append(hot[0])
            if self.effective(hot[0])[0] == "energy":
                await self.apply(hot[0], sp)
            self.publish(hot[0])

    # -- away transitions ---------------------------------------------------------------------------
    async def _check_away(self) -> None:
        active, _why = self.away_state()
        if active == self._away_active:
            return
        self._away_active = active
        for eid in self.thermostats():
            if self._away_applies(eid) and self.active_hold(eid) is None:
                await self.apply_effective(eid)
        self.publish_all()

    def _arm_away_edges(self) -> None:
        away = self.settings["away"]
        for key in ("start", "end"):
            when = self._parse_dt(away.get(key)) if away.get("enabled") else None
            if when is not None and when > self.now():
                self._cancels.append(self.engine.scheduler.at(when, lambda _w: self._spawn(self._check_away())))

    # -- lifecycle -----------------------------------------------------------------------------------
    def _resubscribe(self) -> None:
        for u in self._unsubs:
            u()
        self._unsubs.clear()
        for c in self._cancels:
            c()
        self._cancels.clear()

        def persons_changed(eid: str, _old: Any, _new: Any) -> None:
            if eid.startswith("person.") and self.settings["away"].get("auto"):
                self._spawn(self._check_away())

        self._unsubs.append(self.engine._sub_state(persons_changed, None))
        sensor = self.settings["energy"].get("power_sensor")
        if self.settings["energy"].get("enabled") and sensor:
            self._unsubs.append(self.engine._sub_state(lambda *_a: self._spawn(self._energy_tick()), [sensor]))

            def tick(_w: datetime) -> None:
                self._spawn(self._energy_tick())
                if self._running:
                    self._cancels.append(self.engine.scheduler.after(ENERGY_TICK_S, tick))

            self._cancels.append(self.engine.scheduler.after(ENERGY_TICK_S, tick))
        self._arm_away_edges()

    async def start(self) -> None:
        self._running = True
        now = self.now()
        for eid, hold in list(self.holds.items()):
            until = self._parse_dt(hold.get("until"))
            if until is not None and until <= now:
                self._clear_hold(eid)  # ran out while the hub was down; the startup slot replay applies the schedule
            else:
                self._arm_hold(eid)
        self._away_active, _ = self.away_state()
        self._resubscribe()
        await self._energy_tick()
        self.publish_all()
        self._arm_refresh()

    async def stop(self) -> None:
        self._running = False
        for u in self._unsubs:
            u()
        self._unsubs.clear()
        for c in [*self._cancels, *self._hold_timers.values()]:
            c()
        self._cancels.clear()
        self._hold_timers.clear()
        if self.engine.action_filter == self.filter_action:
            self.engine.action_filter = None
        for t in list(self._tasks):
            t.cancel()

    # -- publishing ---------------------------------------------------------------------------------
    def sensor_id(self, eid: str) -> str:
        return f"sensor.{eid.split('.', 1)[1]}_schedule"

    def _name(self, eid: str) -> str:
        st = self.hub.states.get(eid)
        return str((st.attributes.get("friendly_name") if st else None) or eid)

    def summary(self, eid: str) -> str:
        source, sp = self.effective(eid)
        nxt = self.next_change(eid)
        hold = self.active_hold(eid)
        def local(d: datetime) -> datetime:
            return d.astimezone(self.tz)

        if source == "hold" and hold is not None:
            until = self._parse_dt(hold.get("until"))
            text = f"Held at {describe(sp)}" + (f" until {_clock_text(local(until))}" if until else " until resumed")
        elif source == "away":
            text = f"Away: {describe(sp)}"
        elif source == "energy":
            text = f"Energy rule: {describe(sp)}"
        elif source == "schedule":
            cur = self.current(eid)
            text = f"Following {cur['program_name']}: {describe(sp)}" if cur else describe(sp)
        else:
            text = "No schedule"
        if nxt is not None and source in ("schedule", "none"):
            text += f", goes to {describe(nxt['setpoint'])} at {_clock_text(local(nxt['at']))}"
            if local(nxt["at"]).date() != local(self.now()).date():
                text += f" on {local(nxt['at']).strftime('%A')}"
        return text

    def publish(self, eid: str) -> None:
        if self.hub.states.get(eid) is None:
            return
        source, sp = self.effective(eid)
        cur, nxt, hold = self.current(eid), self.next_change(eid), self.active_hold(eid)

        def loc(d: datetime | None) -> str | None:
            return d.astimezone(self.tz).isoformat() if d else None

        attrs = {
            "friendly_name": f"{self._name(eid)} schedule", "icon": "mdi:calendar-clock", "thermostat": eid,
            "source": source, "setpoint": sp, "setpoint_text": describe(sp),
            "program": cur["program_name"] if cur else None, "slot_start": loc(cur["start"]) if cur else None,
            "next_change_at": loc(nxt["at"]) if nxt else None, "next_setpoint": nxt["setpoint"] if nxt else None,
            "next_setpoint_text": describe(nxt["setpoint"]) if nxt else None,
            "next_program": nxt["program_name"] if nxt else None,
            "hold_until": hold.get("until") if hold else None, "hold_kind": hold.get("kind") if hold else None,
            "away": self._away_active and self._away_applies(eid), "summary": self.summary(eid),
        }
        self.hub.publish(self.sensor_id(eid), source, attrs)

    def publish_all(self) -> None:
        for eid in self.thermostats():
            self.publish(eid)

    def _arm_refresh(self) -> None:
        """Re-publish just after the next slot anywhere starts, so 'current' and 'next' move on."""
        cancel = getattr(self, "_refresh_cancel", None)
        if cancel:
            cancel()
        self._refresh_cancel = None
        if not self._running:
            return
        times = [n["at"] for e in self.thermostats() if (n := self.next_change(e)) is not None]
        if not times:
            return

        def fire(_w: datetime) -> None:
            self._refresh_cancel = None
            self.publish_all()
            self._arm_refresh()

        self._refresh_cancel = self.engine.scheduler.at(min(times) + timedelta(seconds=1), fire)

    def _refresh_soon(self) -> None:
        if self._running:
            self.engine.scheduler.after(1, lambda _w: (self.publish_all(), self._arm_refresh()))

    # -- views ----------------------------------------------------------------------------------------
    def thermostat(self, eid: str) -> dict[str, Any]:
        st = self.hub.states.get(eid)
        if st is None:
            raise EditorError("No such thermostat", 404)
        a = st.attributes
        area_id = self.hub.entity_area_id(eid) if hasattr(self.hub, "entity_area_id") else None
        area = self.hub.areas[area_id].name if area_id and area_id in self.hub.areas else None
        cur, nxt, hold = self.current(eid), self.next_change(eid), self.active_hold(eid)
        source, sp = self.effective(eid)
        supported = int(a.get("supported_features") or 0)
        return {
            "entity_id": eid, "name": self._name(eid), "area": area, "state": st.state,
            "current_temperature": a.get("current_temperature"), "temperature": a.get("temperature"),
            "target_temp_low": a.get("target_temp_low"), "target_temp_high": a.get("target_temp_high"),
            "fan_mode": a.get("fan_mode"), "hvac_action": a.get("hvac_action"),
            "capabilities": {"hvac_modes": list(a.get("hvac_modes") or []), "fan_modes": list(a.get("fan_modes") or []),
                             "min_temp": a.get("min_temp"), "max_temp": a.get("max_temp"),
                             "range": bool(supported & 2) or "heat_cool" in (a.get("hvac_modes") or []),
                             "unit": a.get("temperature_unit") or (self.hub.location or {}).get("unit_system")},
            "current": {**cur, "start": cur["start"].isoformat(), "end": cur["end"].isoformat() if cur["end"] else None}
            if cur else None,
            "next": {**nxt, "at": nxt["at"].isoformat()} if nxt else None,
            "hold": dict(hold) if hold else None,
            "effective": {"source": source, "setpoint": sp},
            "summary": self.summary(eid), "schedule_entity": self.sensor_id(eid),
        }

    def programs(self) -> list[dict[str, Any]]:
        out = []
        for s in self.editor.schedules():
            conf = self.editor.schedule_conf(s.id) or {
                "schedule_id": s.id, "name": s.name, "timeslots": s.timeslots, "weekdays": s.weekdays,
                "start_date": s.start_date.isoformat() if s.start_date else None,
                "end_date": s.end_date.isoformat() if s.end_date else None, "repeat_type": s.repeat_type}
            conf = {**conf, "enabled": s.enabled, "timeslots": s.timeslots}
            prog, _why = program_from_schedule(conf)
            if prog is None:
                continue
            prog.update(entity_id=s.entity_id, editable=True, reason=s.unsupported_reason,
                        next_trigger=s.next_trigger.isoformat() if s.next_trigger else None)
            out.append(prog)
        return out

    def is_program(self, s: Schedule) -> bool:
        return program_from_schedule({"timeslots": s.timeslots, "schedule_id": s.id})[0] is not None

    def overview(self) -> dict[str, Any]:
        active, why = self.away_state()
        return {
            "thermostats": [self.thermostat(e) for e in self.thermostats()],
            "programs": self.programs(), "settings": self.settings,
            "away": {"active": active, "reason": why},
            "energy": {"in_window": self._window is not None, "window": (self._window or {}).get("name"),
                       "power_kw": self.power_kw(), "shed": list(self.shed), "comforted": list(self.comforted)},
            "now": self.now().astimezone(self.tz).isoformat(), "time_zone": str(self.tz),
        }

    async def save_program(self, pid: str | None, raw: Any) -> dict[str, Any]:
        if pid is not None:
            s = self.editor.get_schedule(pid)
            if not self.is_program(s):
                raise EditorError("That schedule is not a thermostat program; edit it as a schedule", 409)
        conf = schedule_from_program(raw, set(self.thermostats()))
        if pid is not None:
            # Keep the tags and anything else the scheduler card stored.
            conf["tags"] = list(raw.get("tags") or (self.editor.schedule_conf(pid) or {}).get("tags") or [])
        s = await self.editor.save_schedule(pid, conf)
        self.publish_all()
        self._arm_refresh()
        for prog in self.programs():
            if prog["id"] == s.id:
                return prog
        raise EditorError("The program was saved but does not read back as a program", 500)

    async def delete_program(self, pid: str) -> None:
        s = self.editor.get_schedule(pid)
        if not self.is_program(s):
            raise EditorError("That schedule is not a thermostat program", 409)
        await self.editor.delete_schedule(pid)
        self.publish_all()
        self._arm_refresh()

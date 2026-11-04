"""Add, change and remove Skylight chores (docs/ACHIEVEMENTS.md, "Managing chores").

Skylight has no public API; these are the private frame endpoints the
community documented (``chrischall/skylight-mcp`` ``src/tools/chores.ts`` and
``members.ts``), all relative to ``/api/frames/{frame}``:

* ``GET  /categories``              the family members (a chore is assigned by
  its member's *category* id; there is no assignee field)
* ``POST /chores``                  one chore for one member, flat body:
  ``summary``, ``category_id``, ``start`` (YYYY-MM-DD), ``start_time``,
  ``description``, ``reward_points``, ``emoji_icon``
* ``POST /chores/create_multiple``  a repeating chore, or one for several
  members: ``category_ids`` and ``recurrence_set`` (``["RRULE:..."]``),
  ``recurring_until``
* ``PUT  /chores/{id}``             change it; ``apply_to`` ``one`` or ``all``
  for a repeating chore
* ``DELETE /chores/{id}?apply_to=one|all``  remove one day of it, or all of it

The HTTP call itself is ``main._skylight_request`` (passed in as ``call`` so
these functions test without a network). Nothing here decides *who* may do
this: the gateway and the assistant's chores handler do.
"""
from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from datetime import date as date_cls
from typing import Any

#: ``call(session, method, suffix, json_body=None, params=None)`` -> JSON or None.
Call = Callable[..., Awaitable[Any]]

APPLY_TO = ("one", "all")
MAX_TITLE = 120

_DAYS = {
    "mo": "MO", "mon": "MO", "monday": "MO", "mondays": "MO",
    "tu": "TU", "tue": "TU", "tues": "TU", "tuesday": "TU", "tuesdays": "TU",
    "we": "WE", "wed": "WE", "weds": "WE", "wednesday": "WE", "wednesdays": "WE",
    "th": "TH", "thu": "TH", "thur": "TH", "thurs": "TH", "thursday": "TH", "thursdays": "TH",
    "fr": "FR", "fri": "FR", "friday": "FR", "fridays": "FR",
    "sa": "SA", "sat": "SA", "saturday": "SA", "saturdays": "SA",
    "su": "SU", "sun": "SU", "sunday": "SU", "sundays": "SU",
}
_ORDER = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]
_WORDS = {
    "daily": "FREQ=DAILY", "every day": "FREQ=DAILY", "everyday": "FREQ=DAILY",
    "weekdays": "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR", "every weekday": "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR",
    "school days": "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR",
    "weekends": "FREQ=WEEKLY;BYDAY=SA,SU", "every weekend": "FREQ=WEEKLY;BYDAY=SA,SU",
    "monthly": "FREQ=MONTHLY", "every month": "FREQ=MONTHLY",
}
_NONE = {"", "none", "never", "once", "no", "one time", "just once"}


class ChoreError(ValueError):
    """A request Skylight cannot take (an unknown member, a bad repeat...)."""


def repeat_rule(repeat: str | None, start: date_cls | None = None) -> str | None:
    """``repeat`` as Skylight's ``"RRULE:..."`` string, or None for a one-off.

    Takes plain words ("daily", "weekdays", "weekly", "mon, wed and fri",
    "every tuesday") or a rule ("FREQ=WEEKLY;BYDAY=MO"). "weekly" is the
    start day's weekday.
    """
    text = re.sub(r"\s+", " ", str(repeat or "").strip().lower())
    if text in _NONE:
        return None
    if text.upper().startswith(("RRULE:", "FREQ=")):
        rule = text.upper().removeprefix("RRULE:")
        if not re.fullmatch(r"FREQ=(DAILY|WEEKLY|MONTHLY|YEARLY)(;[A-Z]+=[A-Z0-9,+-]+)*", rule):
            raise ChoreError(f"Not a repeat rule Skylight understands: {repeat!r}")
        return f"RRULE:{rule}"
    if text in _WORDS:
        return f"RRULE:{_WORDS[text]}"
    if text in ("weekly", "every week"):
        day = _ORDER[(start or date_cls.today()).weekday()]
        return f"RRULE:FREQ=WEEKLY;BYDAY={day}"
    words = re.split(r"[\s,/&]+|\band\b", text.removeprefix("every ").removeprefix("on "))
    days = {_DAYS[w] for w in (w.strip() for w in words) if w in _DAYS}
    leftovers = [w for w in (w.strip() for w in words) if w and w not in _DAYS and w not in ("every", "on")]
    if days and not leftovers:
        return "RRULE:FREQ=WEEKLY;BYDAY=" + ",".join(d for d in _ORDER if d in days)
    raise ChoreError(f"I don't know how to repeat a chore {repeat!r}; try daily, weekdays, weekly or days like 'mon, wed'")


def clock(value: str | None) -> str | None:
    """A time of day as Skylight's ``"HH:MM"`` ("7pm", "7:30 am", "19:05"), None when blank."""
    text = str(value or "").strip().lower().replace(".", "")
    if not text:
        return None
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", text)
    if not m:
        raise ChoreError(f"Not a time of day: {value!r}")
    hour, minute, half = int(m.group(1)), int(m.group(2) or 0), m.group(3)
    if half:
        if not 1 <= hour <= 12:
            raise ChoreError(f"Not a time of day: {value!r}")
        hour = hour % 12 + (12 if half == "pm" else 0)
    if hour > 23 or minute > 59:
        raise ChoreError(f"Not a time of day: {value!r}")
    return f"{hour:02d}:{minute:02d}"


def day(value: str | None, today: date_cls | None = None) -> date_cls:
    """A start day: YYYY-MM-DD, "today" or "tomorrow" (default today)."""
    today = today or date_cls.today()
    text = str(value or "").strip().lower()
    if text in ("", "today"):
        return today
    if text == "tomorrow":
        return date_cls.fromordinal(today.toordinal() + 1)
    try:
        return date_cls.fromisoformat(text)
    except ValueError:
        raise ChoreError(f"Not a date: {value!r} (use YYYY-MM-DD, today or tomorrow)") from None


def members_from(result: Any) -> list[dict]:
    """The frame's chore-chart members from ``GET /categories``."""
    out = []
    for raw in (result or {}).get("data", []) if isinstance(result, dict) else []:
        attrs = raw.get("attributes", {}) or {}
        label = str(attrs.get("label") or "").strip()
        if not label or raw.get("id") is None:
            continue
        # Calendar-only categories (no profile, not on the chore chart) are not people.
        if attrs.get("selected_for_chore_chart") is False and attrs.get("linked_to_profile") is False:
            continue
        out.append({"id": str(raw["id"]), "label": label, "color": attrs.get("color") or None})
    return out


def same_person(name: str, label: str) -> bool:
    """The chore list's matching rule: case-insensitive, either name inside the other."""
    a, b = name.strip().lower(), label.strip().lower()
    return bool(a and b) and (a == b or a in b or b in a)


def member_ids(names: list[str], members: list[dict]) -> list[str]:
    """Category ids for ``names`` (login names or Skylight labels); unknown names raise."""
    ids, unknown = [], []
    for name in names:
        exact = [m for m in members if m["label"].lower() == name.strip().lower()]
        found = exact or [m for m in members if same_person(name, m["label"])]
        if len(found) != 1:
            unknown.append(name)
            continue
        if found[0]["id"] not in ids:
            ids.append(found[0]["id"])
    if unknown:
        labels = ", ".join(m["label"] for m in members) or "nobody"
        raise ChoreError(f"No one on the Skylight chore chart is called {', '.join(unknown)} (it has {labels})")
    return ids


def create_requests(spec: dict, members: list[dict], today: date_cls | None = None) -> list[tuple[str, dict]]:
    """The Skylight calls that create the chore ``spec`` describes.

    ``spec``: ``title``, ``assignees`` (names), ``date``, ``time``, ``repeat``,
    ``until``, ``stars``, ``emoji``, ``note``. A repeating chore is one
    ``create_multiple`` for everyone it is for; a one-off is one ``POST
    /chores`` per person (the documented single-chore call).
    """
    title = re.sub(r"\s+", " ", str(spec.get("title") or "")).strip()[:MAX_TITLE]
    if not title:
        raise ChoreError("A chore needs a name")
    names = [str(n) for n in (spec.get("assignees") or []) if str(n).strip()]
    if not names:
        raise ChoreError("Who is the chore for?")
    ids = member_ids(names, members)
    start = day(spec.get("date"), today)
    rule = repeat_rule(spec.get("repeat"), start)
    common: dict[str, Any] = {"summary": title, "start": start.isoformat()}
    if (t := clock(spec.get("time"))):
        common["start_time"] = t
    if spec.get("stars") not in (None, ""):
        try:
            stars = int(spec["stars"])
        except (TypeError, ValueError):
            raise ChoreError("Stars must be a whole number") from None
        if not 0 <= stars <= 100:
            raise ChoreError("Stars must be between 0 and 100")
        common["reward_points"] = stars
    if (emoji := str(spec.get("emoji") or "").strip()[:8]):
        common["emoji_icon"] = emoji
    if (note := str(spec.get("note") or "").strip()[:500]):
        common["description"] = note
    if rule:
        body = {**common, "category_ids": ids, "recurrence_set": [rule]}
        if spec.get("until"):
            until = day(spec["until"], today)
            if until < start:
                raise ChoreError("The chore stops before it starts")
            body["recurring_until"] = until.isoformat()
        return [("/chores/create_multiple", body)]
    return [("/chores", {**common, "category_id": cid}) for cid in ids]


def update_body(spec: dict, members: list[dict], today: date_cls | None = None) -> dict:
    """``PUT /chores/{id}`` body for the fields ``spec`` sets (others are left alone)."""
    body: dict[str, Any] = {}
    if spec.get("title") is not None:
        title = re.sub(r"\s+", " ", str(spec["title"])).strip()[:MAX_TITLE]
        if not title:
            raise ChoreError("A chore needs a name")
        body["summary"] = title
    if spec.get("assignees"):
        ids = member_ids([str(n) for n in spec["assignees"]], members)
        if len(ids) != 1:
            raise ChoreError("A chore can be moved to one person at a time")
        body["category_id"] = ids[0]
    if spec.get("date"):
        body["start"] = day(spec["date"], today).isoformat()
    if spec.get("time") is not None:
        body["start_time"] = clock(spec["time"])
    if spec.get("repeat") is not None:
        rule = repeat_rule(spec["repeat"], day(spec.get("date"), today))
        body["recurrence_set"] = [rule] if rule else []
    if spec.get("stars") not in (None, ""):
        try:
            body["reward_points"] = max(0, min(100, int(spec["stars"])))
        except (TypeError, ValueError):
            raise ChoreError("Stars must be a whole number") from None
    if spec.get("emoji") is not None:
        body["emoji_icon"] = str(spec["emoji"]).strip()[:8] or None
    if spec.get("note") is not None:
        body["description"] = str(spec["note"]).strip()[:500]
    if not body:
        raise ChoreError("Nothing to change")
    apply_to = spec.get("apply_to")
    if apply_to is not None:
        if apply_to not in APPLY_TO:
            raise ChoreError("apply_to must be one or all")
        body["apply_to"] = apply_to
    return body


def _check_id(chore_id: str) -> str:
    cid = str(chore_id or "").strip()
    if not re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z-]{0,80}", cid):
        raise ChoreError("Not a chore id")
    return cid


# --------------------------------------------------------------------------
# Calls (``session`` and ``call`` come from execution's Skylight client)
# --------------------------------------------------------------------------


async def list_members(session: dict, call: Call) -> list[dict] | None:
    result = await call(session, "GET", "/categories")
    return None if result is None else members_from(result)


async def create(session: dict, call: Call, spec: dict, *, members: list[dict] | None = None) -> list[dict]:
    """Create the chore; returns what Skylight sent back (one entry per call)."""
    members = members if members is not None else await list_members(session, call)
    if members is None:
        raise ChoreError("Could not read the Skylight chore chart")
    made = []
    for path, body in create_requests(spec, members):
        result = await call(session, "POST", path, body)
        if result is None:
            if made:
                raise ChoreError(f"Skylight added the chore for some people but not all ({len(made)} done)")
            raise ChoreError("Skylight did not accept the chore")
        data = result.get("data") if isinstance(result, dict) else None
        made.extend(data if isinstance(data, list) else [data] if isinstance(data, dict) else [{}])
    return made


async def update(session: dict, call: Call, chore_id: str, spec: dict) -> Any:
    members = await list_members(session, call) if spec.get("assignees") else []
    if members is None:
        raise ChoreError("Could not read the Skylight chore chart")
    body = update_body(spec, members)
    result = await call(session, "PUT", f"/chores/{_check_id(chore_id)}", body)
    if result is None:
        raise ChoreError("Skylight did not accept the change")
    return result


async def delete(session: dict, call: Call, chore_id: str, apply_to: str | None = None) -> None:
    """Remove a chore: ``one`` (that day of a repeating chore) or ``all`` of it."""
    if apply_to is not None and apply_to not in APPLY_TO:
        raise ChoreError("apply_to must be one or all")
    params = {"apply_to": apply_to} if apply_to else None
    if await call(session, "DELETE", f"/chores/{_check_id(chore_id)}", params=params) is None:
        raise ChoreError("Skylight did not remove the chore")

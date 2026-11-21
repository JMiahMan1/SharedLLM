"""Garden actions any Jarvis surface can take, implemented once.

FastPath (Tier 1 voice), the assistant routes and the Librarian tools all do the
same three things: find the plant a person named, do it, and say what actually
happened. The finding is ``services/garden``'s ``GET /plants/resolve`` (personal
name, species, or room); everything else is an ordinary Garden call made with the
acting person's own identity headers, so nothing here can see or change a plant
that person could not see in the UI.

Nothing is guessed: an ambiguous name comes back with the candidates for the
caller to read out, and a missing plant is said plainly.
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

import aiohttp

log = logging.getLogger("gateway.garden_assist")

#: ``call(method, path, params=..., json=...)`` -> the Garden service's JSON.
GardenCall = Callable[..., Awaitable[dict]]

_TIMEOUT = aiohttp.ClientTimeout(total=20)


def seg(value: Any) -> str:
    """One URL path segment from a caller's value, percent-encoded entirely.

    Plant, room, harvest and plan ids reach Garden paths from client input (a
    push button's params, a route's path parameter). Encoded with ``safe=""`` a
    ``/``, ``?`` or ``#`` cannot add a segment, a query or a fragment, so
    "../hooks/weather#" stays one (unknown) id instead of reaching the weather
    hook with the internal secret.
    """
    text = str(value if value is not None else "")
    if not text or text in (".", ".."):
        raise ValueError(f"not a usable id: {text!r}")
    return quote(text, safe="")


def checked_path(path: str) -> str:
    """Refuse a Garden path that could leave its route: ``..``, ``?``, ``#``, ``\\``.

    Every Garden call carries ``X-Internal-Secret``; a path built from client
    input must never reach a different route than the one the code named.
    Queries go in ``params``, never in the path.
    """
    if not path.startswith("/") or any(bad in path for bad in ("?", "#", "\\")):
        raise ValueError(f"refusing Garden path {path!r}")
    if any(part in (".", "..") for part in path.split("/")):
        raise ValueError(f"refusing Garden path {path!r}")
    return path


def garden_headers(creds: Any) -> dict[str, str]:
    """The internal secret plus the acting person's verified identity."""
    from services.gateway.config import INTERNAL_SECRET

    data = creds if isinstance(creds, dict) else (getattr(creds, "model_dump", lambda: {})() or {})
    user = str(data.get("user") or "").strip().lower()
    headers = {"X-Internal-Secret": INTERNAL_SECRET, "X-User-Id": user}
    role = str(data.get("family_role") or "").strip().lower()
    if role:
        headers["X-User-Role"] = role
    if data.get("is_admin"):
        headers["X-Is-Admin"] = "true"
    if role in ("mother", "father"):
        headers["X-Family-Manage"] = "true"
    return headers


async def call_garden(
    method: str,
    path: str,
    *,
    creds: Any,
    params: dict | None = None,
    json: Any = None,
) -> dict:
    """One Garden service call as ``creds``; raises on transport failure."""
    from services.gateway.config import GARDEN_SVC
    from services.gateway.main import shared_http_client

    async with shared_http_client() as client, client.request(
        method,
        f"{GARDEN_SVC.rstrip('/')}{checked_path(path)}",
        params=params,
        json=json,
        headers=garden_headers(creds),
        timeout=_TIMEOUT,
    ) as resp:
        try:
            body = await resp.json(content_type=None)
        except Exception:
            body = None
        if not isinstance(body, dict):
            body = {}
        if resp.status >= 400:
            detail = body.get("detail") or f"the garden service answered HTTP {resp.status}"
            return {"__error__": str(detail), "__status__": resp.status, **body}
        return body


def due_phrase(next_due_at: str | None) -> str:
    """``"today"``, ``"in 3 days"``, ``"2 days ago"`` -- from a due timestamp.

    Days are the household's (``services/common/household_tz``): in UTC an
    evening due time read as "tomorrow". Without a configured zone this says
    so instead of counting in UTC.
    """
    if not next_due_at:
        return ""
    try:
        when = datetime.fromisoformat(str(next_due_at).replace("Z", "+00:00"))
    except ValueError:
        return ""
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    from services.common import household_tz

    try:
        zone = household_tz.zoneinfo()
    except household_tz.HouseholdTimeZoneError as exc:
        log.error("[Garden] cannot say when care is due: %s", exc)
        return "at a time I can't name (the household time zone is not set)"
    days = (when.astimezone(zone).date() - datetime.now(zone).date()).days
    if days == 0:
        return "today"
    if days == 1:
        return "tomorrow"
    if days > 1:
        return f"in {days} days"
    return f"{-days} day{'s' if days < -1 else ''} ago"


def plant_label(plant: dict) -> str:
    """What to call a plant out loud: the family's own name, else the species."""
    return str(plant.get("nickname") or plant.get("common_name") or plant.get("species_ref") or "that plant")


async def resolve_target(
    creds: Any,
    target: str,
    *,
    whole_phrase: bool = False,
    call: GardenCall = call_garden,
) -> dict:
    """``GET /plants/resolve`` for a spoken or typed name.

    ``whole_phrase`` asks the Garden to match only names the phrase is wholly
    made of ("bedroom", "monstera"), never a name buried inside a longer phrase
    ("the bedroom at 6"): voice sentences must not reach a plant by accident.
    """
    params = {"q": target}
    if whole_phrase:
        params["whole"] = "true"
    return await call("GET", "/plants/resolve", creds=creds, params=params)


#: Said when the Garden cannot be reached.
UNAVAILABLE_MESSAGE = "I couldn't reach the garden just now."


async def _resolve_or_report(
    creds: Any, target: str, *, whole_phrase: bool, call: GardenCall
) -> tuple[dict, dict | None]:
    """``(resolved, None)`` or ``({}, failure answer)``; a failure is never a match."""
    try:
        resolved = await resolve_target(creds, target, whole_phrase=whole_phrase, call=call)
    except Exception as exc:
        log.warning("[Garden] resolve '%s' failed: %s: %s", target, type(exc).__name__, exc)
        return {}, {"status": "unavailable", "message": UNAVAILABLE_MESSAGE}
    if resolved.get("__error__"):
        log.warning("[Garden] resolve '%s' answered %s: %s", target, resolved.get("__status__"), resolved["__error__"])
        return {}, {"status": "error", "message": f"The garden said: {resolved['__error__']}"}
    if resolved.get("match_type") not in ("plant", "location", "ambiguous", "none"):
        log.error("[Garden] resolve '%s' gave no match_type: %r", target, resolved)
        return {}, {"status": "error", "message": "The garden gave an answer I could not read."}
    return resolved, None


def _names(plants: list[dict]) -> str:
    labels = [plant_label(p) for p in plants]
    if len(labels) <= 1:
        return "".join(labels)
    return f"{', '.join(labels[:-1])} or {labels[-1]}"


def ambiguous_message(resolved: dict) -> str:
    """Ask which one, naming them -- never pick for the person."""
    plants = resolved.get("plants") or []
    rooms = resolved.get("locations") or []
    if plants:
        return f"Which plant did you mean: {_names(plants)}?"
    if rooms:
        names = [str(room.get("name")) for room in rooms]
        return f"Which room did you mean: {', '.join(names)}?"
    return "I couldn't tell which plant you meant."


async def log_care(
    creds: Any,
    target: str,
    *,
    action: str = "water",
    still_damp: bool = False,
    source: str = "voice",
    postpone_days: float | None = None,
    whole_phrase: bool = False,
    call: GardenCall = call_garden,
) -> dict:
    """Water / fertilize / mist / snooze whatever ``target`` names.

    Returns ``{"status", "message", ...}`` where status is one of ``logged``,
    ``snoozed``, ``ambiguous``, ``not_found``, ``unavailable`` or ``error``.
    """
    resolved, failure = await _resolve_or_report(creds, target, whole_phrase=whole_phrase, call=call)
    if failure is not None:
        return failure

    match = resolved.get("match_type")
    if match == "none":
        return {"status": "not_found", "message": f"I couldn't find a plant or room called {target}."}
    if match == "ambiguous":
        return {
            "status": "ambiguous",
            "message": ambiguous_message(resolved),
            "candidates": resolved.get("plants") or resolved.get("locations") or [],
        }

    if match == "location":
        location = resolved.get("location") or {}
        if action != "water" or still_damp:
            return {
                "status": "error",
                "message": f"I can only water a whole room, not {action} it. Name the plant instead.",
            }
        answer = await call(
            "POST",
            f"/locations/{seg(location.get('location_id'))}/water-all",
            creds=creds,
            params={"source": source},
        )
        if answer.get("__error__"):
            return {"status": "error", "message": f"The garden said: {answer['__error__']}"}
        count = int(answer.get("watered_count") or 0)
        name = location.get("name") or "that room"
        if count == 0:
            return {"status": "logged", "message": f"Nothing in {name} needed water just now.", "location": location}
        return {
            "status": "logged",
            "message": f"Watered {count} plant{'s' if count != 1 else ''} in {name}.",
            "location": location,
            "count": count,
        }

    plant = (resolved.get("plants") or [{}])[0]
    plant_id = plant.get("plant_id")
    label = plant_label(plant)
    if still_damp:
        params = {"still_damp": "true"}
        if postpone_days is not None:
            params["postpone_days"] = str(postpone_days)
        answer = await call("POST", f"/plants/{seg(plant_id)}/snooze", creds=creds, params=params)
        if answer.get("__error__"):
            return {"status": "error", "message": f"The garden said: {answer['__error__']}"}
        when = due_phrase(answer.get("next_due_at"))
        return {
            "status": "snoozed",
            "message": f"Okay, {label} gets until {when}." if when else f"Okay, {label} is snoozed.",
            "plant": plant,
            "next_due_at": answer.get("next_due_at"),
        }

    answer = await call(
        "POST",
        f"/plants/{seg(plant_id)}/care",
        creds=creds,
        json={"action": action, "source": source},
    )
    if answer.get("__error__"):
        return {"status": "error", "message": f"The garden said: {answer['__error__']}"}
    verb = {
        "water": "watering",
        "fertilize": "fertilizing",
        "mist": "misting",
        "prune_pinch": "pruning",
        "clean_leaves": "cleaning the leaves of",
        "rotate": "rotating",
        "check_pests": "checking for pests on",
    }.get(action, action.replace("_", " "))
    when = due_phrase(answer.get("next_due_at"))
    next_word = "watering" if action == "water" else action.replace("_", " ")
    tail = f" Next {next_word} due {when}." if when else ""
    return {
        "status": "logged",
        "message": f"Logged {verb} for {label}.{tail}",
        "plant": plant,
        "next_due_at": answer.get("next_due_at"),
    }


async def log_harvest(
    creds: Any,
    target: str,
    *,
    quantity: float,
    unit: str = "lb",
    disposition: str = "fresh",
    notes: str | None = None,
    whole_phrase: bool = False,
    call: GardenCall = call_garden,
) -> dict:
    """Record a harvest against whatever ``target`` names (open to every role)."""
    resolved, failure = await _resolve_or_report(creds, target, whole_phrase=whole_phrase, call=call)
    if failure is not None:
        return failure

    match = resolved.get("match_type")
    if match == "none":
        return {"status": "not_found", "message": f"I couldn't find a plant called {target}."}
    if match == "ambiguous":
        return {
            "status": "ambiguous",
            "message": ambiguous_message(resolved),
            "candidates": resolved.get("plants") or [],
        }
    if match == "location":
        return {
            "status": "error",
            "message": "A harvest belongs to one plant; tell me which plant you picked.",
        }

    plant = (resolved.get("plants") or [{}])[0]
    label = plant_label(plant)
    answer = await call(
        "POST",
        f"/plants/{seg(plant.get('plant_id'))}/harvest",
        creds=creds,
        json={
            "quantity": quantity,
            "unit": unit,
            "disposition": disposition,
            "notes": notes,
        },
    )
    if answer.get("__error__"):
        return {"status": "error", "message": f"The garden said: {answer['__error__']}"}
    amount = f"{quantity:g} {unit}" if unit else f"{quantity:g}"
    kept = "" if disposition in ("", "fresh") else f" for {disposition}"
    return {
        "status": "logged",
        "message": f"Logged {amount} of {label}{kept}.",
        "plant": plant,
        "harvest": answer,
    }


async def summarize(creds: Any, *, call: GardenCall = call_garden) -> dict:
    """Today's care, harvest-ready plants and pantry progress, in one answer.

    A failed read is never summarised as "nothing needs care": the answer says
    which part could not be read (``status`` ``error``), or that the garden is
    unreachable (``status`` ``unavailable``).
    """
    try:
        today = await call("GET", "/today", creds=creds)
        ready = await call("GET", "/harvest/ready", creds=creds)
        pantry = await call("GET", "/pantry-batches", creds=creds)
    except Exception as exc:
        log.warning("[Garden] summary failed: %s: %s", type(exc).__name__, exc)
        return {"status": "unavailable", "message": UNAVAILABLE_MESSAGE}
    failed = [
        (label, answer)
        for label, answer in (("today's care", today), ("the harvest list", ready), ("the pantry", pantry))
        if answer.get("__error__")
    ]
    if failed:
        label, answer = failed[0]
        log.warning("[Garden] summary: %s answered %s: %s", label, answer.get("__status__"), answer["__error__"])
        return {
            "status": "error",
            "message": f"I couldn't read {label} from the garden: {answer['__error__']}",
        }

    due = int(today.get("due_count") or 0)
    overdue = int(today.get("overdue_count") or 0)
    tasks: list[dict] = []
    for room, items in (today.get("by_room") or {}).items():
        for item in items or []:
            if not isinstance(item, dict):
                continue
            tasks.append({
                "room": room,
                "plant_id": item.get("plant_id"),
                "nickname": item.get("nickname"),
                "common_name": item.get("common_name"),
                "action": item.get("action"),
                "next_due_at": item.get("next_due_at"),
            })
    ready_items = [
        {
            "plant_id": item.get("plant_id"),
            "nickname": item.get("nickname"),
            "common_name": item.get("common_name"),
            "in_cooldown": bool(item.get("in_cooldown")),
        }
        for item in (ready.get("ready") or [])
        if isinstance(item, dict)
    ]
    goals = [
        {
            "item": goal.get("item"),
            "qty_put_up": goal.get("qty_put_up"),
            "target_qty": goal.get("target_qty"),
            "unit": goal.get("unit"),
            "percent_complete": goal.get("percent_complete"),
        }
        for goal in (pantry.get("goal_progress") or [])
        if isinstance(goal, dict)
    ]
    weather = today.get("weather") or {}

    if not tasks:
        spoken = "Nothing in the garden needs care today."
    else:
        names = [
            str(task.get("nickname") or task.get("common_name") or "a plant")
            for task in tasks[:6]
        ]
        listed = ", ".join(names[:-1]) + (f" and {names[-1]}" if len(names) > 1 else names[0])
        spoken = f"{due} plant{'s' if due != 1 else ''} need care today: {listed}."
        if overdue:
            spoken += f" {overdue} of them {'are' if overdue != 1 else 'is'} overdue."
    if ready_items:
        spoken += f" {len(ready_items)} ready to harvest."
    if goals:
        goal = goals[0]
        spoken += f" Pantry: {goal.get('item')} is {goal.get('qty_put_up')} of {goal.get('target_qty')} {goal.get('unit')}."
    frost = (weather or {}).get("frost_alerts") or []
    if frost:
        # One alert can cover several plants; say how many plants, not how many
        # alerts ("Frost alert for 1 plant" when six are at risk reads wrong).
        at_risk: set[str] = set()
        for alert in frost:
            names = (alert or {}).get("plants") or []
            if names:
                at_risk.update(str(name) for name in names)
        count = len(at_risk) or len(frost)
        spoken += f" Frost alert for {count} plant{'s' if count != 1 else ''}."
    rain_skipped = (weather or {}).get("rain_skipped") or []
    if rain_skipped:
        spoken += f" Rain skipped watering for {len(rain_skipped)}."

    return {
        "status": "ok",
        "message": spoken,
        "due_count": due,
        "overdue_count": overdue,
        "tasks": tasks,
        "ready": ready_items,
        "pantry_goals": goals,
        "weather": weather or None,
    }

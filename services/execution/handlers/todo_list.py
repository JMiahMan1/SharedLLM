"""The home's to-do lists from the assistant: "add milk to the shopping list", "what is on my reminders".

The lists are homehub's ``todo.*`` entities (HA's ``local_todo`` and
``shopping_list``, services/homehub/drivers/todo.py), reached with HA's own
services through the caller's Home Assistant credentials: ``todo.get_items``,
``todo.add_item``, ``todo.update_item`` and ``todo.remove_item``.

Who may use a list (``list_visible``, shared with the Lists page's gateway
routes in services/gateway/todo_routes.py): a to-do list is household data,
like the whole-house energy totals, so every signed-in member reads and writes
it without a device assignment. Protection still wins: a list an admin has
locked is only for the people on its permit list, and does not exist for
anyone else.

Nothing here knows a list's name: the lists are whatever the hub has, matched
by the words the person used. With one list the name may be left out; with
more, and no name, the reply asks which one instead of guessing.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from services.execution import ha_client, hardware_router
from services.execution.entity_access import EntityPermissions, load_entity_permissions
from services.execution.schemas import ExecutionResult, TodoListRequest

log = logging.getLogger("execution.todo_list")

SERVICE = "todo_list"
NEEDS_ACTION, COMPLETED = "needs_action", "completed"
#: HA's TodoListEntityFeature bits.
FEATURES = {"create": 1, "delete": 2, "update": 4, "move": 8, "due_date": 16, "due_datetime": 32, "description": 64}
_FILLER = re.compile(r"\b(the|my|our|a|list|to-?do|todo)\b")


def list_visible(perms: EntityPermissions | None, entity_id: str) -> bool:
    """Household lists are everyone's; a locked list is only for its permit list."""
    if not entity_id.startswith("todo."):
        return False
    if perms is None or perms.unrestricted:
        return True
    if entity_id in perms.protected:
        return entity_id in perms.permitted
    return True


def features_of(supported: Any) -> dict[str, bool]:
    try:
        bits = int(supported or 0)
    except (TypeError, ValueError):
        bits = 0
    return {name: bool(bits & bit) for name, bit in FEATURES.items()}


def lists_from_states(states: Any, perms: EntityPermissions | None) -> list[dict[str, Any]]:
    """Every to-do list in ``/api/states`` the caller may use, by name."""
    out = []
    for st in states if isinstance(states, list) else []:
        eid = str((st or {}).get("entity_id") or "")
        if not list_visible(perms, eid):
            continue
        attrs = st.get("attributes") or {}
        try:
            open_count = int(st.get("state"))
        except (TypeError, ValueError):
            open_count = None
        out.append({"entity_id": eid, "name": str(attrs.get("friendly_name") or eid.split(".", 1)[1].replace("_", " ")),
                    "open": open_count, "available": st.get("state") not in ("unavailable", "unknown"),
                    "features": features_of(attrs.get("supported_features"))})
    out.sort(key=lambda x: x["name"].lower())
    return out


def _norm(text: str) -> str:
    text = re.sub(r"[^a-z0-9 ]+", " ", str(text or "").lower().replace("_", " "))
    return " ".join(_FILLER.sub(" ", text).split())


def resolve_list(lists: list[dict[str, Any]], name: str | None) -> tuple[dict[str, Any] | None, str | None]:
    """The list ``name`` means, or why it cannot be told (no guessing between several)."""
    if not lists:
        return None, "There are no to-do lists in the home yet."
    want = _norm(name or "")
    if not want:
        if len(lists) == 1:
            return lists[0], None
        return None, "Which list? The home has " + _join([x["name"] for x in lists]) + "."
    keys = [(x, _norm(x["name"]), _norm(x["entity_id"].split(".", 1)[1])) for x in lists]
    for matcher in (lambda a, b: a == b, lambda a, b: bool(a) and (a in b or b in a)):
        found = [x for x, n, s in keys if matcher(want, n) or matcher(want, s)]
        if len(found) == 1:
            return found[0], None
        if len(found) > 1:
            return None, f"More than one list matches '{name}': " + _join([x["name"] for x in found]) + ". Which one?"
    return None, f"There is no list called '{name}'. The home has " + _join([x["name"] for x in lists]) + "."


def resolve_item(items: list[dict[str, Any]], words: str) -> tuple[dict[str, Any] | None, str | None]:
    want = " ".join(str(words or "").lower().split())
    if not want:
        return None, "Which item?"
    exact = [i for i in items if " ".join(str(i.get("summary") or "").lower().split()) == want]
    if len(exact) == 1:
        return exact[0], None
    near = exact or [i for i in items if want in str(i.get("summary") or "").lower()]
    if len(near) == 1:
        return near[0], None
    if near:
        return None, f"More than one item matches '{words}': " + _join([str(i.get('summary')) for i in near]) + "."
    return None, f"'{words}' is not on that list."


def _join(names: list[str]) -> str:
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def _fail(message: str, **detail: Any) -> ExecutionResult:
    return ExecutionResult(status="FAILURE", message=message, service=SERVICE, detail=detail or None)


async def _items(ctx: Any, entity_id: str) -> list[dict[str, Any]]:
    res = await ha_client.call_service(ctx.ha_url or "", ctx.ha_token or "", "todo", "get_items",
                                       entity_id=entity_id, return_response=True)
    if not res.get("ok"):
        raise RuntimeError(str(res.get("error") or "the home hub did not answer"))
    raw = res.get("service_response") or {}
    body = raw.get("service_response", raw) if isinstance(raw, dict) else {}
    items = ((body or {}).get(entity_id) or {}).get("items")
    if not isinstance(items, list):
        raise RuntimeError("the home hub sent no items")
    return [i for i in items if isinstance(i, dict)]


async def _run(ctx: Any, service: str, entity_id: str, data: dict[str, Any]) -> None:
    res = await ha_client.call_service(ctx.ha_url or "", ctx.ha_token or "", "todo", service,
                                       entity_id=entity_id, service_data=data)
    if not res.get("ok"):
        raise RuntimeError(str(res.get("error") or "the home hub did not answer"))


async def handle_todo_list(req: TodoListRequest) -> ExecutionResult:
    # The caller's own hub login, else the hub's: the lists are filtered by the
    # caller's entity permissions below, so the hub's login widens nothing.
    ctx = hardware_router.with_hub_login(req.user_context)
    if ctx is None:
        return _fail(hardware_router.NO_HUB_MESSAGE)
    try:
        states = await ha_client.get_states(ctx.ha_url, ctx.ha_token, account=ctx.user, strict=True)
    except Exception as exc:
        log.warning("[todo_list] reading the lists for %s failed: %s", ctx.user, type(exc).__name__)
        return _fail(f"I could not reach the home hub to read the lists ({type(exc).__name__}).")
    perms = await load_entity_permissions(ctx.user, bool(ctx.is_admin))
    lists = lists_from_states(states, perms)

    if req.action == "lists":
        if not lists:
            return _fail("There are no to-do lists in the home yet.")
        return ExecutionResult.ok("The lists are " + _join([x["name"] for x in lists]) + ".", SERVICE,
                                  {"lists": lists})

    target, why = resolve_list(lists, req.list_name)
    if target is None:
        return _fail(why or "Which list?", lists=[x["name"] for x in lists])
    eid, name = target["entity_id"], target["name"]

    try:
        if req.action == "add":
            words = [w.strip() for w in ([req.item] if req.item else []) + list(req.items or []) if w and w.strip()]
            if not words:
                return _fail(f"What should I add to {name}?")
            for word in words:
                await _run(ctx, "add_item", eid, {"item": word})
            return ExecutionResult.ok(f"Added {_join(words)} to {name}.", SERVICE,
                                      {"list": eid, "added": words})

        items = await _items(ctx, eid)
        if req.action == "list":
            todo = [i for i in items if i.get("status") != COMPLETED]
            done = [i for i in items if i.get("status") == COMPLETED]
            shown = todo + (done if req.include_completed else [])
            if not todo:
                message = f"{name} is empty." if not done else f"Everything on {name} is done."
            else:
                message = f"{name} has {len(todo)} to do: " + _join([str(i.get('summary')) for i in todo]) + "."
            if req.include_completed and done:
                message += " Done: " + _join([str(i.get("summary")) for i in done]) + "."
            return ExecutionResult.ok(message, SERVICE, {"list": eid, "items": shown})

        pool = {
            "complete": [i for i in items if i.get("status") != COMPLETED],
            "uncomplete": [i for i in items if i.get("status") == COMPLETED],
            "remove": items,
        }[req.action]
        item, why = resolve_item(pool, req.item or (req.items or [""])[0])
        if item is None:
            return _fail(why or "Which item?", list=eid)
        if req.action == "remove":
            await _run(ctx, "remove_item", eid, {"item": item["uid"]})
            return ExecutionResult.ok(f"Took {item['summary']} off {name}.", SERVICE, {"list": eid})
        status = COMPLETED if req.action == "complete" else NEEDS_ACTION
        await _run(ctx, "update_item", eid, {"item": item["uid"], "status": status})
        verb = "Ticked off" if status == COMPLETED else "Put back"
        return ExecutionResult.ok(f"{verb} {item['summary']} on {name}.", SERVICE, {"list": eid})
    except RuntimeError as exc:
        return _fail(f"The list could not be changed: {exc}", list=eid)

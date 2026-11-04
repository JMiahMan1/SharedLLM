"""Family chores on Skylight from the assistant (docs/ACHIEVEMENTS.md, "Managing chores").

"Jarvis, give Noah 'feed the chickens' every day at 7am", "take 'trash' off
Gracie's chores", "what are my chores today", "I fed the dog".

Who may do what:

* **list / complete / uncomplete** their own chores: anyone.
* **add** a chore for anyone: a parent (an admin, the father or the mother).
* **remove / update** a chore, or tick off someone else's: a parent, and not
  when recognised by voice alone. A voice can be imitated, and once chores
  earn screen time, "remove my chores" in a parent's voice is an unlock.

The Skylight calls are ``services/execution/skylight_chores.py`` and the
chore list is execution's own ``/api/integrations/skylight/chores``.
"""
from __future__ import annotations

import logging

import aiohttp

from services.execution import skylight_chores as chores
from services.execution.schemas import ChoreRequest, ExecutionResult

log = logging.getLogger("execution.chores")

SERVICE = "chores"
#: Family roles that run the chore chart, besides admins.
MANAGER_ROLES = frozenset({"father", "mother"})


async def family_role(user: str) -> str:
    """``user``'s family role from identity ("other" when it cannot be read)."""
    from services.common.http import get_client
    from services.config import IDENTITY_SVC_URL, INTERNAL_SECRET

    try:
        async with get_client().get(
            f"{IDENTITY_SVC_URL.rstrip('/')}/api/internal/family-role/{user}",
            headers={"X-Internal-Secret": INTERNAL_SECRET}, timeout=aiohttp.ClientTimeout(total=5),
        ) as resp:
            if resp.status == 200:
                return str(((await resp.json(content_type=None)) or {}).get("family_role") or "other")
    except Exception as e:
        log.warning(f"[chores] family role lookup for {user} failed: {type(e).__name__}: {e}")
    return "other"


async def is_parent(req: ChoreRequest) -> bool:
    ctx = req.user_context
    return bool(ctx.is_admin) or await family_role(ctx.user) in MANAGER_ROLES


def _names(req: ChoreRequest) -> list[str]:
    names = [n for n in (req.assignees or []) if str(n).strip()]
    if req.assignee and req.assignee.strip():
        names.append(req.assignee.strip())
    return names


def _line(chore: dict) -> str:
    who = ", ".join(chore.get("assignees") or []) or "anyone"
    done = " (done)" if chore.get("completed") else ""
    at = f" at {chore['start_time']}" if chore.get("start_time") else ""
    return f"{chore.get('title') or 'Chore'} for {who}{at}{done}"


async def _today(req: ChoreRequest, user: str | None) -> list[dict] | None:
    """The day's chores (``req.date``, default today); ``user`` narrows to theirs."""
    from services.execution import main

    when = chores.day(req.date).isoformat()
    got = await main.get_skylight_chores(user=user, date=when, x_internal_secret=None)
    if got.get("status") != "SUCCESS":
        return None
    return list(got.get("chores") or [])


def _matching(items: list[dict], title: str | None, names: list[str]) -> list[dict]:
    wanted = (title or "").strip().lower()
    out = []
    for c in items:
        name = str(c.get("title") or "").lower()
        if wanted and wanted not in name and name not in wanted:
            continue
        if names and not any(chores.same_person(n, a) for n in names for a in c.get("assignees") or []):
            continue
        out.append(c)
    # An exact title beats partial ones ("trash" vs "trash and recycling").
    exact = [c for c in out if str(c.get("title") or "").lower() == wanted]
    return exact or out


async def _find(req: ChoreRequest, names: list[str]) -> tuple[dict | None, ExecutionResult | None]:
    """The one chore the request means, or the reply explaining why not."""
    if req.chore_id:
        return {"id": req.chore_id, "title": req.title or "that chore", "assignees": names}, None
    if not (req.title or "").strip():
        return None, ExecutionResult.fail("Which chore? Tell me its name.", SERVICE)
    items = await _today(req, None)
    if items is None:
        return None, ExecutionResult.fail("I couldn't read the Skylight chores right now.", SERVICE)
    found = _matching(items, req.title, names)
    if not found:
        whose = f" for {', '.join(names)}" if names else ""
        return None, ExecutionResult.fail(f"I couldn't find a chore called \"{req.title}\"{whose}.", SERVICE)
    if len(found) > 1:
        options = "; ".join(_line(c) for c in found[:6])
        return None, ExecutionResult.fail(f"More than one chore matches: {options}. Which one?", SERVICE,
                                          detail={"matches": found[:6]})
    return found[0], None


async def handle_chores(req: ChoreRequest) -> ExecutionResult:
    from services.execution import main

    ctx = req.user_context
    names = _names(req)
    by_voice = ctx.identified_by == "voice"
    try:
        if req.action == "list":
            parent = await is_parent(req)
            if names and not parent and not all(chores.same_person(ctx.user, n) for n in names):
                return ExecutionResult.fail("You can only see your own chores.", SERVICE)
            items = await _today(req, None if parent else ctx.user)
            if items is None:
                return ExecutionResult.fail("I couldn't read the Skylight chores right now.", SERVICE)
            if names:
                items = [c for c in items if any(chores.same_person(n, a) for n in names for a in c.get("assignees") or [])]
            if not items:
                return ExecutionResult.ok("No chores on the chart for then.", SERVICE, detail={"chores": []})
            left = sum(1 for c in items if not c.get("completed"))
            return ExecutionResult.ok(f"{left} of {len(items)} left: " + "; ".join(_line(c) for c in items) + ".",
                                      SERVICE, detail={"chores": items})

        if req.action == "add":
            if not await is_parent(req):
                return ExecutionResult.fail("Only a parent can add chores.", SERVICE)
            session = await main._get_skylight_session(ctx.user)
            if not session:
                return ExecutionResult.fail("Skylight isn't set up.", SERVICE)
            spec = {"title": req.title, "assignees": names, "date": req.date, "time": req.time,
                    "repeat": req.repeat, "until": req.until, "stars": req.stars, "emoji": req.emoji,
                    "note": req.note}
            await chores.create(session, main._skylight_request, spec)
            rule = chores.repeat_rule(req.repeat, chores.day(req.date))
            when = f", repeating {req.repeat}" if rule else ""
            return ExecutionResult.ok(f"Added \"{(req.title or '').strip()}\" for {', '.join(names)}{when}.", SERVICE)

        if req.action in ("remove", "update"):
            if not await is_parent(req):
                return ExecutionResult.fail("Only a parent can change or remove chores.", SERVICE)
            if by_voice:
                return ExecutionResult.fail(
                    "I can't change or remove chores from a voice alone. Use the app, or ask from your phone.", SERVICE)
            chore, problem = await _find(req, names)
            if problem:
                return problem
            session = await main._get_skylight_session(ctx.user)
            if not session:
                return ExecutionResult.fail("Skylight isn't set up.", SERVICE)
            repeating = bool(chore.get("recurrence")) or req.scope == "all"
            apply_to = (req.scope or "one") if repeating else None
            if req.action == "remove":
                await chores.delete(session, main._skylight_request, chore["id"], apply_to)
                span = " (every day of it)" if apply_to == "all" else " for that day" if apply_to == "one" else ""
                return ExecutionResult.ok(f"Removed \"{chore.get('title')}\"{span}.", SERVICE)
            spec = {"title": req.new_title, "date": None, "time": req.time, "repeat": req.repeat,
                    "stars": req.stars, "emoji": req.emoji, "note": req.note, "apply_to": apply_to}
            await chores.update(session, main._skylight_request, chore["id"], spec)
            return ExecutionResult.ok(f"Updated \"{chore.get('title')}\".", SERVICE)

        # complete / uncomplete
        mine = not names or all(chores.same_person(ctx.user, n) for n in names)
        if not mine and (by_voice or not await is_parent(req)):
            return ExecutionResult.fail("You can only tick off your own chores.", SERVICE)
        search = names or [ctx.user]
        chore, problem = await _find(req, search)
        if problem:
            return problem
        route = main.complete_skylight_chore if req.action == "complete" else main.uncomplete_skylight_chore
        done = await route(chore_id=chore["id"], user=ctx.user, x_internal_secret=None)
        if done.get("status") != "SUCCESS":
            return ExecutionResult.fail(f"Skylight didn't take that: {done.get('message')}.", SERVICE)
        verb = "Done" if req.action == "complete" else "Marked not done"
        return ExecutionResult.ok(f"{verb}: \"{chore.get('title')}\".", SERVICE)
    except chores.ChoreError as e:
        return ExecutionResult.fail(str(e), SERVICE)

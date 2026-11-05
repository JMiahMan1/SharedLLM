"""Family chores on Skylight from the assistant (docs/ACHIEVEMENTS.md, "Managing chores").

"Jarvis, give Noah 'feed the chickens' every day at 7am", "take 'trash' off
Gracie's chores", "what are my chores today", "I fed the dog".

Who may do what:

* **list / complete / uncomplete** their own chores: anyone.
* **add** a chore for anyone: a parent (an admin, the father or the mother).
* **remove / update** a chore, or tick off someone else's: a parent, and not
  when recognised by voice alone. A voice can be imitated, and once chores
  earn screen time, "remove my chores" in a parent's voice is an unlock.
* **checks / approve / redo** a ticked-off chore (earned screen time,
  docs/HEALTH_HUB.md, "Earning time and chores first"): the child's parents
  (identity decides). By voice alone too, since a speaker is where a parent
  sees the chore done, but then the child's parents are told it was approved
  by voice, so a mistaken or imitated voice never goes unnoticed. The most
  it can earn is the day's cap the parents set.

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


async def _identity(method: str, path: str, **kwargs) -> tuple[int, dict]:
    from services.common.http import get_client
    from services.config import IDENTITY_SVC_URL, INTERNAL_SECRET

    async with get_client().request(
        method, f"{IDENTITY_SVC_URL.rstrip('/')}{path}",
        headers={"X-Internal-Secret": INTERNAL_SECRET}, timeout=aiohttp.ClientTimeout(total=10), **kwargs,
    ) as resp:
        try:
            body = await resp.json(content_type=None)
        except Exception:
            body = {}
        return resp.status, body if isinstance(body, dict) else {}


def _check_line(check: dict) -> str:
    return f"{check.get('display_name') or check.get('username')}: {check.get('title') or 'a chore'}"


def _matching_checks(checks: list[dict], title: str | None, names: list[str]) -> list[dict]:
    wanted = (title or "").strip().lower()
    out = []
    for c in checks:
        name = str(c.get("title") or "").lower()
        if wanted and wanted not in name and name not in wanted:
            continue
        who = [str(c.get("username") or ""), str(c.get("display_name") or "")]
        if names and not any(chores.same_person(n, w) for n in names for w in who if w):
            continue
        out.append(c)
    exact = [c for c in out if str(c.get("title") or "").lower() == wanted]
    return exact or out


async def _answer_checks(req: ChoreRequest, names: list[str], by_voice: bool) -> ExecutionResult:
    """"<child> did the trash right", "<child> needs to redo the dishes", "what chores need checking?".

    Children are found by the login and display names identity sends with each
    check, so any family's names work; nothing here knows who lives in the house.
    """
    from services.common import notify
    from services.common.screen_time import chore_answer_words
    from services.execution import main

    ctx = req.user_context
    status, body = await _identity("GET", "/api/internal/chore-checks", params={"viewer": ctx.user, "status": "pending"})
    if status != 200:
        return ExecutionResult.fail("I couldn't reach the chore approvals right now.", SERVICE)
    waiting = list(body.get("checks") or [])
    if req.action == "checks":
        if names:
            waiting = _matching_checks(waiting, None, names)
        if not waiting:
            return ExecutionResult.ok("No chores are waiting for your OK.", SERVICE, detail={"checks": []})
        return ExecutionResult.ok(
            f"{len(waiting)} waiting for your OK: " + "; ".join(_check_line(c) for c in waiting) + ".",
            SERVICE, detail={"checks": waiting})

    if not waiting:
        return ExecutionResult.fail("No ticked-off chores are waiting for your OK.", SERVICE)
    found = _matching_checks(waiting, req.title, names)
    if not found:
        whose = f" from {', '.join(names)}" if names else ""
        what = f" called \"{req.title}\"" if req.title else ""
        return ExecutionResult.fail(f"No ticked-off chore{what}{whose} is waiting for your OK.", SERVICE)
    if len(found) > 1 and req.scope != "all":
        options = "; ".join(_check_line(c) for c in found[:6])
        return ExecutionResult.fail(f"More than one is waiting: {options}. Which one, or all of them?", SERVICE,
                                    detail={"matches": found[:6]})

    approve = req.action == "approve"
    done: list[dict] = []
    for check in found:
        code, answer = await _identity("POST", f"/api/internal/chore-checks/{check['id']}/decide",
                                       json={"by": ctx.user, "approve": approve})
        if code == 403:
            return ExecutionResult.fail("Only their parents can answer that.", SERVICE)
        if code != 200:
            continue  # already answered in the app a moment ago
        decided = answer.get("check") or {}
        child = str(decided.get("username") or "")
        if answer.get("untick") and decided.get("chore_id"):
            unticked = await main.uncomplete_skylight_chore(chore_id=decided["chore_id"], user=child, x_internal_secret=None)
            if unticked.get("status") != "SUCCESS":
                log.warning(f"[chores] could not untick {decided.get('chore_id')} for {child}: {unticked.get('message')}")
        heading, line = chore_answer_words(approve, decided.get("title"))
        try:
            await notify.send(notify.notice(child, "family_safety", heading, line, url="/fitness", tag=f"screen-time:{child}"))
            if by_voice and approve:
                # Approved from a voice alone: the parents hear, so a misheard or imitated voice shows.
                for parent in answer.get("parents") or []:
                    await notify.send(notify.notice(
                        parent, "family_safety", "Chore approved by voice",
                        f"\u201c{decided.get('title') or 'A chore'}\u201d for {answer.get('display_name') or child} "
                        f"was approved by voice as {ctx.user}.", url=f"/family/member/{child}", tag=f"screen-time:{child}"))
        except Exception as e:  # a missed notice never undoes the answer
            log.warning(f"[chores] notice after answering a chore failed: {type(e).__name__}: {e}")
        done.append({**check, **decided})

    if not done:
        return ExecutionResult.fail("That was already answered.", SERVICE)
    titles = ", ".join(f"{c.get('display_name') or c.get('username')}'s \"{c.get('title')}\"" for c in done)
    if approve:
        return ExecutionResult.ok(f"Approved {titles}. The minutes count now.", SERVICE, detail={"checks": done})
    return ExecutionResult.ok(f"Sent back {titles}. It's unticked and back on the list.", SERVICE, detail={"checks": done})


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

        if req.action in ("checks", "approve", "redo"):
            return await _answer_checks(req, names, by_voice)

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

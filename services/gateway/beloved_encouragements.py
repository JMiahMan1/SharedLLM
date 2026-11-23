"""A gentle daily encouragement inside Beloved, the couple's page.

Beloved is the married couple's end to end encrypted page inside the parents'
private chat (services/gateway/beloved.py). The server cannot read a single
record: it knows only the kind of a record, who wrote it and when, and a
metadata-only activity log of visits and reads. This module works from that
much and nothing more, exactly like the private chat's reply reminders
(services/gateway/private_chat_reminders.py).

For each enrolled couple (the two members of a private chat who pass
``private_chat.is_couple``: one father and one mother), and for each spouse who
has encouragements on, it sends at most one warm line per local day, inside that
spouse's chosen ``encourage_hour`` and never in the quiet hours (22:00 to
07:00). A ``once`` key per user per day means a restart never sends twice.

The choice is made from metadata only, in this order:

a. no heart or note from this spouse in two days or more;
b. the other spouse sent something in the last day that this one has not answered;
c. no visit (``page``) in three days or more;
d. otherwise a rotating feature tip, one per day, cycling a list of at least ten.

The notice is a ``nudge`` to the spouse only, titled ``Beloved`` and linking to
the page, exactly like ``beloved._tell_spouse``. It never names any content and
never mentions how long since they wrote.

``decide`` is a pure function (metadata, now, preferences in; message or None
out) so every rule is unit tested without a network.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from services.common import notify

log = logging.getLogger("gateway.beloved_encouragements")

#: How often the background worker looks.
CHECK_S = 300
#: The quiet hours: no encouragement inside them.
QUIET_START, QUIET_END = 22, 7
#: No heart or note from this spouse in this many days prompts rule a.
LONELY_DAYS = 2
#: No visit in this many days prompts rule c.
NO_VISIT_DAYS = 3
#: The other spouse's something in this many seconds prompts rule b (a day).
RECENT_S = 86400

#: Rule d's rotating feature tips, at least ten, warm and content-free.
TIPS = [
    "Plan an evening together in Just us",
    "Add a prayer request for each other",
    "Pick a date night idea for this week",
    "Leave a love note for the morning",
    "Answer today's question together",
    "Mark a day you want to remember",
    "Add your children's birthdays",
    "Change your cover photo",
    "Read today's devotion together",
    "Send a heart before bed",
]


def _env_flag(name: str, default: bool) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    raise ValueError(f"{name}={raw!r} is not on or off (use 1 or 0).")


#: The whole feature, on by default.
ENABLED = _env_flag("BELOVED_ENCOURAGEMENTS", True)


@dataclass(frozen=True)
class CoupleFacts:
    """Everything the decision needs, in metadata only: no content, ever.

    ``records`` is a list of ``(kind, by, at)`` for every sealed record of the
    couple (hearts, notes, prayers, ideas, days, plans, together and profile).
    ``activity`` is the couple's metadata activity log (page visits and reads).
    ``prefs`` is ``{user: {share_activity, encouragements, encourage_hour}}``.
    ``sent_today`` is the set of users already encouraged today.
    """

    records: list[tuple[str, str, float]]
    activity: list[dict[str, Any]]
    prefs: dict[str, dict[str, Any]]
    sent_today: set[str]


def in_quiet_hours(now: float, tz: ZoneInfo) -> bool:
    local = datetime.fromtimestamp(now, tz)
    return local.hour >= QUIET_START or local.hour < QUIET_END


def _in_encourage_hour(now: float, tz: ZoneInfo, hour: int) -> bool:
    """Whether ``now`` is inside the chosen hour (``hour``:00 to ``hour``:59), local time."""
    local = datetime.fromtimestamp(now, tz)
    if in_quiet_hours(now, tz):
        return False
    return local.hour == hour


def _last_at(facts: CoupleFacts, *, kind: str | None = None, user: str | None = None,
             what: str | None = None) -> float:
    """The newest matching timestamp, or 0 when there is none.

    Records and the activity log are searched separately so a record's time can
    never stand in for an activity entry (or the reverse).
    """
    best = 0.0
    if what is None:
        for k, by, at in facts.records:
            if (kind is None or k == kind) and (user is None or by == user) and isinstance(at, (int, float)):
                best = max(best, float(at))
    if kind is None:
        for entry in facts.activity:
            if what is not None and entry.get("what") != what:
                continue
            if user is not None and entry.get("who") != user:
                continue
            at = entry.get("at")
            if isinstance(at, (int, float)):
                best = max(best, float(at))
    return best


def _tip(facts: CoupleFacts, token: str, today) -> str:
    """A deterministic feature tip for this couple on this day (rule d)."""
    day = int(today.strftime("%Y%m%d"))
    return TIPS[(day + sum(token.encode())) % len(TIPS)]


def choose_message(*, facts: CoupleFacts, spouse: str, other: str, other_name: str, now: float,
                   tz: ZoneInfo, token: str) -> str | None:
    """The pure decision: which warm line, if any, ``spouse`` is owed now.

    ``other_name`` is the other spouse's display name (the caller knows it; the
    pure function only reads metadata). Returns the message body, or None when
    nothing is due (quiet hours, outside the chosen hour, already encouraged
    today, encouragements off).
    """
    pref = facts.prefs.get(spouse) or {}
    if not pref.get("encouragements", True):
        return None
    hour = int(pref.get("encourage_hour", 19))
    if not _in_encourage_hour(now, tz, hour):
        return None
    today = datetime.fromtimestamp(now, tz).date()
    if spouse in facts.sent_today:
        return None

    # a. no heart or note from this spouse in two days or more
    last_love = max(_last_at(facts, kind="heart", user=spouse),
                    _last_at(facts, kind="note", user=spouse))
    if now - last_love >= LONELY_DAYS * 86400:
        return f"Send {other_name} a little love today: a heart takes one tap."

    # b. the other spouse sent something in the last day this one has not answered
    last_spouse = _last_at(facts, user=spouse)
    for _kind, by, at in facts.records:
        if by != other or not isinstance(at, (int, float)) or now - at > RECENT_S:
            continue
        if at > last_spouse:
            return f"{other_name} reached out today. Send something back?"

    # c. no visit (activity "page") in three days or more
    last_visit = _last_at(facts, what="page")
    if now - last_visit >= NO_VISIT_DAYS * 86400:
        return "Today's verse and question are waiting in Beloved. Talk it over tonight?"

    # d. otherwise a rotating feature tip
    return _tip(facts, token, today)


def decide(*, facts: CoupleFacts, spouse: str, other: str, other_name: str, now: float, tz: ZoneInfo,
           token: str) -> str | None:
    """Public alias of :func:`choose_message` (the tested, pure decision)."""
    return choose_message(facts=facts, spouse=spouse, other=other, other_name=other_name, now=now, tz=tz,
                          token=token)


# --- The once-per-day claim ---------------------------------------------------


def _claim_once(r, token: str, user: str, today: str) -> bool:
    """Record today's encouragement under a ``once`` key so a restart never sends twice."""
    return bool(r.set(f"beloved:encourage:{token}:{user}:{today}", "1", nx=True, ex=2 * 86400))


def _sent_today(r, token: str, user: str, today: str) -> bool:
    try:
        return bool(r.get(f"beloved:encourage:{token}:{user}:{today}"))
    except Exception:
        return False


# --- One pass (the background worker runs it every CHECK_S) -------------------


async def _people_directory(client, identity: str, secret: str) -> dict[str, dict[str, Any]] | None:
    import aiohttp

    try:
        async with client.get(f"{identity.rstrip('/')}/api/users", headers={"X-Internal-Secret": secret},
                              timeout=aiohttp.ClientTimeout(total=15)) as resp:
            users = await resp.json(content_type=None) if resp.status == 200 else None
    except (TimeoutError, aiohttp.ClientError) as exc:
        log.warning("[beloved] encouragements: identity unreachable (%s); skipping this pass", type(exc).__name__)
        return None
    if not isinstance(users, list):
        log.warning("[beloved] encouragements: identity gave no user list; skipping this pass")
        return None
    return {str(u["username"]): u for u in users if isinstance(u, dict) and u.get("username")}


def _household_tz() -> ZoneInfo | None:
    from services.common import household_tz

    try:
        return household_tz.zoneinfo()
    except household_tz.HouseholdTimeZoneError as exc:
        log.error("[beloved] encouragements: %s (none until it is set)", exc)
        return None


async def check_once(client, *, identity: str, secret: str, now: float | None = None) -> int:
    """Look at every enrolled couple once and send the encouragements due. Returns how many went."""
    from services.gateway import beloved, private_chat
    from services.gateway.cache import get_redis

    if not ENABLED:
        return 0
    tz = _household_tz()
    if tz is None:
        return 0
    people = await _people_directory(client, identity, secret)
    if people is None:
        return 0
    try:
        r = get_redis()
    except Exception as exc:
        log.warning("[beloved] encouragements: no redis (%s); skipping this pass", type(exc).__name__)
        return 0
    at = time.time() if now is None else now
    sent = 0
    for chat in await asyncio.to_thread(private_chat.get_store().all):
        token = str(chat.get("token") or "")
        members = [str(m) for m in chat.get("members", [])]
        # Same couple rule as beloved itself: one father and one mother, both still parents.
        if not private_chat.is_couple(chat, people):
            continue
        try:
            records = await asyncio.to_thread(beloved.get_store().all, token)
            activity = await asyncio.to_thread(beloved.load_activity, token)
            prefs_state = await asyncio.to_thread(beloved.load_prefs, token)
        except Exception as exc:
            log.warning("[beloved] encouragements: %s unreadable (%s); skipping", token, type(exc).__name__)
            continue
        # Only couples already enrolled in Beloved: they have kept something there or opened it.
        # A father and mother who never used it are never nudged into it.
        if not records and not activity:
            continue
        facts_records = [(str(r.get("kind") or ""), str(r.get("by") or ""), r.get("at", 0)) for r in records]
        prefs = {m: beloved.prefs_for(prefs_state, m) for m in members}
        today = datetime.fromtimestamp(at, tz).strftime("%Y-%m-%d")
        sent_today = {m for m in members if _sent_today(r, token, m, today)}
        for spouse in members:
            facts = CoupleFacts(records=facts_records, activity=activity, prefs=prefs, sent_today=set(sent_today))
            other = next((m for m in members if m != spouse), "")
            if not other:
                continue
            other_name = private_chat._display(people.get(other) or {"username": other})
            body = await asyncio.to_thread(decide, facts=facts, spouse=spouse, other=other,
                                           other_name=other_name, now=at, tz=tz, token=token)
            if not body:
                continue
            if not await asyncio.to_thread(_claim_once, r, token, spouse, today):
                continue
            if await _send(token, spouse, body, client=client, secret=secret):
                sent += 1
                sent_today.add(spouse)
                log.info("[beloved] %s: encouraged %s", token, spouse)
    return sent


async def _send(token: str, user: str, body: str, *, client, secret: str) -> bool:
    """One content-free encouragement to one spouse, exactly like beloved's notices."""
    target = notify.link("chat", chat=token, beloved=1)
    payload = notify.notice(
        user, "nudge", "Beloved", body,
        link=target, priority=4, tag=f"beloved-encourage:{token}", kind="nudge",
        actions=[notify.view_action(target)],
        data={"beloved": True, "private": True, "encouragement": True},
    )
    try:
        return await notify.send(payload, client=client, secret=secret)
    except Exception as exc:
        log.warning("[beloved] encouragements: %s notice not delivered (%s)", token, type(exc).__name__)
        return False


def remove(token: str) -> None:
    """A deleted chat's preferences and activity go with it (the folder is removed by the store)."""
    # The whole beloved/<token> folder is removed by private_chat.Store.remove; nothing extra to do.
    return None

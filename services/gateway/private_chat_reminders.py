"""Gentle reply reminders in the parents' private chat.

When one parent (A) has written, the other (B) has read it and B has not
answered, B gets one quiet reminder after a while: "Sam is waiting to hear
back from you". The nudge (services/gateway/private_chat.py) is A's own button
while a message is still unread; a reminder is the server's, once it has been
read and left.

The server cannot read the chat (it is end to end encrypted), so it decides
from metadata only: who sent the newest message and when, and each member's
read marker (services/gateway/private_chat_messages.py). Whether that message
asked something is decided on B's device, which holds the text: when B opens
the thread, the app looks at A's last few messages since B last wrote
(services/ui/src/lib/privateChatReminders.ts) and, if one of them asks a
question, tells the server one bit for the newest message id
(``POST /chats/<token>/expects-reply``). Nothing else about the text leaves
the device. With that bit the reminder comes after ``QUESTION_DELAY_S``
(2 h), without it after ``DELAY_S`` (6 h).

A reminder is due for B when all of these hold:

* the newest live message is A's, and B's read marker is at or past it (so B
  has read it and has sent nothing since);
* B has reminders on (``PUT /chats/<token>/reminders``, per person, default on);
* B has not been reminded about this message already (one per message: A's
  newer message starts a new clock, and B's reply ends it);
* the delay has passed, counted from the later of when A sent it and when the
  server first saw B had read it, so reading late never triggers one at once;
* the message is no older than ``MAX_AGE_S`` (3 days);
* it is not quiet hours in the household's time zone (``QUIET``, 21:30 to
  07:30; a reminder held by them goes after they end, if still due);
* B has had fewer than ``DAILY_MAX`` (2) reminders this household day;
* the chat still exists, lives in the gateway's store and both members are
  still parents (anything else counts as locked: no reminder).

What the server keeps for this (``PRIVATE_CHAT_DIR/reminders/<token>.json``):
each member's on/off choice, the id of the newest message B's device flagged
as asking something (``expects``), the id and time the server first saw B
had read it (``read_seen``), the ids B was reminded about and the times of
the reminders sent in the last two days. No content, and it goes with the chat
when the chat is deleted.

The notice carries no content either: "Sam is waiting to hear back from you",
or "Sam asked you something and is waiting to hear back" when the flag is set.
It is gentle on purpose: category ``chat`` (the private chat's ordinary message
channel and sound, grouped with the chat's messages, not shown while the chat
is on screen) with its own kind ``private_reminder``, never the nudge's
channel, heartbeat vibration or full-screen card. Tagged per chat so a newer
one replaces it.

Settings (``.env``, read once at start; a malformed value stops the gateway
with a message saying which):

* ``PRIVATE_CHAT_REMINDERS`` (1): 0 turns the whole feature off.
* ``PRIVATE_CHAT_REMINDER_DELAY_S`` (21600) and
  ``PRIVATE_CHAT_REMINDER_QUESTION_DELAY_S`` (7200).
* ``PRIVATE_CHAT_REMINDER_QUIET`` (``21:30-07:30``; ``off`` for none).
* ``PRIVATE_CHAT_REMINDER_DAILY_MAX`` (2), ``PRIVATE_CHAT_REMINDER_MAX_AGE_S``
  (259200) and ``PRIVATE_CHAT_REMINDER_CHECK_S`` (300, how often the gateway's
  background worker looks).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from datetime import time as dtime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import aiohttp
from fastapi import APIRouter, HTTPException, Request

from services.common import notify

log = logging.getLogger("gateway.private_chat_reminders")

router = APIRouter(prefix="/api/private-chat")


# --- Settings (read once, at import) --------------------------------------------


def _env_int(name: str, default: int, *, low: int, high: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name}={raw!r} is not a whole number (expected {low} to {high}).") from exc
    if not low <= value <= high:
        raise ValueError(f"{name}={value} is out of range (expected {low} to {high}).")
    return value


def _env_flag(name: str, default: bool) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    raise ValueError(f"{name}={raw!r} is not on or off (use 1 or 0).")


_CLOCK = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def parse_quiet(raw: str, name: str = "PRIVATE_CHAT_REMINDER_QUIET") -> tuple[dtime, dtime] | None:
    """``"21:30-07:30"`` as (start, end) local times; ``"off"`` or empty as None. Raises on anything else."""
    text = (raw or "").strip().lower()
    if text in ("", "off", "none"):
        return None
    parts = [p.strip() for p in text.split("-")]
    matches = [_CLOCK.match(p) for p in parts] if len(parts) == 2 else []
    if len(matches) != 2 or not all(matches):
        raise ValueError(f"{name}={raw!r} is not a range like 21:30-07:30 (or off).")
    start, end = (dtime(int(m.group(1)), int(m.group(2))) for m in matches if m)
    if start == end:
        raise ValueError(f"{name}={raw!r} starts and ends at the same time; use off for no quiet hours.")
    return start, end


ENABLED = _env_flag("PRIVATE_CHAT_REMINDERS", True)
#: How long after a message B has read and not answered before the reminder.
DELAY_S = _env_int("PRIVATE_CHAT_REMINDER_DELAY_S", 6 * 3600, low=300, high=7 * 86400)
#: The same when B's device said the message asks something.
QUESTION_DELAY_S = _env_int("PRIVATE_CHAT_REMINDER_QUESTION_DELAY_S", 2 * 3600, low=300, high=7 * 86400)
#: Household-local quiet hours: no reminder inside them.
QUIET = parse_quiet(os.getenv("PRIVATE_CHAT_REMINDER_QUIET", "21:30-07:30"))
#: Reminders one person may get in one household day.
DAILY_MAX = _env_int("PRIVATE_CHAT_REMINDER_DAILY_MAX", 2, low=1, high=10)
#: No reminder for a message older than this when it would go.
MAX_AGE_S = _env_int("PRIVATE_CHAT_REMINDER_MAX_AGE_S", 3 * 86400, low=3600, high=30 * 86400)
#: The notice's kind: an ordinary chat notice, never a nudge.
KIND = "private_reminder"
#: How often the background worker looks.
CHECK_S = _env_int("PRIVATE_CHAT_REMINDER_CHECK_S", 300, low=30, high=3600)

#: Ids a member was reminded about that are kept (only the newest can matter; a few cover a deleted one).
_KEEP_REMINDED = 20
#: Reminder times kept: enough to count today's in any time zone.
_KEEP_SENT_S = 2 * 86400

_lock = threading.Lock()


# --- State (one small JSON file per chat, no content) ----------------------------


class ReminderStateUnreadable(Exception):
    """The chat's reminder file exists but cannot be read or parsed. Never treated as empty: writing over it
    would silently undo a parent's choice (reminders turned off) along with everything else in it."""


def _path(token: str) -> Path:
    from services.gateway import private_chat

    store = private_chat.get_store()
    store._chat_path(token)  # validates the token
    return store.root / "reminders" / f"{token}.json"


def _empty() -> dict[str, Any]:
    return {"enabled": {}, "expects": {}, "read_seen": {}, "reminded": {}, "sent": {}}


def load(token: str) -> dict[str, Any]:
    path = _path(token)
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return _empty()  # first use of this chat: nothing chosen yet
    except (OSError, ValueError) as exc:
        log.error("[private-chat] %s: reminder state %s unreadable (%s: %s); leaving it untouched",
                  token, path, type(exc).__name__, exc)
        raise ReminderStateUnreadable(f"{path}: {type(exc).__name__}: {exc}") from exc
    if not isinstance(data, dict):
        log.error("[private-chat] %s: reminder state %s is not a JSON object; leaving it untouched", token, path)
        raise ReminderStateUnreadable(f"{path}: not a JSON object")
    state = _empty()
    for key in state:
        if isinstance(data.get(key), dict):
            state[key] = data[key]
    return state


def _save(token: str, state: dict[str, Any]) -> None:
    from services.gateway.private_chat_messages import write_atomic

    write_atomic(_path(token), json.dumps(state, sort_keys=True).encode())


def _update(token: str, change) -> dict[str, Any]:
    """Re-read the chat's reminder state, apply ``change`` and save it (one writer at a time)."""
    with _lock:
        state = load(token)
        result = change(state)
        _save(token, state)
        return result if isinstance(result, dict) else state


def enabled_for(state: dict[str, Any], user: str) -> bool:
    return state.get("enabled", {}).get(user, True) is not False


# --- The rules (pure, so every one can be tested on its own) ---------------------


@dataclass(frozen=True)
class Decision:
    due: bool
    reason: str
    question: bool = False
    due_at: float | None = None


def in_quiet_hours(now: float, tz: ZoneInfo, quiet: tuple[dtime, dtime] | None) -> bool:
    if quiet is None:
        return False
    start, end = quiet
    local = datetime.fromtimestamp(now, tz).time()
    if start < end:
        return start <= local < end
    return local >= start or local < end


def sent_today(stamps: list[Any], now: float, tz: ZoneInfo) -> int:
    today = datetime.fromtimestamp(now, tz).date()
    return sum(1 for t in stamps if isinstance(t, (int, float)) and datetime.fromtimestamp(t, tz).date() == today)


def decide(
    *,
    recipient: str,
    latest: dict[str, Any] | None,
    read_upto: int,
    state: dict[str, Any],
    now: float,
    tz: ZoneInfo,
    delay_s: int | None = None,
    question_delay_s: int | None = None,
    quiet: tuple[dtime, dtime] | str | None = "default",
    daily_max: int | None = None,
    max_age_s: int | None = None,
) -> Decision:
    """Whether ``recipient`` is owed a reminder now about the chat's newest message ``latest``."""
    delay_s = DELAY_S if delay_s is None else delay_s
    question_delay_s = QUESTION_DELAY_S if question_delay_s is None else question_delay_s
    quiet_hours = QUIET if quiet == "default" else quiet
    daily_max = DAILY_MAX if daily_max is None else daily_max
    max_age_s = MAX_AGE_S if max_age_s is None else max_age_s

    if not latest or latest.get("deleted"):
        return Decision(False, "no_message")
    mid = int(latest.get("id") or 0)
    by = str(latest.get("by") or "")
    if not mid or not by:
        return Decision(False, "no_message")
    if by == recipient:
        return Decision(False, "replied")
    if read_upto < mid:
        return Decision(False, "unread")
    if not enabled_for(state, recipient):
        return Decision(False, "off")
    if mid in (state.get("reminded", {}).get(recipient) or []):
        return Decision(False, "already")
    sent_at = float(latest.get("at") or 0)
    question = state.get("expects", {}).get(recipient) == mid
    seen = state.get("read_seen", {}).get(recipient)
    seen_at = float(seen.get("at") or now) if isinstance(seen, dict) and seen.get("id") == mid else now
    due_at = max(sent_at, seen_at) + (question_delay_s if question else delay_s)
    if now < due_at:
        return Decision(False, "waiting", question, due_at)
    if now - sent_at > max_age_s:
        return Decision(False, "too_old", question, due_at)
    if in_quiet_hours(now, tz, quiet_hours):
        return Decision(False, "quiet", question, due_at)
    if sent_today(state.get("sent", {}).get(recipient) or [], now, tz) >= daily_max:
        return Decision(False, "daily_cap", question, due_at)
    return Decision(True, "due", question, due_at)


def reminder_text(sender_name: str, question: bool) -> str:
    """The notice's body: who, never what."""
    if question:
        return f"{sender_name} asked you something and is waiting to hear back"
    return f"{sender_name} is waiting to hear back from you"


# --- One pass (the gateway's background worker runs it every CHECK_S) ------------


def _note_read(token: str, user: str, mid: int, now: float) -> None:
    """Remember when the server first saw ``user`` had read message ``mid`` (once per message)."""

    def change(state: dict[str, Any]) -> None:
        seen = state["read_seen"].get(user)
        if not (isinstance(seen, dict) and seen.get("id") == mid):
            state["read_seen"][user] = {"id": mid, "at": int(now)}

    current = load(token)["read_seen"].get(user)
    if isinstance(current, dict) and current.get("id") == mid:
        return
    _update(token, change)


def _claim(token: str, user: str, mid: int, now: float, tz: ZoneInfo, daily_max: int) -> bool:
    """Record the reminder before it is sent, under the lock, so two passes can never send it twice."""

    def change(state: dict[str, Any]) -> dict[str, Any]:
        reminded = [i for i in state["reminded"].get(user) or [] if isinstance(i, int)]
        sent = [t for t in state["sent"].get(user) or [] if isinstance(t, (int, float)) and now - t < _KEEP_SENT_S]
        if mid in reminded or sent_today(sent, now, tz) >= daily_max:
            return {"claimed": False}
        state["reminded"][user] = [*reminded, mid][-_KEEP_REMINDED:]
        state["sent"][user] = [*sent, int(now)]
        return {"claimed": True}

    return bool(_update(token, change).get("claimed"))


def _release(token: str, user: str, mid: int, now: float) -> None:
    """Undo a claim whose notice was not delivered, so the next pass tries again."""

    def change(state: dict[str, Any]) -> None:
        state["reminded"][user] = [i for i in state["reminded"].get(user) or [] if i != mid]
        sent = list(state["sent"].get(user) or [])
        if int(now) in sent:
            sent.remove(int(now))
        state["sent"][user] = sent

    _update(token, change)


async def _directory(client: aiohttp.ClientSession, identity: str, secret: str) -> dict[str, dict[str, Any]] | None:
    try:
        async with client.get(f"{identity.rstrip('/')}/api/users", headers={"X-Internal-Secret": secret},
                              timeout=aiohttp.ClientTimeout(total=15)) as resp:
            users = await resp.json(content_type=None) if resp.status == 200 else None
    except (TimeoutError, aiohttp.ClientError) as exc:
        log.warning("[private-chat] reminders: identity unreachable (%s); skipping this pass", type(exc).__name__)
        return None
    if not isinstance(users, list):
        log.warning("[private-chat] reminders: identity gave no user list; skipping this pass")
        return None
    return {str(u["username"]): u for u in users if isinstance(u, dict) and u.get("username")}


def _household_tz() -> ZoneInfo | None:
    from services.common import household_tz

    try:
        return household_tz.zoneinfo()
    except household_tz.HouseholdTimeZoneError as exc:
        log.error("[private-chat] reminders: %s (no reminders until it is set)", exc)
        return None


async def check_once(client: aiohttp.ClientSession, *, identity: str, telemetry: str, secret: str,
                     now: float | None = None) -> int:
    """Look at every private chat once and send the reminders that are due. Returns how many went."""
    from services.gateway import private_chat, private_chat_messages

    if not ENABLED:
        return 0
    tz = _household_tz()
    if tz is None:
        return 0
    people = await _directory(client, identity, secret)
    if people is None:
        return 0
    sent = 0
    for chat in await asyncio.to_thread(private_chat.get_store().all):
        token = str(chat.get("token") or "")
        members = [str(m) for m in chat.get("members", [])]
        # Locked: not in the gateway's store yet, not two people, or someone is no longer a parent.
        if not private_chat_messages.is_store_chat(chat) or len(set(members)) != 2:
            continue
        if not all(private_chat.is_parent(people.get(m)) for m in members):
            continue
        try:
            latest = await asyncio.to_thread(private_chat_messages.latest, token)
            markers = await asyncio.to_thread(private_chat_messages.get_store().read_markers, token)
        except private_chat_messages.StoreUnreadable as exc:
            log.warning("[private-chat] reminders: %s unreadable (%s)", token, exc)
            continue
        if not latest:
            continue
        mid = int(latest.get("id") or 0)
        at = time.time() if now is None else now
        try:
            sent += await _remind_members(client, token, members, latest, markers, people, mid, at, tz,
                                          telemetry=telemetry, secret=secret)
        except ReminderStateUnreadable as exc:
            log.error("[private-chat] reminders: skipped %s, its reminder state is unreadable (%s)", token, exc)
    return sent


async def _remind_members(client: aiohttp.ClientSession, token: str, members: list[str], latest: dict[str, Any],
                          markers: dict[str, int], people: dict[str, dict[str, Any]], mid: int, at: float,
                          tz: ZoneInfo, *, telemetry: str, secret: str) -> int:
    """Send the reminders due in one chat; raises ReminderStateUnreadable without writing anything."""
    from services.gateway import private_chat

    sent = 0
    for recipient in members:
        if recipient == latest.get("by"):
            continue
        if markers.get(recipient, 0) >= mid:
            await asyncio.to_thread(_note_read, token, recipient, mid, at)
        state = await asyncio.to_thread(load, token)
        decision = decide(recipient=recipient, latest=latest, read_upto=markers.get(recipient, 0),
                          state=state, now=at, tz=tz)
        if not decision.due:
            continue
        if not await asyncio.to_thread(_claim, token, recipient, mid, at, tz, DAILY_MAX):
            continue
        sender = people.get(str(latest.get("by"))) or {"username": latest.get("by")}
        url = f"/family?chat={token}"
        payload = notify.notice(
            recipient, "chat", "Private chat", reminder_text(private_chat._display(sender), decision.question),
            url=url, priority=3, tag=f"private-reminder:{token}", kind=KIND, replace=True,
            actions=[notify.view_action(url)],
            data={"token": token, "private": True, "reminder": True},
        )
        if await notify.send(payload, client=client, telemetry=telemetry, secret=secret):
            sent += 1
            log.info("[private-chat] %s: reminded %s about message %s%s", token, recipient, mid,
                     " (asked something)" if decision.question else "")
        else:
            await asyncio.to_thread(_release, token, recipient, mid, at)
    return sent


# --- Routes ---------------------------------------------------------------------


async def _body(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="A JSON body is required.") from exc
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="A JSON body is required.")
    return body


def _unreadable(exc: ReminderStateUnreadable) -> HTTPException:
    return HTTPException(
        status_code=500,
        detail="The reminder settings for this chat could not be read (the file under PRIVATE_CHAT_DIR/reminders "
               "is damaged or unreadable), so nothing was changed. Fix or remove that file.",
    )


def _public(state: dict[str, Any], user: str) -> dict[str, Any]:
    return {
        "enabled": enabled_for(state, user),
        "available": ENABLED,
        "delay_s": DELAY_S,
        "question_delay_s": QUESTION_DELAY_S,
        "quiet": None if QUIET is None else f"{QUIET[0]:%H:%M}-{QUIET[1]:%H:%M}",
        "daily_max": DAILY_MAX,
    }


@router.get("/chats/{token}/reminders")
async def get_reminders(token: str, request: Request):
    """The caller's reply reminder setting for this chat (on unless they turned it off)."""
    from services.gateway import private_chat

    user, _chat, _people = await private_chat._member_chat(request, token)
    try:
        state = await asyncio.to_thread(load, token)
    except ReminderStateUnreadable as exc:
        raise _unreadable(exc) from exc
    return _public(state, user)


@router.put("/chats/{token}/reminders")
async def put_reminders(token: str, request: Request):
    """Turn the caller's own reply reminders for this chat on or off (``{"enabled": bool}``)."""
    from services.gateway import private_chat

    user, _chat, _people = await private_chat._member_chat(request, token)
    body = await _body(request)
    value = body.get("enabled")
    if not isinstance(value, bool):
        raise HTTPException(status_code=422, detail="enabled must be true or false.")

    def change(state: dict[str, Any]) -> None:
        state["enabled"][user] = value

    try:
        state = await asyncio.to_thread(_update, token, change)
    except ReminderStateUnreadable as exc:
        raise _unreadable(exc) from exc
    except OSError as exc:
        log.error("[private-chat] %s: could not save the reminder setting: %s", token, type(exc).__name__)
        raise HTTPException(status_code=500, detail="Could not save the setting (PRIVATE_CHAT_DIR not writable).") from exc
    log.info("[private-chat] %s turned reply reminders %s in %s", user, "on" if value else "off", token)
    return _public(state, user)


@router.post("/chats/{token}/expects-reply")
async def expects_reply(token: str, request: Request):
    """One bit from the reader's device: the newest message (the partner's) asks something.

    ``{"message_id": N, "expects_reply": true}``. The device decided it from
    the text it holds; the server only learns the bit, for that one message id,
    and only while it is the chat's newest message and not the caller's own.
    ``false`` clears it.
    """
    from services.gateway import private_chat, private_chat_messages

    user, chat, _people = await private_chat._member_chat(request, token)
    body = await _body(request)
    mid = body.get("message_id")
    flag = body.get("expects_reply")
    if not isinstance(mid, int) or isinstance(mid, bool) or mid < 1:
        raise HTTPException(status_code=422, detail="message_id must be a message id.")
    if not isinstance(flag, bool):
        raise HTTPException(status_code=422, detail="expects_reply must be true or false.")
    if not private_chat_messages.is_store_chat(chat):
        raise HTTPException(status_code=409, detail="Reminders start once the chat has moved off Talk.")
    try:
        latest = await asyncio.to_thread(private_chat_messages.latest, token)
    except private_chat_messages.StoreUnreadable as exc:
        raise HTTPException(status_code=503, detail="Could not read the chat; try again.") from exc
    if not latest or int(latest.get("id") or 0) != mid or latest.get("by") == user:
        raise HTTPException(status_code=409, detail="That is not the newest message from your partner.")

    def change(state: dict[str, Any]) -> None:
        if flag:
            state["expects"][user] = mid
        else:
            state["expects"].pop(user, None)

    try:
        await asyncio.to_thread(_update, token, change)
    except ReminderStateUnreadable as exc:
        raise _unreadable(exc) from exc
    return {"message_id": mid, "expects_reply": flag}


def remove(token: str) -> None:
    """Forget a deleted chat's reminder state."""
    _path(token).unlink(missing_ok=True)

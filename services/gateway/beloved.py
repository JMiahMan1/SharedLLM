"""Beloved: the married couple's page inside the parents' private chat.

"I am my beloved's, and my beloved is mine" (Song of Solomon 6:3). A home
page for the two of them (their pictures, how long they have been married, a
cover photo), their special days, a daily verse and devotion with a question
to talk over, love notes, "thinking of you" hearts, prayer requests and date
night ideas.

Everything personal is end to end encrypted exactly like the private chat
(services/gateway/private_chat.py): the device seals each record with the
chat's data key and this service keeps only the envelope, who wrote it and
when. Wedding dates, birthdays, the names of special days, notes, prayers,
answers, choices and the cover photo (an encrypted blob in the chat's blob
store) are never readable here.

Who may see it:

* **Only the couple: one father and one mother.** Every route goes through
  ``private_chat._member_chat`` (a member of this private chat who is still a
  parent: family role father or mother, not a minor, not the system account,
  with a chat account) and then checks the chat's two members are one
  ``father`` and one ``mother``. Anyone else, an administrator who is not one
  of the two included, gets the same 404 as a chat that does not exist, so
  nothing says the feature is there.
* **Notices go only to the other spouse** and carry no content: "Sam is
  thinking of you", "Sam left you a love note". They use the private chat's
  ``nudge`` category (a heartbeat on the phone), not a chat notice, so they
  make no unread count in the thread.
* **Records are kept** as ``beloved/<token>/<kind>/<id>.json`` under
  ``PRIVATE_CHAT_DIR`` and deleted with the chat (``Store.remove``).

Reminders for special days are scheduled on the phone itself (the server
cannot read the dates); see docs/BELOVED.md.

The "Just us" tab keeps two more kinds, ``together`` (a day the couple marked)
and ``plan`` (a planned evening). Their names were chosen to say nothing in
clear, and the only notice they make, for a plan, names no content at all.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import os
import re
import secrets
import shutil
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from services.common import notify
from services.common.sealed import is_sealed
from services.gateway import private_chat

log = logging.getLogger("gateway.beloved")

router = APIRouter(prefix="/api/private-chat/chats/{token}/beloved")

#: The kinds of record a couple keeps. The kind is not secret (the notices name it); what is in it is.
KINDS = frozenset({"profile", "day", "note", "heart", "prayer", "devotion", "idea", "together", "plan"})
#: Kinds only their author may change or remove (what someone wrote or felt is theirs).
AUTHOR_ONLY = frozenset({"note", "heart", "devotion", "together"})
#: One sealed record: room for a long note or prayer, never a picture (those are blobs).
MAX_RECORD_BYTES = 16 * 1024
#: Records per kind per couple, so a runaway client cannot fill the disk.
MAX_RECORDS_PER_KIND = 5_000
#: What the other spouse is told when a record is added, by kind. Never any content.
NOTICES = {
    "heart": "{name} is thinking of you",
    "note": "{name} left you a love note",
    "prayer": "{name} shared a prayer request with you",
    "idea": "{name} has a date night idea for you",
    "day": "{name} added a special day",
    "together": "Something from {name} is waiting",
    "profile": "{name} updated your Beloved page",
    # The "Just us" tab: a notice that could be read over a shoulder or by a child
    # holding the phone, so it says nothing at all about what is waiting.
    "plan": "Something from {name} is waiting",
}
#: What the other spouse is told when a record is changed, by kind. Never any content.
UPDATED_NOTICES = {
    "day": "{name} updated a special day",
    "plan": "{name} updated your plans",
    "together": "Something from {name} is waiting",
    "prayer": "{name} answered a prayer request",
    "idea": "{name} updated a date night idea",
}
#: One notice per kind per sender in this many seconds; a record sent sooner is kept, just not announced.
NOTICE_INTERVAL_S = {"heart": 60, "note": 30, "prayer": 30, "idea": 30, "plan": 30,
                     "day": 30, "together": 30, "profile": 60}

#: What a spouse may share about their own reading, by ``what``. Never any content.
SEEN_NOTICES = {
    "gallery": "{name} is looking through your photos",
    "note": "{name} read your love note",
    "prayer": "{name} read your prayer request",
    "idea": "{name} saw your date night idea",
}
#: One reading notice per ``what`` per viewer in this many seconds (a page visit makes no notice).
SEEN_INTERVAL_S = {"gallery": 1800, "note": 600, "prayer": 600, "idea": 600}
#: A page visit is recorded for encouragements but never announced.
SEEN_WHAT = frozenset({"gallery", "note", "prayer", "idea", "page"})

#: Activity log: metadata only (what, who, when), the newest this many kept per couple.
ACTIVITY_KEEP = 200
#: How many the overview returns.
ACTIVITY_SHOW = 50

#: Per-user Beloved preferences and their defaults (never secret, never sealed).
DEFAULT_PREFS = {"share_activity": True, "encouragements": True, "encourage_hour": 19}
PREF_HOUR_MIN, PREF_HOUR_MAX = 7, 21

_lock = threading.Lock()

_ID = re.compile(r"^[0-9a-f]{32}$")
_FENCE = "```jarvis-envelope"


# --- Store --------------------------------------------------------------------


class BelovedStore:
    """Sealed records under ``<root>/beloved/<token>/<kind>/<id>.json``."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def _dir(self, token: str, kind: str) -> Path:
        if kind not in KINDS:
            raise HTTPException(status_code=404, detail=private_chat.NOT_AVAILABLE)
        private_chat.get_store()._chat_path(token)  # validates the token
        return self.root / "beloved" / token / kind

    def path(self, token: str, kind: str, record_id: str) -> Path:
        if not _ID.match(record_id or ""):
            raise HTTPException(status_code=404, detail="No such record.")
        return self._dir(token, kind) / f"{record_id}.json"

    def add(self, token: str, kind: str, by: str, sealed: str) -> dict[str, Any]:
        folder = self._dir(token, kind)
        folder.mkdir(parents=True, exist_ok=True)
        if sum(1 for _ in folder.glob("*.json")) >= MAX_RECORDS_PER_KIND:
            raise HTTPException(status_code=413, detail="There is no room for more of these.")
        now = int(time.time())
        record = {"id": secrets.token_hex(16), "kind": kind, "by": by, "at": now,
                  "updated_at": now, "updated_by": by, "sealed": sealed}
        self._write(folder / f"{record['id']}.json", record)
        return record

    def get(self, token: str, kind: str, record_id: str) -> dict[str, Any] | None:
        path = self.path(token, kind, record_id)
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            return None

    def replace(self, token: str, record: dict[str, Any], by: str, sealed: str) -> dict[str, Any]:
        record = {**record, "sealed": sealed, "updated_at": int(time.time()), "updated_by": by}
        self._write(self.path(token, record["kind"], record["id"]), record)
        return record

    def delete(self, token: str, kind: str, record_id: str) -> None:
        self.path(token, kind, record_id).unlink(missing_ok=True)

    def all(self, token: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for kind in sorted(KINDS):
            folder = self._dir(token, kind)
            if not folder.is_dir():
                continue
            for path in folder.glob("*.json"):
                try:
                    out.append(json.loads(path.read_text()))
                except (OSError, ValueError):
                    log.warning("[beloved] unreadable record %s", path.name)
        return sorted(out, key=lambda r: (r.get("at", 0), r.get("id", "")))

    def remove_all(self, token: str) -> None:
        private_chat.get_store()._chat_path(token)
        shutil.rmtree(self.root / "beloved" / token, ignore_errors=True)

    @staticmethod
    def _write(path: Path, record: dict[str, Any]) -> None:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(record, sort_keys=True))
        os.replace(tmp, path)


def get_store() -> BelovedStore:
    return BelovedStore(private_chat.get_store().root)


# --- Preferences and activity (metadata only, never sealed) --------------------


def _prefs_path(token: str) -> Path:
    private_chat.get_store()._chat_path(token)  # validates the token
    return get_store().root / "beloved" / token / "prefs.json"


def _default_prefs() -> dict[str, Any]:
    return dict(DEFAULT_PREFS)


def load_prefs(token: str) -> dict[str, Any]:
    """The couple's per-user preferences (never a record: no ``by``, no ``sealed``)."""
    path = _prefs_path(token)
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        log.warning("[beloved] %s: preferences unreadable; using defaults", token)
        return {}
    return data if isinstance(data, dict) else {}


def prefs_for(state: dict[str, Any], user: str) -> dict[str, Any]:
    stored = state.get(user) if isinstance(state.get(user), dict) else {}
    return {
        "share_activity": bool(stored.get("share_activity", DEFAULT_PREFS["share_activity"])),
        "encouragements": bool(stored.get("encouragements", DEFAULT_PREFS["encouragements"])),
        "encourage_hour": int(stored.get("encourage_hour", DEFAULT_PREFS["encourage_hour"])),
    }


def set_prefs(token: str, user: str, prefs: dict[str, Any]) -> dict[str, Any]:
    """Replace the caller's own preferences; another spouse's are left alone."""

    def change() -> dict[str, Any]:
        state = load_prefs(token)
        state[user] = dict(prefs)
        return state

    with _lock:
        state = change()
        _prefs_path(token).parent.mkdir(parents=True, exist_ok=True)
        tmp = _prefs_path(token).with_suffix(".tmp")
        tmp.write_text(json.dumps(state, sort_keys=True))
        os.replace(tmp, _prefs_path(token))
    return prefs_for(state, user)


def _activity_path(token: str) -> Path:
    private_chat.get_store()._chat_path(token)  # validates the token
    return get_store().root / "beloved" / token / "activity.json"


def load_activity(token: str) -> list[dict[str, Any]]:
    path = _activity_path(token)
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return []
    except (OSError, ValueError):
        log.warning("[beloved] %s: activity unreadable; starting empty", token)
        return []
    return [e for e in data if isinstance(e, dict)] if isinstance(data, list) else []


def activity_entries(token: str, limit: int = ACTIVITY_SHOW) -> list[dict[str, Any]]:
    """The newest activity, newest first, each ``{who, what, at}``: no content, ever."""
    entries = sorted(load_activity(token), key=lambda e: (e.get("at", 0), e.get("who", "")), reverse=True)
    return [{"who": e.get("who"), "what": e.get("what"), "at": e.get("at", 0)} for e in entries[:limit]]


def record_activity(token: str, who: str, what: str) -> None:
    """Keep one ``{who, what, at}`` entry, capped at ``ACTIVITY_KEEP`` newest. Metadata only."""
    with _lock:
        entries = load_activity(token)
        entries.append({"who": who, "what": what, "at": int(time.time())})
        entries = entries[-ACTIVITY_KEEP:]
        path = _activity_path(token)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(entries, sort_keys=True))
        os.replace(tmp, path)


# --- Who may see it ---------------------------------------------------------


async def _couple(request: Request, token: str) -> tuple[str, dict[str, Any], dict[str, dict[str, Any]]]:
    """The caller, the chat and the directory, for one of the couple; 404 for anyone else.

    Not signed in, not a member, no longer a parent, or a chat that is not one
    father and one mother: all the same 404, naming nothing. Only identity
    being down stays an honest 503.
    """
    try:
        user, chat, people = await private_chat._member_chat(request, token)
    except HTTPException as exc:
        if exc.status_code in (401, 403, 404):
            raise HTTPException(status_code=404, detail=private_chat.NOT_AVAILABLE) from exc
        raise
    if not private_chat.is_couple(chat, people):
        raise HTTPException(status_code=404, detail=private_chat.NOT_AVAILABLE)
    return user, chat, people


# --- Sealed records -----------------------------------------------------------


def _sealed(value: Any) -> str:
    """A sealed envelope whose iv and ciphertext are the right shape. Never anything readable."""
    if not isinstance(value, str) or not is_sealed(value):
        raise HTTPException(status_code=422, detail="Only an encrypted record can be kept.")
    if len(value.encode()) > MAX_RECORD_BYTES:
        raise HTTPException(status_code=413, detail="That is too long.")
    start = value.find(_FENCE)
    end = value.find("```", start + len(_FENCE))  # the closing fence, bare or repeated
    try:
        envelope = json.loads(value[start + len(_FENCE):end].strip()) if start != -1 and end != -1 else None
        iv = base64.b64decode(str(envelope["iv"]), validate=True)
        ct = base64.b64decode(str(envelope["ct"]), validate=True)
    except (TypeError, KeyError, ValueError, binascii.Error) as exc:
        raise HTTPException(status_code=422, detail="Only an encrypted record can be kept.") from exc
    if not isinstance(envelope, dict) or envelope.get("kind") != "sealed" or len(iv) != private_chat.IV_BYTES \
            or len(ct) <= private_chat.TAG_BYTES:
        raise HTTPException(status_code=422, detail="Only an encrypted record can be kept.")
    return value


async def _body(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Send a JSON body with the sealed record.") from exc
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="Send a JSON body with the sealed record.")
    return body


def _public(record: dict[str, Any], me: str, people: dict[str, dict[str, Any]]) -> dict[str, Any]:
    by = str(record.get("by") or "")
    return {
        "id": record["id"],
        "kind": record["kind"],
        "at": record.get("at", 0),
        "updated_at": record.get("updated_at", record.get("at", 0)),
        "by": by,
        "own": by == me,
        "by_name": private_chat._display(people.get(by) or {"username": by}),
        "sealed": record["sealed"],
    }


#: The Beloved section a notice opens: the page a person was about when the phone
#: buzzed, not the couple's home. Kept beside the notices so a new kind cannot be
#: added without deciding where it lands.
BELOVED_SECTIONS = {
    "heart": "home",
    "note": "notes",
    "prayer": "notes",
    "idea": "dates",
    "day": "days",
    "together": "today",
    "plan": "justus",
    "profile": "home",
}


def _beloved_section(kind: str) -> str:
    """Where a Beloved notice opens; the couple's home when the kind is unknown."""
    return BELOVED_SECTIONS.get(kind, "home")


# --- Notices --------------------------------------------------------------------


def _claim(key: str, interval: int) -> bool:
    from services.gateway.cache import get_redis

    return bool(get_redis().set(key, "1", nx=True, ex=interval))


async def _tell_spouse(token: str, chat: dict[str, Any], user: str, people: dict[str, dict[str, Any]], kind: str,
                       *, updated: bool = False) -> bool:
    """A content-free notice to the other spouse only; False when it was not sent (too soon, or undelivered)."""
    template = (UPDATED_NOTICES if updated else NOTICES).get(kind) or NOTICES.get(kind)
    if not template:
        return False
    try:
        allowed = await asyncio.to_thread(_claim, f"beloved:notice:{token}:{user}:{kind}", NOTICE_INTERVAL_S[kind])
    except Exception as exc:
        log.warning("[beloved] notice limit unavailable: %s", type(exc).__name__)
        return False
    if not allowed:
        return False
    spouse = [m for m in chat["members"] if m != user]
    body = template.format(name=private_chat._display(people.get(user) or {"username": user}))
    # Beloved opens only for the couple: the app checks it again on arrival, and
    # opens the section the notice is about (a love note lands on Notes, not on
    # the couple's home).
    target = notify.link("chat", chat=token, beloved=_beloved_section(kind))
    sent = await notify.send_many(
        spouse,
        lambda person: notify.notice(
            # The private chat's own gentle category: a heartbeat on the phone and, with
            # Jarvis open, the heart over the screen. No chat token, so no unread count.
            person, "nudge", "Beloved", body,
            link=target, priority=4, tag=f"beloved-{kind}:{token}", kind="nudge",
            actions=[notify.view_action(target)],
            data={"beloved": True, "private": True},
        ),
    )
    return bool(sent)


async def _tell_spouse_seen(token: str, chat: dict[str, Any], user: str,
                            people: dict[str, dict[str, Any]], what: str) -> bool:
    """A content-free notice to the other spouse that this one read something; False when not sent."""
    template = SEEN_NOTICES.get(what)
    if not template:
        return False
    try:
        allowed = await asyncio.to_thread(_claim, f"beloved:seen:{token}:{user}:{what}", SEEN_INTERVAL_S[what])
    except Exception as exc:
        log.warning("[beloved] seen limit unavailable: %s", type(exc).__name__)
        return False
    if not allowed:
        return False
    spouse = [m for m in chat["members"] if m != user]
    body = template.format(name=private_chat._display(people.get(user) or {"username": user}))
    # "They read your note" opens the note it is about.
    target = notify.link("chat", chat=token, beloved=_beloved_section(what))
    sent = await notify.send_many(
        spouse,
        lambda person: notify.notice(
            person, "nudge", "Beloved", body,
            link=target, priority=4, tag=f"beloved-seen-{what}:{token}", kind="nudge",
            actions=[notify.view_action(target)],
            data={"beloved": True, "private": True},
        ),
    )
    return bool(sent)


# --- Routes -------------------------------------------------------------------


async def _children_of(user: str) -> list[dict[str, Any]]:
    """The caller's own children and the birth dates their parents keep in
    Jarvis, for filling in the children's birthdays. Not secret between the two
    parents; the couple's own list is sealed in their profile. When identity
    cannot say, the list is empty and the couple types them in."""
    from services.gateway.app_vetting_routes import identity_call

    try:
        status, body = await identity_call("GET", "/api/internal/guardian/children")
    except Exception as exc:  # a convenience: Beloved opens without it
        log.warning("beloved: could not list the children: %s", exc)
        return []
    if status != 200:
        log.warning("beloved: could not list the children: identity answered HTTP %s", status)
        return []
    out = []
    for child in (body or {}).get("children") or []:
        if user not in (child.get("parents") or []) or not child.get("username"):
            continue
        out.append({
            "username": child["username"],
            "display_name": child.get("display_name") or child["username"],
            "birth_date": child.get("birth_date") or None,
        })
    return sorted(out, key=lambda c: (c["birth_date"] or "9999", c["display_name"]))


@router.get("")
async def overview(token: str, request: Request):
    """The couple (names and pictures, which are not secret in the family) and every sealed record."""
    user, chat, people = await _couple(request, token)
    records = await asyncio.to_thread(get_store().all, token)
    couple = []
    for name in chat["members"]:
        person = people.get(name) or {"username": name}
        couple.append({
            "username": name,
            "display_name": private_chat._display(person),
            "avatar": person.get("avatar") or None,
            "family_role": person.get("family_role"),
            "me": name == user,
        })
    return {
        "me": user,
        "couple": couple,
        "children": await _children_of(user),
        "records": [_public(r, user, people) for r in records
                    if isinstance(r.get("id"), str) and isinstance(r.get("sealed"), str) and r.get("kind") in KINDS],
    }


@router.post("/seen")
async def mark_seen(token: str, request: Request):
    """The caller read something (``{"what": "gallery"|"note"|"prayer"|"idea"|"page"}``).

    Only the caller's own choice decides whether it is shared: with
    ``share_activity`` off, nothing is recorded and nothing is sent. ``page`` is
    recorded for encouragements but never announced.
    """
    user, chat, people = await _couple(request, token)
    body = await _body(request)
    what = body.get("what")
    if what not in SEEN_WHAT:
        raise HTTPException(status_code=422, detail="what must be gallery, note, prayer, idea or page.")
    if not prefs_for(load_prefs(token), user)["share_activity"]:
        return {"shared": False, "notified": False}
    await asyncio.to_thread(record_activity, token, user, what)
    notified = what != "page" and await _tell_spouse_seen(token, chat, user, people, what)
    return {"shared": True, "notified": notified}


@router.get("/activity")
async def activity(token: str, request: Request):
    """The couple's recent activity, newest first, each ``{who, what, at}``: metadata only."""
    user, _chat, people = await _couple(request, token)
    entries = await asyncio.to_thread(activity_entries, token, ACTIVITY_SHOW)
    return {
        "me": user,
        "activity": [{
            "who": e["who"],
            "who_name": private_chat._display(people.get(str(e.get("who"))) or {"username": e.get("who")}),
            "what": e.get("what"),
            "at": e.get("at", 0),
        } for e in entries],
    }


@router.get("/prefs")
async def get_prefs(token: str, request: Request):
    """The caller's own Beloved preferences."""
    user, _chat, _people = await _couple(request, token)
    return prefs_for(await asyncio.to_thread(load_prefs, token), user)


@router.put("/prefs")
async def put_prefs(token: str, request: Request):
    """Set the caller's own preferences: ``share_activity``, ``encouragements``, ``encourage_hour``."""
    user, _chat, _people = await _couple(request, token)
    body = await _body(request)
    prefs = {}
    for field in ("share_activity", "encouragements"):
        if field in body:
            if not isinstance(body[field], bool):
                raise HTTPException(status_code=422, detail=f"{field} must be true or false.")
            prefs[field] = body[field]
    if "encourage_hour" in body:
        hour = body["encourage_hour"]
        if not isinstance(hour, int) or isinstance(hour, bool) or not PREF_HOUR_MIN <= hour <= PREF_HOUR_MAX:
            raise HTTPException(status_code=422, detail=f"encourage_hour must be a whole number from {PREF_HOUR_MIN} to {PREF_HOUR_MAX}.")
        prefs["encourage_hour"] = hour
    current = prefs_for(await asyncio.to_thread(load_prefs, token), user)
    return await asyncio.to_thread(set_prefs, token, user, {**current, **prefs})


@router.post("/{kind}")
async def add_record(token: str, kind: str, request: Request):
    """Keep one sealed record; ``notify`` tells the other spouse (hearts, notes, prayers, ideas)."""
    user, chat, people = await _couple(request, token)
    if kind not in KINDS:
        raise HTTPException(status_code=404, detail=private_chat.NOT_AVAILABLE)
    body = await _body(request)
    sealed = _sealed(body.get("sealed"))
    record = await asyncio.to_thread(get_store().add, token, kind, user, sealed)
    notified = bool(body.get("notify")) and await _tell_spouse(token, chat, user, people, kind)
    return {**_public(record, user, people), "notified": notified}


@router.put("/{kind}/{record_id}")
async def replace_record(token: str, kind: str, record_id: str, request: Request):
    """Replace a record's sealed contents (marking a prayer prayed for, a date idea done, editing a day)."""
    user, chat, people = await _couple(request, token)
    store = get_store()
    record = store.get(token, kind, record_id)
    if not record:
        raise HTTPException(status_code=404, detail="No such record.")
    if kind in AUTHOR_ONLY and record.get("by") != user:
        raise HTTPException(status_code=403, detail="Only the one who wrote it can change it.")
    body = await _body(request)
    sealed = _sealed(body.get("sealed"))
    updated = await asyncio.to_thread(store.replace, token, record, user, sealed)
    if body.get("notify"):
        notified = await _tell_spouse(token, chat, user, people, kind, updated=True)
        return {**_public(updated, user, people), "notified": notified}
    return _public(updated, user, people)


@router.delete("/{kind}/{record_id}")
async def delete_record(token: str, kind: str, record_id: str, request: Request):
    user, _chat, _people = await _couple(request, token)
    store = get_store()
    record = store.get(token, kind, record_id)
    if not record:
        raise HTTPException(status_code=404, detail="No such record.")
    if kind in AUTHOR_ONLY and record.get("by") != user:
        raise HTTPException(status_code=403, detail="Only the one who wrote it can remove it.")
    store.delete(token, kind, record_id)
    return {"deleted": record_id}

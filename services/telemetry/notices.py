"""One Jarvis notice to one person, or to every admin (docs/PUSH.md).

``POST /api/telemetry/notify`` validates the body with ``parse`` and hands it
to ``deliver`` per recipient: preference check, ``once`` dedupe, in-app
outbox, browser web push, then ntfy. Only the outbox write may fail the
request; every transport after it is best effort.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any

import aiohttp

from services.common import notice_links
from services.telemetry import ntfy, push, store
from services.telemetry.config import IDENTITY_SVC
from services.telemetry.store import internal_headers, utcnow_iso

log = logging.getLogger("telemetry.notices")

ADMINS = "@admins"
#: System notices: delivered to admins only (main.deliver_notice).
ADMIN_CATEGORY = "admin"
DEFAULT_CATEGORY = "general"
#: Known categories: used for preference defaults and UI switches.
KNOWN_CATEGORIES = frozenset({
    "admin", "app_updates", "bible", "calendar", "chat", "call",
    "family_presence", "family_safety", "garden", "health", "home",
    "jobs", "leave_by", "raven", "reports", "timers", "weather",
})
ACTION_TYPES = ("view", "callback", "reply")
MAX_ACTIONS = 3
#: Who a notice may reach: everyone, or parents only (never a minor).
AUDIENCES = ("all", "parents")
AUDIENCE_PARENTS = "parents"
#: Preferences and the admin list are re-read from identity this often.
CACHE_SECONDS = 60.0
#: A person's ntfy topic is re-read this often, so a topic that changes in
#: identity (an account removed and added again) is picked up without
#: restarting telemetry.

TOPIC_CACHE_SECONDS = 600.0
ONCE_SECONDS = 3 * 86400

_CATEGORY = re.compile(r"^[a-z][a-z_]{0,31}$")
_CALLBACK = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

_prefs_cache: dict[str, tuple[float, dict[str, bool] | None]] = {}
_topic_cache: dict[str, tuple[float, str]] = {}
_admins_cache: tuple[float, list[str]] | None = None
_minors_cache: tuple[float, set[str]] | None = None


class NoticeError(ValueError):
    """The notify body is invalid (answered 422)."""


def _actions(raw: Any) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise NoticeError("actions must be a list")
    if len(raw) > MAX_ACTIONS:
        raise NoticeError(f"at most {MAX_ACTIONS} actions")
    out = []
    for action in raw:
        if not isinstance(action, dict):
            raise NoticeError("each action must be an object")
        kind = action.get("type")
        label = str(action.get("label") or "").strip()
        if kind not in ACTION_TYPES:
            raise NoticeError(f"action type must be one of {list(ACTION_TYPES)}")
        if not label or len(label) > 40:
            raise NoticeError("action label must be 1 to 40 characters")
        clean: dict[str, Any] = {"label": label, "type": kind}
        if kind == "view":
            url = str(action.get("url") or "").strip()
            if not url:
                raise NoticeError("a view action needs a url")
            if not url.startswith("https://"):
                try:
                    url = notice_links.link_from_href(url)["href"]
                except notice_links.LinkError as e:
                    raise NoticeError(f"a view action must open a page of the app or an https page: {e}") from e
            clean["url"] = url[:500]
        else:
            callback = str(action.get("callback") or "")
            if not _CALLBACK.match(callback):
                raise NoticeError("a callback or reply action needs a callback name")
            params = action.get("params") if action.get("params") is not None else {}
            if not isinstance(params, dict):
                raise NoticeError("action params must be an object")
            clean["callback"] = callback
            clean["params"] = params
        out.append(clean)
    return out


def _data(raw: Any) -> dict[str, Any]:
    """The notice's ``data`` with its ``link`` checked (docs/PUSH.md, Deep links).

    Every notice says where a tap goes: ``data.link`` (from
    ``services.common.notice_links.link``), or an in-app ``data.url`` that is
    read into one. A notice with neither, or with a target no page of the app
    reads, is refused, so a tap never lands on a page that cannot show it.
    ``data.url`` is always rewritten to the link's address for older apps.
    """
    data = dict(raw) if isinstance(raw, dict) else {}
    try:
        if data.get("link") is not None:
            target = notice_links.check(data["link"])
        elif data.get("url"):
            target = notice_links.link_from_href(str(data["url"]))
        else:
            raise NoticeError("a notice needs data.link: where a tap on it goes")
    except notice_links.LinkError as e:
        raise NoticeError(f"data.link: {e}") from e
    data["link"] = target
    data["url"] = target["href"]
    return data


def parse(body: dict[str, Any]) -> dict[str, Any]:
    """The validated notice fields of a notify body. Raises ``NoticeError``."""
    user = str(body.get("user") or "").strip().lower()
    title = str(body.get("title") or "").strip()
    if not user or not title:
        raise NoticeError("user and title required")
    category = str(body.get("category") or DEFAULT_CATEGORY).strip()
    if not _CATEGORY.match(category):
        raise NoticeError("category must be a lowercase name")
    audience = str(body.get("audience") or "all").strip()
    if audience not in AUDIENCES:
        raise NoticeError(f"audience must be one of {list(AUDIENCES)}")
    priority = body.get("priority", 3)
    if isinstance(priority, bool) or not isinstance(priority, int) or not 1 <= priority <= 5:
        raise NoticeError("priority must be a whole number from 1 to 5")
    return {
        "user": user,
        "category": category,
        "kind": str(body.get("kind") or "notice"),
        "title": title[:120],
        "body": str(body.get("body") or "")[:180],
        "priority": priority,
        "tag": str(body.get("tag") or "").strip()[:200] or None,
        "data": _data(body.get("data")),
        "actions": _actions(body.get("actions")),
        "once": str(body.get("once") or "").strip()[:200],
        "audience": audience,
        "replace": body.get("replace") is True,
        # Data-only: for the person's devices to act on, not to read (see deliver).
        "signal": body.get("signal") is True,
    }


def wanted(preferences: dict[str, bool] | None, category: str, priority: int) -> bool:
    """Whether the person receives this category. An SOS (family_safety at
    priority 5) always goes through; unknown categories and an unreadable
    preference set count as on."""
    if category == "family_safety" and priority == 5:
        return True
    return bool((preferences or {}).get(category, True))


async def _identity_get(session: aiohttp.ClientSession, path: str, params: dict | None = None) -> Any:
    resp = await session.get(
        f"{IDENTITY_SVC}{path}",
        params=params,
        headers=internal_headers(),
        timeout=aiohttp.ClientTimeout(total=10.0),
    )
    async with resp:
        if resp.status != 200:
            raise RuntimeError(f"identity {path} answered {resp.status}")
        return await resp.json(content_type=None)


async def identity_settings(session: aiohttp.ClientSession, keys) -> dict[str, str]:
    """The named global settings from identity, trimmed (missing ones left out)."""
    rows = await _identity_get(session, "/api/settings")
    wanted = set(keys)
    return {
        r["key"]: str(r.get("value") or "").strip()
        for r in rows or [] if isinstance(r, dict) and r.get("key") in wanted
    }


async def preferences(session: aiohttp.ClientSession, user: str) -> dict[str, bool] | None:
    cached = _prefs_cache.get(user)
    if cached and cached[0] > time.monotonic():
        return cached[1]
    try:
        data = await _identity_get(session, "/api/internal/push/preferences", {"username": user})
        prefs = data.get("preferences") if isinstance(data, dict) else None
    except Exception as e:
        log.warning("Could not read %s's notification preferences: %s", user, e)
        return None
    _prefs_cache[user] = (time.monotonic() + CACHE_SECONDS, prefs)
    return prefs


async def push_topic(session: aiohttp.ClientSession, user: str) -> str | None:
    """The person's ntfy topic, cached for ``TOPIC_CACHE_SECONDS``."""
    cached = _topic_cache.get(user)
    if cached and cached[0] > time.monotonic():
        return cached[1]
    try:
        data = await _identity_get(session, "/api/internal/push/topic", {"username": user})
    except Exception as e:
        log.warning("Could not read %s's push topic: %s", user, e)
        return None
    topic = str((data or {}).get("topic") or "")
    if topic:
        _topic_cache[user] = (time.monotonic() + TOPIC_CACHE_SECONDS, topic)
    return topic or None


async def admins(session: aiohttp.ClientSession) -> list[str]:
    global _admins_cache
    if _admins_cache and _admins_cache[0] > time.monotonic():
        return _admins_cache[1]
    data = await _identity_get(session, "/api/internal/push/admins")
    names = [str(n).lower() for n in (data or {}).get("admins") or []]
    _admins_cache = (time.monotonic() + CACHE_SECONDS, names)
    return names


async def minors(session: aiohttp.ClientSession) -> set[str]:
    """Every minor's username, from identity's user list (``is_minor``), cached.

    A ``"audience": "parents"`` notice must never reach one of these. Identity
    is the only source of roles; a lookup that fails raises, so the caller can
    refuse rather than risk a parents-only notice reaching a child.
    """
    global _minors_cache
    if _minors_cache and _minors_cache[0] > time.monotonic():
        return _minors_cache[1]
    rows = await _identity_get(session, "/api/users")
    if not isinstance(rows, list):
        raise RuntimeError("identity's user list was not a list")
    names = {
        str(u["username"]).lower()
        for u in rows
        if isinstance(u, dict) and u.get("username") and u.get("is_minor")
    }
    _minors_cache = (time.monotonic() + CACHE_SECONDS, names)
    return names


async def deliver(rc, session: aiohttp.ClientSession, user: str, fields: dict[str, Any]) -> dict[str, Any]:
    """Send one parsed notice to one person. Returns what happened."""
    if not wanted(await preferences(session, user), fields["category"], fields["priority"]):
        return {"dropped": "preference"}
    if fields.get("signal"):
        # A data-only signal (a parent changed a child's screen time, so the
        # phone re-reads its status now): straight to the push stream the
        # phone listens on. It is not a notice to read, so it never enters the
        # notification list or web push, and a missed one is not replayed; the
        # phone also re-reads on wake, on reconnect and every minute.
        topic = await push_topic(session, user)
        signal = {"id": store.new_id(), "user": user, "category": fields["category"], "kind": fields["kind"],
                  "title": fields["title"], "body": fields["body"], "priority": fields["priority"],
                  "tag": fields["tag"], "data": fields["data"], "actions": [], "signal": True,
                  "created_at": utcnow_iso()}
        return {"signal": True, "ntfy": bool(topic) and await ntfy.publish(session, topic, signal)}
    once = fields["once"]
    once_key = f"telemetry:notify_once:{user}:{once}" if once else None
    if once_key and not await rc.set(once_key, "1", nx=True, ex=ONCE_SECONDS):
        return {"duplicate": True}
    notice = {
        "id": store.new_id(),
        "user": user,
        "category": fields["category"],
        "kind": fields["kind"],
        "title": fields["title"],
        "body": fields["body"],
        "priority": fields["priority"],
        "tag": fields["tag"],
        "data": fields["data"],
        "actions": fields["actions"],
        "once": once or None,
        "created_at": utcnow_iso(),
    }
    try:
        if fields.get("replace") and notice["tag"]:
            await store.drop_unread_tagged(rc, user, notice["tag"])
        await store.push_notification(rc, user, {**notice, "read": False})
    except Exception:
        # Not recorded, so not sent: a retry must not count as a duplicate.
        if once_key:
            await rc.delete(once_key)
        raise
    delivered: dict[str, Any] = {}
    try:
        delivered = await push.send_to_user(user, notice["title"], notice["body"], notice["data"],
                                            actions=notice["actions"], tag=notice["tag"])
    except Exception as e:  # the outbox already has it
        log.warning("Push delivery failed for %s: %s", user, e)
    topic = await push_topic(session, user)
    published = bool(topic) and await ntfy.publish(session, topic, notice)
    return {"push": delivered, "ntfy": published}

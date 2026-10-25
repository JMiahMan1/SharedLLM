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

from services.telemetry import ntfy, push, store
from services.telemetry.config import IDENTITY_SVC
from services.telemetry.store import internal_headers, utcnow_iso

log = logging.getLogger("telemetry.notices")

ADMINS = "@admins"
#: System notices: delivered to admins only (main.deliver_notice).
ADMIN_CATEGORY = "admin"
DEFAULT_CATEGORY = "general"
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
        "data": body.get("data") if isinstance(body.get("data"), dict) else {},
        "actions": _actions(body.get("actions")),
        "once": str(body.get("once") or "").strip()[:200],
        "audience": audience,
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
        await store.push_notification(rc, user, {**notice, "read": False})
    except Exception:
        # Not recorded, so not sent: a retry must not count as a duplicate.
        if once_key:
            await rc.delete(once_key)
        raise
    delivered: dict[str, Any] = {}
    try:
        delivered = await push.send_to_user(user, notice["title"], notice["body"], notice["data"])
    except Exception as e:  # the outbox already has it
        log.warning("Push delivery failed for %s: %s", user, e)
    topic = await push_topic(session, user)
    published = bool(topic) and await ntfy.publish(session, topic, notice)
    return {"push": delivered, "ntfy": published}

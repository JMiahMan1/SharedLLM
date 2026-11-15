"""App vetting in telemetry: the apps on children's devices, their public Play
listings, the local model's review of each, and the parents' notices.

* A child's device reports its installed apps (``save_inventory``, from the
  gateway's authenticated, minors-only ``POST /api/apps/inventory``). Each
  device's latest list is kept; a device that stops reporting is forgotten
  after ``APP_INVENTORY_TTL_DAYS``.
* Every package seen there, or in a child's "ask a parent" request, is queued
  for a lookup. One background loop (``catalog_loop``) works the queue: it
  fetches the app's public Play listing (only the package name in the URL, a
  generic user agent, no cookies), at most one request per
  ``APP_CATALOG_MIN_INTERVAL_S``, and backs off (doubling, up to
  ``APP_CATALOG_BACKOFF_MAX_S``) when Google refuses or fails. A request is
  never made while answering anyone: the routes only read what is cached.
* A found listing is read by the household's model (``app_review``); the
  review is kept with the listing's hash and redone when the listing changes.
  Without a configured model the review says so, and is tried again daily.
* After each lookup the child's apps are assessed (``app_vetting.assess``) and
  each parent hears about newly flagged ones at their own notify level, once
  per app and verdict. A request waiting on the lookup gets its notice again,
  with the verdict, replacing the "checking" one.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import aiohttp

from services.common import app_review, app_vetting, play_listing
from services.telemetry import guardian, llm
from services.telemetry.config import (
    APP_CATALOG_BACKOFF_BASE_S,
    APP_CATALOG_BACKOFF_MAX_S,
    APP_CATALOG_ENABLED,
    APP_CATALOG_FETCH_TIMEOUT_S,
    APP_CATALOG_MIN_INTERVAL_S,
    APP_CATALOG_TTL_DAYS,
    APP_INVENTORY_TTL_DAYS,
    APP_REVIEW_TIMEOUT_S,
    JARVIS_ANDROID_PACKAGE,
)

log = logging.getLogger("telemetry.app_catalog")

Deliver = Callable[[dict[str, Any]], Awaitable[Any]]

INVENTORY_PREFIX = "tel:apps:inv:"
DEVICES_PREFIX = "tel:apps:devices:"
LISTING_PREFIX = "tel:apps:listing:"
REVIEW_PREFIX = "tel:apps:review:"
WAITING_PREFIX = "tel:apps:waiting:"
NOTIFIED_PREFIX = "tel:apps:notified:"
LOOKUP_DUE = "tel:apps:due"
REVIEW_DUE = "tel:apps:review-due"
BACKOFF_KEY = "tel:apps:backoff"
LAST_FETCH_KEY = "tel:apps:last-fetch"

MAX_APPS_PER_DEVICE = 500
#: More newly flagged apps than this for one parent at once become one notice.
NOTICE_GROUP_OVER = 3
IDLE_TICK_S = 30.0
#: A failed review is tried again after this, doubling, up to a day.
REVIEW_RETRY_BASE_S = 900
REVIEW_RETRY_MAX_S = 86400
REVIEW_MAX_TOKENS = 900

_fetch_session: aiohttp.ClientSession | None = None


def _now() -> float:
    return time.time()


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).isoformat()


async def _get_json(rc, key: str) -> Any:
    raw = await rc.get(key)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


# --- inventory --------------------------------------------------------------


async def save_inventory(rc, user: str, device: str, body: dict[str, Any]) -> dict[str, Any]:
    """Keep one device's app list (replacing its previous one) and queue a
    lookup for every package whose listing is missing or old. Returns what was
    kept and the packages queued."""
    apps = []
    seen: set[str] = set()
    for raw in (body.get("apps") or [])[:MAX_APPS_PER_DEVICE]:
        app = app_vetting.clean_inventory_app(raw)
        if app is None or app["package"] in seen or app["package"] == JARVIS_ANDROID_PACKAGE:
            continue
        seen.add(app["package"])
        apps.append(app)
    record = {
        "device": device,
        "user": user,
        "reported_at": _iso(_now()),
        "usage_access": bool(body.get("usage_access")),
        "apps": apps,
    }
    ttl = APP_INVENTORY_TTL_DAYS * 86400
    await rc.set(f"{INVENTORY_PREFIX}{user}:{device}", json.dumps(record), ex=ttl)
    await rc.sadd(f"{DEVICES_PREFIX}{user}", device)
    queued = await queue_lookups(rc, [a["package"] for a in apps])
    return {"device": device, "apps": len(apps), "queued": queued}


async def inventories(rc, user: str) -> list[dict[str, Any]]:
    """Every device's latest list for one person (forgotten devices dropped)."""
    out = []
    for device in sorted(await rc.smembers(f"{DEVICES_PREFIX}{user}") or []):
        record = await _get_json(rc, f"{INVENTORY_PREFIX}{user}:{device}")
        if record is None:
            await rc.srem(f"{DEVICES_PREFIX}{user}", device)
            continue
        out.append(record)
    return out


# --- the lookup queue --------------------------------------------------------


def _stale(entry: dict[str, Any] | None, now: float) -> bool:
    if not entry or entry.get("status") in (None, "pending"):
        return True
    fetched = entry.get("fetched_ts") or 0
    return now - fetched >= APP_CATALOG_TTL_DAYS * 86400


async def queue_lookups(rc, packages: list[str], *, first: bool = False) -> list[str]:
    """Queue the packages whose listing is missing or older than the TTL.
    ``first`` puts them ahead of a child's whole inventory (a request is
    waiting on them)."""
    if not APP_CATALOG_ENABLED:
        return []
    now = _now()
    queued = []
    for package in dict.fromkeys(packages):
        if not app_vetting.is_package(package):
            continue
        entry = await _get_json(rc, f"{LISTING_PREFIX}{package}")
        if not _stale(entry, now):
            continue
        if entry is None:
            await rc.set(f"{LISTING_PREFIX}{package}", json.dumps({"status": "pending", "queued_at": _iso(now)}))
        await rc.zadd(LOOKUP_DUE, {package: now - 86400 if first else now})
        queued.append(package)
    return queued


async def backoff_state(rc) -> dict[str, Any] | None:
    state = await _get_json(rc, BACKOFF_KEY)
    if state and state.get("until", 0) > _now():
        return state
    return None


async def _back_off(rc, reason: str) -> dict[str, Any]:
    previous = await _get_json(rc, BACKOFF_KEY) or {}
    failures = int(previous.get("failures") or 0) + 1
    wait = min(APP_CATALOG_BACKOFF_BASE_S * 2 ** (failures - 1), APP_CATALOG_BACKOFF_MAX_S)
    state = {"until": _now() + wait, "failures": failures, "reason": reason, "until_iso": _iso(_now() + wait)}
    await rc.set(BACKOFF_KEY, json.dumps(state))
    log.warning("Google Play lookups paused for %ss after %s failure(s): %s", wait, failures, reason)
    return state


async def _clear_backoff(rc) -> None:
    await rc.delete(BACKOFF_KEY)


def _session() -> aiohttp.ClientSession:
    """A session that never stores or sends a cookie."""
    global _fetch_session
    if _fetch_session is None or _fetch_session.closed:
        _fetch_session = aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar())
    return _fetch_session


async def fetch_page(package: str) -> tuple[int, str]:
    """GET the public listing: (HTTP status, page text). Nothing but the
    package name and a generic browser's headers goes to Google."""
    async with _session().get(
        play_listing.listing_url(package),
        headers=play_listing.REQUEST_HEADERS,
        timeout=aiohttp.ClientTimeout(total=APP_CATALOG_FETCH_TIMEOUT_S),
        allow_redirects=False,
    ) as resp:
        return resp.status, await resp.text() if resp.status == 200 else ""


async def refresh_listing(rc, package: str) -> dict[str, Any] | None:
    """Look one package up. Returns the stored entry, or None when Google
    refused or failed (the package is queued again and lookups back off)."""
    now = _now()
    await rc.set(LAST_FETCH_KEY, str(now))
    try:
        status, page = await fetch_page(package)
    except Exception as exc:  # network trouble: try again later, never guess
        await _back_off(rc, f"{type(exc).__name__}: {exc}")
        await rc.zadd(LOOKUP_DUE, {package: now})
        return None
    if status == 404:
        entry = {"status": "not_on_play", "fetched_ts": now, "fetched_at": _iso(now),
                 "reason": "Google Play has no public listing for this app"}
    elif status == 200:
        try:
            listing = play_listing.parse(page, package)
        except play_listing.ListingError as exc:
            # A page this parser cannot read: kept as an error with the reason,
            # looked at again with the next refresh, never half-read.
            entry = {"status": "error", "fetched_ts": now, "fetched_at": _iso(now), "reason": str(exc)}
            log.warning("Play listing for %s could not be read: %s", package, exc)
        else:
            entry = {"status": "found", "fetched_ts": now, "fetched_at": _iso(now), "hash": listing["hash"],
                     "listing": listing}
    else:
        # 429, 5xx, a redirect to a consent page: Google is not answering now.
        await _back_off(rc, f"Google Play answered HTTP {status}")
        await rc.zadd(LOOKUP_DUE, {package: now})
        return None
    await _clear_backoff(rc)
    await rc.set(f"{LISTING_PREFIX}{package}", json.dumps(entry))
    return entry


async def needs_review(rc, entry: dict[str, Any], package: str) -> bool:
    """A found listing whose review is missing or was made of another version."""
    if entry.get("status") != "found":
        return False
    review = await _get_json(rc, f"{REVIEW_PREFIX}{package}") or {}
    return review.get("status") != "ready" or review.get("listing_hash") != entry.get("hash")


async def _model(session) -> tuple[str, str]:
    """The household model (``llm_local_url`` + ``guardian_monitor_model``);
    raises naming the missing setting."""
    settings = await guardian._model_settings(session)
    url, model = settings.get("llm_local_url") or "", settings.get("guardian_monitor_model") or ""
    if not url or not model:
        raise LookupError("no local model configured (set llm_local_url and guardian_monitor_model)")
    return url, model


async def refresh_review(rc, session, package: str) -> dict[str, Any] | None:
    """Have the local model read a found listing; keep the review, or the
    reason it could not be made (bad JSON is a failed review, never a guess)."""
    entry = await _get_json(rc, f"{LISTING_PREFIX}{package}") or {}
    if entry.get("status") != "found":
        return None
    listing = entry["listing"]
    previous = await _get_json(rc, f"{REVIEW_PREFIX}{package}") or {}
    now = _now()
    attempts = int(previous.get("attempts") or 0) if previous.get("listing_hash") == entry["hash"] else 0
    try:
        url, model = await _model(session)
    except LookupError as exc:
        review = {"status": "no_model", "listing_hash": entry["hash"], "reason": str(exc), "at": _iso(now)}
        await rc.set(f"{REVIEW_PREFIX}{package}", json.dumps(review))
        await rc.zadd(REVIEW_DUE, {package: now + REVIEW_RETRY_MAX_S})
        return review
    except Exception as exc:
        review = {"status": "failed", "listing_hash": entry["hash"], "reason": f"model settings could not be read: {exc}",
                  "at": _iso(now), "attempts": attempts + 1}
        await rc.set(f"{REVIEW_PREFIX}{package}", json.dumps(review))
        await rc.zadd(REVIEW_DUE, {package: now + REVIEW_RETRY_BASE_S})
        return review
    payload = {
        "model": model,
        "messages": app_review.messages(listing),
        "temperature": 0,
        "max_tokens": REVIEW_MAX_TOKENS,
        "stream": False,
    }
    try:
        content = await llm.chat_content(
            session, f"{url.rstrip('/')}/v1/chat/completions", payload, APP_REVIEW_TIMEOUT_S, what="the local model"
        )
        parsed = app_review.parse(content, listing)
    except Exception as exc:
        attempts += 1
        review = {"status": "failed", "listing_hash": entry["hash"], "model": model, "at": _iso(now),
                  "reason": str(exc) or type(exc).__name__, "attempts": attempts}
        await rc.set(f"{REVIEW_PREFIX}{package}", json.dumps(review))
        await rc.zadd(REVIEW_DUE, {package: now + min(REVIEW_RETRY_BASE_S * 2 ** (attempts - 1), REVIEW_RETRY_MAX_S)})
        log.warning("App review of %s failed (%s): %s", package, attempts, review["reason"])
        return review
    review = {"status": "ready", "listing_hash": entry["hash"], "model": model, "at": _iso(now), **parsed}
    await rc.set(f"{REVIEW_PREFIX}{package}", json.dumps(review))
    return review


async def catalog_entry(rc, package: str) -> dict[str, Any]:
    """What is known about one package, for a card: the listing without its
    description and reviews, and the review."""
    listing = await _get_json(rc, f"{LISTING_PREFIX}{package}")
    review = await _get_json(rc, f"{REVIEW_PREFIX}{package}")
    if listing is None and not APP_CATALOG_ENABLED:
        listing = {"status": "off", "reason": "Google Play lookups are switched off on this server (APP_CATALOG_ENABLED)"}
    if listing and listing.get("listing"):
        listing = {**listing, "listing": play_listing.summary(listing["listing"])}
    return {"listing": listing, "review": review}


async def catalog(rc, packages: list[str]) -> dict[str, dict[str, Any]]:
    return {p: await catalog_entry(rc, p) for p in dict.fromkeys(packages) if app_vetting.is_package(p)}


# --- assessing a child's apps ---------------------------------------------


def _policy(child: dict[str, Any]) -> dict[str, Any]:
    return app_vetting.effective_policy(child.get("app_policy"), child.get("birth_date"), guardian.household_now().date())


async def merged_apps(rc, user: str, usage_days: int = 60) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """One row per app across a person's devices, last use including what the
    daily screen-time reports saw. Returns (apps, device summaries)."""
    from services.telemetry import store

    records = await inventories(rc, user)
    apps = app_vetting.merge_inventories(records)
    last_seen: dict[str, int] = {}
    for day in await store.get_usage(rc, user, usage_days):
        for row in day.get("apps") or []:
            last = row.get("last_use")
            if isinstance(row, dict) and isinstance(last, (int, float)) and not isinstance(last, bool):
                last_seen[row.get("package")] = max(last_seen.get(row.get("package"), 0), int(last))
    for app in apps:
        seen = last_seen.get(app["package"])
        if seen and (app.get("last_used") is None or seen > app["last_used"]):
            app["last_used"] = seen
    devices = [{"device": r["device"], "reported_at": r.get("reported_at"), "apps": len(r.get("apps") or []),
                "usage_access": bool(r.get("usage_access"))} for r in records]
    return apps, devices


def _verdict_body(verdict: dict[str, Any], flags: list[dict[str, Any]]) -> str:
    parts = [f["text"] for f in flags[:2]]
    facts = app_vetting.fact_words(verdict)
    if facts:
        parts.append(facts)
    return ". ".join(p.rstrip(".") for p in parts) + "."


def _flag_notice(parent: str, child: dict[str, Any], verdict: dict[str, Any], flags: list[dict[str, Any]]) -> dict[str, Any]:
    name = child.get("display_name") or child["username"]
    worst = app_vetting.max_level([f["level"] for f in flags])
    always = any(f["category"] in guardian.concern_monitor.ALWAYS_ON for f in flags)
    return {
        "user": parent,
        "category": "family_safety",
        "kind": "app_flag",
        "priority": 5 if always else 4 if worst == "severe" else 3,
        "title": f"{verdict['label']} on {name}'s {guardian._device(child)}",
        "body": _verdict_body(verdict, flags),
        "tag": f"app:{child['username']}:{verdict['package']}",
        "once": f"app:{child['username']}:{verdict['package']}:{verdict['fingerprint']}",
        "audience": "parents",
        "data": {"url": f"/guardian?member={child['username']}&area=overview", "child": child["username"],
                 "package": verdict["package"], "level": worst},
    }


def _group_notice(parent: str, child: dict[str, Any], verdicts: list[dict[str, Any]]) -> dict[str, Any]:
    name = child.get("display_name") or child["username"]
    names = ", ".join(v["label"] for v in verdicts[:4]) + (" and more" if len(verdicts) > 4 else "")
    return {
        "user": parent,
        "category": "family_safety",
        "kind": "app_flag",
        "priority": 3,
        "title": f"{len(verdicts)} apps on {name}'s {guardian._device(child)} need a look",
        "body": f"{names}. Open the Apps card to see why.",
        "tag": f"apps:{child['username']}",
        "audience": "parents",
        "data": {"url": f"/guardian?member={child['username']}&area=overview", "child": child["username"]},
    }


async def notify_child(rc, child: dict[str, Any], deliver: Deliver, packages: list[str] | None = None) -> int:
    """Tell each parent about this child's newly flagged installed apps, at
    their own level, once per app and verdict. Returns notices sent."""
    apps, _devices = await merged_apps(rc, child["username"])
    if packages is not None:
        wanted = set(packages)
        apps = [a for a in apps if a["package"] in wanted]
    if not apps:
        return 0
    policy = _policy(child)
    verdicts = []
    for app in apps:
        entry = await catalog_entry(rc, app["package"])
        if (entry.get("listing") or {}).get("status") in (None, "pending"):
            continue  # still checking: the lookup's own pass decides
        verdicts.append(app_vetting.assess(app, entry, policy))
    sent = 0
    key = f"{NOTIFIED_PREFIX}{child['username']}"
    already = set(await rc.smembers(key) or [])
    for parent in child.get("parents") or []:
        level = guardian.concern_monitor.parent_level(child, parent)
        fresh = []
        for verdict in verdicts:
            flags = app_vetting.notifies(verdict, level)
            marker = f"{parent}|{verdict['package']}|{verdict['fingerprint']}"
            if flags and marker not in already:
                fresh.append((verdict, flags, marker))
        if not fresh:
            continue
        notices = (
            [_group_notice(parent, child, [v for v, _f, _m in fresh])] if len(fresh) > NOTICE_GROUP_OVER
            else [_flag_notice(parent, child, v, f) for v, f, _m in fresh]
        )
        for notice in notices:
            try:
                await deliver(notice)
                sent += 1
            except Exception as exc:
                log.warning("app notice to %s failed: %s", parent, exc)
        await rc.sadd(key, *[m for _v, _f, m in fresh])
    return sent


# --- requests waiting on a lookup -------------------------------------------


async def wait_for(rc, package: str, waiting: dict[str, Any]) -> None:
    """Remember a request whose notice should be sent again with the verdict."""
    rows = await _get_json(rc, f"{WAITING_PREFIX}{package}") or []
    rows = [r for r in rows if r.get("request_id") != waiting.get("request_id")] + [waiting]
    await rc.set(f"{WAITING_PREFIX}{package}", json.dumps(rows[-20:]), ex=7 * 86400)


def request_notice(parent: str, child: dict[str, Any], waiting: dict[str, Any], verdict: dict[str, Any]) -> dict[str, Any]:
    """The "new app to approve" notice again, now with the verdict (replaces
    the one that said it was being checked)."""
    from services.common import notify

    name = child.get("display_name") or child["username"]
    request_id = waiting.get("request_id")
    return notify.notice(
        parent, "family_safety", "New app to approve",
        f"{name} installed {verdict['label']} and is waiting for your OK. {app_vetting.verdict_line(verdict)}",
        url=f"/family/member/{child['username']}",
        tag=f"guard-app:{child['username']}:{verdict['package']}",
        replace=True,
        actions=[
            notify.callback_action("Approve", "guard_decide", {"id": request_id, "approve": True}),
            notify.callback_action("Disapprove", "guard_decide", {"id": request_id, "approve": False}),
        ] if request_id is not None else None,
    )


def settled(entry: dict[str, Any]) -> bool:
    """Whether a package's verdict is in: looked up, and for a found listing
    reviewed (or the review failed with a reason)."""
    listing = entry.get("listing") or {}
    if listing.get("status") in (None, "pending"):
        return False
    if listing.get("status") != "found":
        return True
    review = entry.get("review") or {}
    return review.get("listing_hash") == listing.get("hash") and review.get("status") in ("ready", "failed", "no_model")


async def _answer_waiting(rc, children: dict[str, dict[str, Any]], package: str, deliver: Deliver) -> None:
    rows = await _get_json(rc, f"{WAITING_PREFIX}{package}") or []
    if not rows:
        return
    entry = await catalog_entry(rc, package)
    if not settled(entry):
        return  # the review is still coming: answer once it is in
    await rc.delete(f"{WAITING_PREFIX}{package}")
    for row in rows:
        child = children.get(str(row.get("child") or ""))
        if not child:
            continue
        verdict = app_vetting.assess({"package": package, "label": row.get("label") or ""}, entry, _policy(child))
        for parent in child.get("parents") or []:
            try:
                await deliver(request_notice(parent, child, row, verdict))
            except Exception as exc:
                log.warning("app request notice to %s failed: %s", parent, exc)


# --- the loop ----------------------------------------------------------------


async def after_update(rc, session, package: str, deliver: Deliver) -> None:
    """A package's listing or review changed: tell whoever is waiting, and
    each child holding it whose parents should now hear."""
    children = await guardian.children(session)
    await _answer_waiting(rc, children, package, deliver)
    for child in children.values():
        holders = {a["package"] for r in await inventories(rc, child["username"]) for a in r.get("apps") or []}
        if package in holders:
            await notify_child(rc, child, deliver, [package])


async def step(rc, session, deliver: Deliver) -> str:
    """One unit of work: a review that is due, else one Play lookup when the
    interval and any backoff allow. Returns "fetched" (a request went to
    Google), "reviewed" or "idle"."""
    now = _now()
    reviews = await rc.zrangebyscore(REVIEW_DUE, 0, now, start=0, num=1)
    if reviews:
        package = reviews[0]
        await rc.zrem(REVIEW_DUE, package)
        if await refresh_review(rc, session, package) is not None:
            await after_update(rc, session, package, deliver)
        return "reviewed"
    if not APP_CATALOG_ENABLED or await backoff_state(rc):
        return "idle"
    last = await rc.get(LAST_FETCH_KEY)
    if last and now - float(last) < APP_CATALOG_MIN_INTERVAL_S:
        return "idle"
    due = await rc.zrangebyscore(LOOKUP_DUE, 0, now, start=0, num=1)
    if not due:
        return "idle"
    package = due[0]
    await rc.zrem(LOOKUP_DUE, package)
    entry = await refresh_listing(rc, package)
    if entry is not None:
        if await needs_review(rc, entry, package):
            await refresh_review(rc, session, package)
        await after_update(rc, session, package, deliver)
    return "fetched"


#: Seconds the loop waits after each kind of step.
_PAUSE = {"fetched": APP_CATALOG_MIN_INTERVAL_S, "reviewed": 1.0, "idle": IDLE_TICK_S}


async def catalog_loop(redis_factory, session_factory, deliver: Deliver) -> None:
    rc = redis_factory()
    while True:
        outcome = "idle"
        try:
            outcome = await step(rc, session_factory(), deliver)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.error("app catalog tick failed: %s", exc)
        await asyncio.sleep(_PAUSE[outcome])

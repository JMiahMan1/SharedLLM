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
* A found listing's extra reviews (the newest, and the one- and two-star
  ones: ``play_listing.REVIEW_BATCHES``) are read one request at a time on
  the same interval and backoff as the listing; once all are in, the review
  is redone with them (the entry's hash moves).
* Common Sense Media's review of each found app (``common_sense``) is looked
  for by the app's Play title, on its own interval, backoff and TTL, while
  the hub's Common Sense Media integration is set up.
* The SHA-256 of each app file a device reports is looked up on abuse.ch
  MalwareBazaar (``malwarebazaar``) with the Auth-Key of the hub's abuse.ch
  MalwareBazaar integration, on its own interval and backoff; without one
  every app says the check is not set up.
* Both integrations are read from the hub (``GET /hub/integrations/{domain}``
  with the internal secret) for each round of lookups, cached a minute, so a
  change on the Devices page applies within a minute. While the hub cannot be
  asked both count as not set up, which is logged once until it answers.
* After each lookup the child's apps are assessed (``app_vetting.assess``) and
  each parent hears about newly flagged ones at their own notify level, once
  per app and verdict. A request waiting on the lookup gets its notice again,
  with the verdict, replacing the "checking" one.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import aiohttp

from services.common import app_review, app_vetting, common_sense, malwarebazaar, play_listing
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
    COMMON_SENSE_BACKOFF_BASE_S,
    COMMON_SENSE_BACKOFF_MAX_S,
    COMMON_SENSE_MIN_INTERVAL_S,
    COMMON_SENSE_TTL_DAYS,
    HOMEHUB_URL,
    INTERNAL_SECRET,
    JARVIS_ANDROID_PACKAGE,
    MALWAREBAZAAR_BACKOFF_BASE_S,
    MALWAREBAZAAR_BACKOFF_MAX_S,
    MALWAREBAZAAR_MIN_INTERVAL_S,
    MALWAREBAZAAR_TTL_DAYS,
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
MORE_REVIEWS_DUE = "tel:apps:more-reviews-due"
CSM_PREFIX = "tel:apps:csm:"
CSM_DUE = "tel:apps:csm-due"
CSM_BACKOFF_KEY = "tel:apps:csm-backoff"
CSM_LAST_FETCH_KEY = "tel:apps:csm-last-fetch"
CSM_STATE_KEY = "tel:apps:csm-state"
MB_PREFIX = "tel:apps:mb:"
MB_DUE = "tel:apps:mb-due"
MB_BACKOFF_KEY = "tel:apps:mb-backoff"
MB_LAST_FETCH_KEY = "tel:apps:mb-last-fetch"
MB_STATE_KEY = "tel:apps:mb-state"

#: The hub integrations these checks read (homehub; cached for a minute).
MB_DOMAIN = "malwarebazaar"
CSM_DOMAIN = "common_sense_media"
SETTINGS_CACHE_S = 60.0
HUB_TIMEOUT_S = 5.0
#: Where an admin turns Common Sense lookups on (the Devices page).
CSM_NOT_SET_UP = ("Common Sense Media lookups are off: add the Common Sense Media integration "
                  "(Devices > Add & fix > Add by address > Cloud accounts)")
MB_FETCH_TIMEOUT_S = 20.0

MAX_APPS_PER_DEVICE = 500
#: More newly flagged apps than this for one parent at once become one notice.
NOTICE_GROUP_OVER = 3
IDLE_TICK_S = 30.0
#: A failed review is tried again after this, doubling, up to a day.
REVIEW_RETRY_BASE_S = 900
REVIEW_RETRY_MAX_S = 86400
REVIEW_MAX_TOKENS = 900

_fetch_session: aiohttp.ClientSession | None = None
_settings_cache: tuple[float, dict[str, Any]] | None = None
#: The hub could not be asked last time (so the next failure is not logged again).
_hub_down = False


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
    await queue_hashes(rc, [a["apk_sha256"] for a in apps if a.get("apk_sha256")])
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
        if not app_vetting.is_package(package) or package == JARVIS_ANDROID_PACKAGE:
            continue
        entry = await _get_json(rc, f"{LISTING_PREFIX}{package}")
        if not _stale(entry, now):
            continue
        if entry is None:
            await rc.set(f"{LISTING_PREFIX}{package}", json.dumps({"status": "pending", "queued_at": _iso(now)}))
        await rc.zadd(LOOKUP_DUE, {package: now - 86400 if first else now})
        queued.append(package)
    return queued


async def backoff_state(rc, key: str = BACKOFF_KEY) -> dict[str, Any] | None:
    state = await _get_json(rc, key)
    if state and state.get("until", 0) > _now():
        return state
    return None


#: Each source's backoff: (key, base, maximum, what the log calls it).
_BACKOFFS = {
    BACKOFF_KEY: (APP_CATALOG_BACKOFF_BASE_S, APP_CATALOG_BACKOFF_MAX_S, "Google Play lookups"),
    CSM_BACKOFF_KEY: (COMMON_SENSE_BACKOFF_BASE_S, COMMON_SENSE_BACKOFF_MAX_S, "Common Sense Media lookups"),
    MB_BACKOFF_KEY: (MALWAREBAZAAR_BACKOFF_BASE_S, MALWAREBAZAAR_BACKOFF_MAX_S, "MalwareBazaar lookups"),
}


async def _back_off(rc, reason: str, key: str = BACKOFF_KEY) -> dict[str, Any]:
    base, most, what = _BACKOFFS[key]
    previous = await _get_json(rc, key) or {}
    failures = int(previous.get("failures") or 0) + 1
    wait = min(base * 2 ** (failures - 1), most)
    state = {"until": _now() + wait, "failures": failures, "reason": reason, "until_iso": _iso(_now() + wait)}
    await rc.set(key, json.dumps(state))
    log.warning("%s paused for %ss after %s failure(s): %s", what, wait, failures, reason)
    return state


async def _clear_backoff(rc, key: str = BACKOFF_KEY) -> None:
    await rc.delete(key)


async def _interval_ok(rc, key: str, interval: float) -> bool:
    last = await rc.get(key)
    return not (last and _now() - float(last) < interval)


async def hub_integration(session, domain: str) -> dict[str, Any]:
    """``GET /hub/integrations/{domain}`` with the internal secret: ``{configured, data}``. Raises when
    the hub does not answer 200."""
    async with session.get(
        f"{HOMEHUB_URL}/hub/integrations/{domain}", headers={"Authorization": f"Bearer {INTERNAL_SECRET}"},
        timeout=aiohttp.ClientTimeout(total=HUB_TIMEOUT_S),
    ) as resp:
        if resp.status != 200:
            raise RuntimeError(f"homehub answered HTTP {resp.status}")
        body = await resp.json(content_type=None)
    return body if isinstance(body, dict) else {}


async def check_settings(session) -> dict[str, Any]:
    """``{"malwarebazaar_key": str, "common_sense": bool}`` from the hub's integrations (cached a minute).

    The key is the abuse.ch MalwareBazaar integration's Auth-Key, empty without one; Common Sense
    is on while its integration is set up. A hub that cannot be asked counts as neither set up for
    this pass, logged once until it answers again; never raises."""
    global _settings_cache, _hub_down
    if _settings_cache and _settings_cache[0] > time.monotonic():
        return _settings_cache[1]
    try:
        mb = await hub_integration(session, MB_DOMAIN)
        csm = await hub_integration(session, CSM_DOMAIN)
    except Exception as exc:
        if not _hub_down:
            log.warning("homehub could not be asked for the app check integrations (%s); "
                        "treating them as not set up until it answers", type(exc).__name__)
        _hub_down = True
        found: dict[str, Any] = {"malwarebazaar_key": "", "common_sense": False, "hub_down": True}
    else:
        if _hub_down:
            log.info("homehub answers again; the app check integrations are read from it")
        _hub_down = False
        key = str((mb.get("data") or {}).get("auth_key") or "").strip() if mb.get("configured") else ""
        found = {"malwarebazaar_key": key, "common_sense": bool(csm.get("configured"))}
    _settings_cache = (time.monotonic() + SETTINGS_CACHE_S, found)
    return found


def common_sense_enabled(settings: dict[str, Any]) -> bool:
    """On while the hub's Common Sense Media integration is set up."""
    return bool(settings.get("common_sense"))


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


async def fetch_reviews(package: str, batch: str) -> tuple[int, str]:
    """POST for one extra page of reviews, as the Play page itself does: the
    package name, the sort and the star filter, a generic browser's headers."""
    url, form, headers = play_listing.reviews_request(package, batch)
    async with _session().post(
        url, data=form, headers=headers,
        timeout=aiohttp.ClientTimeout(total=APP_CATALOG_FETCH_TIMEOUT_S), allow_redirects=False,
    ) as resp:
        return resp.status, await resp.text() if resp.status == 200 else ""


async def fetch_common_sense(url: str) -> tuple[int, str, str | None]:
    """GET one Common Sense Media page: (status, text, redirect location).
    Only the app's name is in the URL; generic headers, no cookies."""
    async with _session().get(
        url, headers=common_sense.REQUEST_HEADERS,
        timeout=aiohttp.ClientTimeout(total=APP_CATALOG_FETCH_TIMEOUT_S), allow_redirects=False,
    ) as resp:
        return resp.status, (await resp.text() if resp.status == 200 else ""), resp.headers.get("Location")


async def query_malwarebazaar(sha256: str, auth_key: str) -> tuple[int, Any]:
    """POST one hash to MalwareBazaar: (status, JSON body or None). Only the
    hash and the household's Auth-Key go out; the key is never logged."""
    async with _session().post(
        malwarebazaar.API_URL, data=malwarebazaar.request_form(sha256),
        headers=malwarebazaar.request_headers(auth_key),
        timeout=aiohttp.ClientTimeout(total=MB_FETCH_TIMEOUT_S), allow_redirects=False,
    ) as resp:
        try:
            body = await resp.json(content_type=None)
        except Exception:
            body = None
        return resp.status, body


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
                     "page_hash": listing["hash"], "listing": listing,
                     "more_reviews": {"pending": list(play_listing.REVIEW_BATCHES)}}
    else:
        # 429, 5xx, a redirect to a consent page: Google is not answering now.
        await _back_off(rc, f"Google Play answered HTTP {status}")
        await rc.zadd(LOOKUP_DUE, {package: now})
        return None
    await _clear_backoff(rc)
    await rc.set(f"{LISTING_PREFIX}{package}", json.dumps(entry))
    if entry["status"] == "found":
        await rc.zadd(MORE_REVIEWS_DUE, {package: now})
        await queue_common_sense(rc, package, entry["listing"].get("title") or "")
    return entry


async def refresh_more_reviews(rc, package: str) -> dict[str, Any] | None:
    """Read the next extra page of reviews for a found listing (one request).
    Returns the entry once every page is in (its hash then covers them, so
    the review is redone), else None."""
    entry = await _get_json(rc, f"{LISTING_PREFIX}{package}") or {}
    more = entry.get("more_reviews") or {}
    pending = list(more.get("pending") or [])
    if entry.get("status") != "found" or not pending:
        return None
    batch = pending[0]
    now = _now()
    await rc.set(LAST_FETCH_KEY, str(now))
    try:
        status, body = await fetch_reviews(package, batch)
    except Exception as exc:
        await _back_off(rc, f"{type(exc).__name__}: {exc}")
        await rc.zadd(MORE_REVIEWS_DUE, {package: now})
        return None
    if status != 200:
        await _back_off(rc, f"Google Play answered HTTP {status} for reviews")
        await rc.zadd(MORE_REVIEWS_DUE, {package: now})
        return None
    await _clear_backoff(rc)
    try:
        more[batch] = {"reviews": play_listing.parse_reviews(body, batch)}
    except play_listing.ListingError as exc:
        # Kept with its reason; the other pages and the review go on without it.
        more[batch] = {"error": str(exc), "reviews": []}
        log.warning("Play reviews (%s) for %s could not be read: %s", batch, package, exc)
    more["pending"] = pending[1:]
    entry["more_reviews"] = more
    done = not more["pending"]
    if done:
        entry["hash"] = _combined_hash(entry.get("page_hash") or entry.get("hash") or "", more)
    else:
        await rc.zadd(MORE_REVIEWS_DUE, {package: now})
    await rc.set(f"{LISTING_PREFIX}{package}", json.dumps(entry))
    return entry if done else None


def _combined_hash(page_hash: str, more: dict[str, Any]) -> str:
    return hashlib.sha256(f"{page_hash}|{play_listing.more_reviews_hash(more)}".encode()).hexdigest()[:24]


def reviews_read(entry: dict[str, Any]) -> dict[str, Any]:
    """How many reviews of each kind the local model reads, for the card."""
    more = entry.get("more_reviews") or {}

    def count(batch: str) -> int:
        return len((more.get(batch) or {}).get("reviews") or [])

    return {
        "relevant": len((entry.get("listing") or {}).get("reviews") or []),
        "newest": count("newest"),
        "lowest": count("one_star") + count("two_star"),
        # An entry kept from before extra reviews were read has none yet.
        "state": "reading" if more.get("pending") else ("done" if more else "not_read"),
        "errors": {b: (more.get(b) or {}).get("error") for b in play_listing.REVIEW_BATCHES
                   if (more.get(b) or {}).get("error")},
    }


# --- Common Sense Media ------------------------------------------------------


def _csm_stale(entry: dict[str, Any] | None, now: float) -> bool:
    if not entry:
        return True
    if entry.get("status") == "pending":
        return False
    return now - (entry.get("checked_ts") or 0) >= COMMON_SENSE_TTL_DAYS * 86400


async def queue_common_sense(rc, package: str, name: str) -> bool:
    """Queue a Common Sense lookup for a found app by its Play title."""
    if package == JARVIS_ANDROID_PACKAGE or not name.strip():
        return False
    now = _now()
    entry = await _get_json(rc, f"{CSM_PREFIX}{package}")
    if not _csm_stale(entry, now):
        return False
    candidates = common_sense.candidate_urls(name)
    await rc.set(f"{CSM_PREFIX}{package}", json.dumps({
        "status": "pending", "name": name[:120], "candidates": candidates, "tried": 0, "queued_at": _iso(now),
        # A previous answer stays on the card until the new one is in.
        "previous": {k: (entry or {}).get(k) for k in ("status", "review", "reason", "checked_at")}
        if entry and entry.get("status") != "pending" else None,
    }))
    await rc.zadd(CSM_DUE, {package: now})
    return True


async def refresh_common_sense(rc, package: str) -> dict[str, Any] | None:
    """Try the next candidate page for one app (one request). Returns the
    entry when it is settled (found, not found or a parse error)."""
    entry = await _get_json(rc, f"{CSM_PREFIX}{package}") or {}
    if entry.get("status") != "pending":
        return None
    candidates = list(entry.get("candidates") or [])
    tried = int(entry.get("tried") or 0)
    now = _now()
    if tried >= len(candidates):
        settled_entry = {"status": "not_found", "checked_ts": now, "checked_at": _iso(now), "name": entry.get("name"),
                         "reason": "Common Sense Media has no review under this app's name"}
        await rc.set(f"{CSM_PREFIX}{package}", json.dumps(settled_entry))
        return settled_entry
    url = candidates[tried]
    await rc.set(CSM_LAST_FETCH_KEY, str(now))
    try:
        status, page, location = await fetch_common_sense(url)
    except Exception as exc:
        await _back_off(rc, f"{type(exc).__name__}: {exc}", CSM_BACKOFF_KEY)
        await rc.zadd(CSM_DUE, {package: now})
        return None
    result: dict[str, Any] | None = None
    if status == 200:
        await _clear_backoff(rc, CSM_BACKOFF_KEY)
        try:
            review = common_sense.parse(page, url)
        except common_sense.CommonSenseError as exc:
            result = {"status": "error", "reason": f"The Common Sense Media page could not be read: {exc}",
                      "url": url}
            log.warning("Common Sense page %s for %s could not be read: %s", url, package, exc)
        else:
            if common_sense.same_app(review.get("name"), entry.get("name") or ""):
                result = {"status": "found", "review": review}
    elif status in (301, 302, 303, 307, 308):
        await _clear_backoff(rc, CSM_BACKOFF_KEY)
        target = common_sense.redirect_target(location)
        if target and target not in candidates and len(candidates) <= common_sense.MAX_CANDIDATES:
            candidates.insert(tried + 1, target)
    elif status in (404, 410):
        await _clear_backoff(rc, CSM_BACKOFF_KEY)
    else:
        await _back_off(rc, f"Common Sense Media answered HTTP {status}", CSM_BACKOFF_KEY)
        await rc.zadd(CSM_DUE, {package: now})
        return None
    if result is not None:
        settled_entry = {**result, "checked_ts": now, "checked_at": _iso(now), "name": entry.get("name")}
        await rc.set(f"{CSM_PREFIX}{package}", json.dumps(settled_entry))
        return settled_entry
    entry.update(candidates=candidates, tried=tried + 1)
    await rc.set(f"{CSM_PREFIX}{package}", json.dumps(entry))
    await rc.zadd(CSM_DUE, {package: now})
    return None


async def common_sense_entry(rc, package: str) -> dict[str, Any] | None:
    """What the card shows of the Common Sense lookup. While a refresh is
    pending the previous answer is shown; switched off says so."""
    state = await _get_json(rc, CSM_STATE_KEY) or {}
    if state.get("enabled") is False:
        return {"status": "off", "reason": CSM_NOT_SET_UP}
    entry = await _get_json(rc, f"{CSM_PREFIX}{package}")
    if entry is None:
        return None
    if entry.get("status") == "pending":
        previous = entry.get("previous")
        return previous if previous and previous.get("status") else {"status": "checking", "reason": None}
    return {k: entry.get(k) for k in ("status", "review", "reason", "checked_at")}


# --- MalwareBazaar -----------------------------------------------------------


async def queue_hashes(rc, hashes: list[str]) -> list[str]:
    """Queue app file hashes with no recent MalwareBazaar answer."""
    now = _now()
    queued = []
    for sha in dict.fromkeys(hashes):
        result = await _get_json(rc, f"{MB_PREFIX}{sha}")
        if result and result.get("status") == "malware":
            continue
        if result and result.get("status") == "not_known" \
                and now - (result.get("checked_ts") or 0) < MALWAREBAZAAR_TTL_DAYS * 86400:
            continue
        await rc.zadd(MB_DUE, {sha: now})
        queued.append(sha)
    return queued


def _key_fingerprint(key: str) -> str:
    """A short fingerprint telling one key from another (never the key)."""
    return hashlib.sha256(key.encode()).hexdigest()[:12]


async def refresh_malware(rc, auth_key: str) -> str:
    """Look the next due hash up (one request). Returns "fetched" or "idle"."""
    now = _now()
    due = await rc.zrangebyscore(MB_DUE, 0, now, start=0, num=1)
    if not due:
        return "idle"
    sha = due[0]
    await rc.zrem(MB_DUE, sha)
    await rc.set(MB_LAST_FETCH_KEY, str(now))
    try:
        status, body = await query_malwarebazaar(sha, auth_key)
        result = malwarebazaar.parse_response(status, body, sha)
    except malwarebazaar.AuthKeyRejected as exc:
        await rc.set(MB_STATE_KEY, json.dumps({"state": "key_rejected", "reason": str(exc),
                                               "key_fp": _key_fingerprint(auth_key), "at": _iso(now)}))
        await _back_off(rc, str(exc), MB_BACKOFF_KEY)
        await rc.zadd(MB_DUE, {sha: now})
        return "fetched"
    except Exception as exc:
        # Never the exception's repr of the request: it could carry headers.
        reason = str(exc) if isinstance(exc, LookupError) else f"{type(exc).__name__}"
        await rc.set(f"{MB_PREFIX}{sha}", json.dumps({"status": "error", "reason": reason, "checked_ts": now,
                                                      "checked_at": _iso(now)}))
        await _back_off(rc, reason, MB_BACKOFF_KEY)
        await rc.zadd(MB_DUE, {sha: now})
        return "fetched"
    await _clear_backoff(rc, MB_BACKOFF_KEY)
    await rc.set(MB_STATE_KEY, json.dumps({"state": "ready", "key_fp": _key_fingerprint(auth_key), "at": _iso(now)}))
    await rc.set(f"{MB_PREFIX}{sha}", json.dumps({**result, "checked_ts": now, "checked_at": _iso(now)}))
    return "fetched"


async def malware_check(rc, app: dict[str, Any], state: dict[str, Any] | None = None) -> dict[str, Any]:
    """One app's MalwareBazaar state for the card and the verdict:
    ``{"state", "reason", ...}`` where state is malware, not_known, checking,
    not_set_up, error, skipped (the device did not hash it) or no_hash (the
    device's app is older than the check)."""
    hashes = app.get("apk_hashes") or ([app["apk_sha256"]] if app.get("apk_sha256") else [])
    if not hashes:
        why = {
            "too_large": "The app file is too large to fingerprint on the device",
            "unreadable": "The device could not read the app file",
            "deferred": "The device fingerprints it on a later report",
        }.get(app.get("apk_hash_state") or "")
        if why:
            return {"state": "skipped" if app.get("apk_hash_state") != "deferred" else "checking", "reason": why}
        return {"state": "no_hash", "reason": "This device's Jarvis app does not report app fingerprints yet"}
    results = [await _get_json(rc, f"{MB_PREFIX}{sha}") or {} for sha in hashes]
    for sha, result in zip(hashes, results, strict=True):
        if result.get("status") == "malware":
            return {"state": "malware", "sha256": sha, "reason": None, "checked_at": result.get("checked_at"),
                    **{k: result.get(k) for k in ("signature", "file_type", "first_seen", "tags", "link")}}
    if all(r.get("status") == "not_known" for r in results):
        return {"state": "not_known", "reason": "Not a known malware sample on MalwareBazaar",
                "checked_at": min(r.get("checked_at") or "" for r in results) or None}
    state = state if state is not None else (await _get_json(rc, MB_STATE_KEY) or {})
    if state.get("state") == "not_set_up":
        return {"state": "not_set_up", "reason": malwarebazaar.NOT_SET_UP}
    if state.get("state") == "key_rejected":
        return {"state": "error", "reason": state.get("reason")}
    error = next((r for r in results if r.get("status") == "error"), None)
    if error:
        return {"state": "error", "reason": error.get("reason") or "MalwareBazaar could not be asked"}
    return {"state": "checking", "reason": None}


async def needs_review(rc, entry: dict[str, Any], package: str) -> bool:
    """A found listing whose review is missing or was made of another version."""
    if entry.get("status") != "found":
        return False
    # Reviewed with the first page at once, and redone when all the extra
    # pages are in (refresh_more_reviews moves the entry's hash then).
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
    listing = {**entry["listing"], "more_reviews": entry.get("more_reviews") or {}}
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
        listing = {**{k: v for k, v in listing.items() if k != "more_reviews"},
                   "listing": {**play_listing.summary(listing["listing"]), "reviews_read": reviews_read(listing)}}
    return {"listing": listing, "review": review, "common_sense": await common_sense_entry(rc, package)}


async def catalog(rc, packages: list[str]) -> dict[str, dict[str, Any]]:
    return {p: await catalog_entry(rc, p) for p in dict.fromkeys(packages) if app_vetting.is_package(p)}


async def checks_state(rc) -> dict[str, Any]:
    """How the extra checks stand, for the card's header: Common Sense on or
    off and paused, MalwareBazaar set up or not and paused."""
    csm = await _get_json(rc, CSM_STATE_KEY) or {}
    csm_backoff = await backoff_state(rc, CSM_BACKOFF_KEY)
    mb = await _get_json(rc, MB_STATE_KEY) or {}
    mb_backoff = await backoff_state(rc, MB_BACKOFF_KEY)
    mb_state = mb.get("state") or "checking"
    return {
        "common_sense": {
            "enabled": csm.get("enabled", True),
            "paused_until": (csm_backoff or {}).get("until_iso"),
            "paused_reason": (csm_backoff or {}).get("reason"),
        },
        "malware": {
            "state": mb_state,
            "reason": malwarebazaar.NOT_SET_UP if mb_state == "not_set_up" else mb.get("reason"),
            "paused_until": (mb_backoff or {}).get("until_iso"),
            "paused_reason": (mb_backoff or {}).get("reason"),
        },
    }


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
    state = await _get_json(rc, MB_STATE_KEY) or {}
    for app in apps:
        seen = last_seen.get(app["package"])
        if seen and (app.get("last_used") is None or seen > app["last_used"]):
            app["last_used"] = seen
        app["malware_check"] = await malware_check(rc, app, state)
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
        verdicts.append(app_vetting.assess(app, entry, policy, JARVIS_ANDROID_PACKAGE))
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
        verdict = app_vetting.assess({"package": package, "label": row.get("label") or ""}, entry, _policy(child),
                                     JARVIS_ANDROID_PACKAGE)
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


async def _play_step(rc, session, deliver: Deliver) -> bool:
    """One Google Play request when the interval and backoff allow: a listing
    first, else an extra page of reviews. Returns whether a request went."""
    if not APP_CATALOG_ENABLED or await backoff_state(rc):
        return False
    if not await _interval_ok(rc, LAST_FETCH_KEY, APP_CATALOG_MIN_INTERVAL_S):
        return False
    now = _now()
    due = await rc.zrangebyscore(LOOKUP_DUE, 0, now, start=0, num=1)
    if due:
        package = due[0]
        await rc.zrem(LOOKUP_DUE, package)
        entry = await refresh_listing(rc, package)
        if entry is not None:
            if await needs_review(rc, entry, package):
                await refresh_review(rc, session, package)
            await after_update(rc, session, package, deliver)
        return True
    more = await rc.zrangebyscore(MORE_REVIEWS_DUE, 0, now, start=0, num=1)
    if more:
        package = more[0]
        await rc.zrem(MORE_REVIEWS_DUE, package)
        entry = await refresh_more_reviews(rc, package)
        if entry is not None and await needs_review(rc, entry, package):
            await refresh_review(rc, session, package)
            await after_update(rc, session, package, deliver)
        return True
    return False


async def _common_sense_step(rc, session, deliver: Deliver, settings: dict[str, Any] | None) -> bool:
    """One Common Sense Media request when switched on and allowed."""
    if settings is None:
        return False
    enabled = common_sense_enabled(settings)
    await rc.set(CSM_STATE_KEY, json.dumps({"enabled": enabled}))
    if not enabled or await backoff_state(rc, CSM_BACKOFF_KEY):
        return False
    if not await _interval_ok(rc, CSM_LAST_FETCH_KEY, COMMON_SENSE_MIN_INTERVAL_S):
        return False
    due = await rc.zrangebyscore(CSM_DUE, 0, _now(), start=0, num=1)
    if not due:
        return False
    package = due[0]
    await rc.zrem(CSM_DUE, package)
    entry = await refresh_common_sense(rc, package)
    if entry is not None:
        await after_update(rc, session, package, deliver)
    return True


async def _malware_step(rc, session, deliver: Deliver, settings: dict[str, Any] | None) -> bool:
    """One MalwareBazaar request when a key is set and allowed. Without a key
    the state says "not set up" and nothing is asked."""
    if settings is None:
        return False
    key = str(settings.get("malwarebazaar_key") or "").strip()
    state = await _get_json(rc, MB_STATE_KEY) or {}
    if not key:
        if state.get("state") != "not_set_up":
            await rc.set(MB_STATE_KEY, json.dumps({"state": "not_set_up", "reason": malwarebazaar.NOT_SET_UP,
                                                   "at": _iso(_now())}))
        return False
    if state.get("state") == "not_set_up" or (state.get("key_fp") and state.get("key_fp") != _key_fingerprint(key)):
        # A key was added or changed: a refusal of the old one no longer holds.
        await rc.set(MB_STATE_KEY, json.dumps({"state": "ready", "key_fp": _key_fingerprint(key), "at": _iso(_now())}))
        await _clear_backoff(rc, MB_BACKOFF_KEY)
    if await backoff_state(rc, MB_BACKOFF_KEY):
        return False
    if not await _interval_ok(rc, MB_LAST_FETCH_KEY, MALWAREBAZAAR_MIN_INTERVAL_S):
        return False
    due = await rc.zrangebyscore(MB_DUE, 0, _now(), start=0, num=1)
    if not due:
        return False
    sha = due[0]
    await refresh_malware(rc, key)
    result = await _get_json(rc, f"{MB_PREFIX}{sha}") or {}
    if result.get("status") == "malware":
        await _after_hash(rc, session, sha, deliver)
    return True


async def _after_hash(rc, session, sha: str, deliver: Deliver) -> None:
    """A hash turned out to be malware: tell the parents of each child holding it."""
    for child in (await guardian.children(session)).values():
        packages = {a["package"] for r in await inventories(rc, child["username"]) for a in r.get("apps") or []
                    if a.get("apk_sha256") == sha}
        if packages:
            await notify_child(rc, child, deliver, sorted(packages))


async def step(rc, session, deliver: Deliver) -> str:
    """One round of work: a review that is due, else at most one request to
    each source whose interval and backoff allow (Google Play, Common Sense
    Media, MalwareBazaar). Returns "fetched" (a request went out),
    "reviewed" or "idle"."""
    now = _now()
    reviews = await rc.zrangebyscore(REVIEW_DUE, 0, now, start=0, num=1)
    if reviews:
        package = reviews[0]
        await rc.zrem(REVIEW_DUE, package)
        if await refresh_review(rc, session, package) is not None:
            await after_update(rc, session, package, deliver)
        return "reviewed"
    fetched = await _play_step(rc, session, deliver)
    try:
        settings: dict[str, Any] | None = await check_settings(session)
    except Exception as exc:
        log.warning("app check settings could not be read: %s", type(exc).__name__)
        settings = None
    fetched = await _common_sense_step(rc, session, deliver, settings) or fetched
    fetched = await _malware_step(rc, session, deliver, settings) or fetched
    return "fetched" if fetched else "idle"


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

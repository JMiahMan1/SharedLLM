"""App vetting: what a parent should know about the apps on a child's device.

Pure rules shared by identity (the age policy a parent edits), telemetry (which
apps to notify about) and the gateway (the Apps card and request cards):

- **Age policy.** A child's age (from the birth date a parent set) gives a
  default maximum content rating (``default_max_rating``); a parent may set a
  different maximum per child and mark single apps "always allow" or "always
  flag".
- **Assessment.** One app, its public Play listing (``play_listing``), the
  local model's review of it (``app_review``) and the policy give a verdict:
  why it is flagged (over the age limit, a parent's flag, each concern the
  review found, or for apps not on Google Play the permissions they hold),
  each with a level on content monitoring's scale (``concern_monitor.LEVELS``)
  so a parent's notify level applies exactly as it does for concerns.
- **Not used lately.** Apps with no recorded use for a parent-chosen number of
  days, as removal suggestions.

Nothing here guesses: an unknown rating or an unknown birth date is reported
as unknown, never as a default.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, date, datetime
from typing import Any

from services.common import concern_monitor
from services.common.screen_time import PERMISSION_GROUPS

#: Google Play's content ratings in the United States (IARC / ESRB labels),
#: least to most restricted. The server looks listings up with ``gl=US`` so
#: these are the labels it sees.
RATINGS = ("Everyone", "Everyone 10+", "Teen", "Mature 17+", "Adults only 18+")

#: The rating a child of each age may use by default (inclusive upper ages).
_AGE_BANDS = ((9, "Everyone"), (12, "Everyone 10+"), (16, "Teen"), (17, "Mature 17+"))

#: A parent's per-app choice: never flag it, or always flag it.
APP_OVERRIDES = ("allow", "flag")

#: Days without use before an app is suggested for removal.
DEFAULT_UNUSED_DAYS = 30
UNUSED_DAYS_RANGE = (7, 365)

#: What the local model may raise about an app, with a parent's label. A
#: self-harm concern is content monitoring's ALWAYS_ON category, so it notifies
#: whatever level a parent chose.
CONCERNS: dict[str, str] = {
    "contact_strangers": "Contact with strangers or chat",
    "sexual": "Sexual content",
    "violence": "Violence",
    "gambling": "Gambling or loot boxes",
    "scams": "Scams or predatory purchases",
    "ads": "Heavy ads",
    "data": "Data collection or location sharing",
    "horror": "Horror or frightening content",
    "drugs": "Drugs or alcohol",
    "self_harm": "Self-harm",
    "quality": "Bugs or poor quality",
}

#: Installers that are app stores (anything else, or none, is a sideload).
PLAY_INSTALLER = "com.android.vending"
STORE_INSTALLERS = {
    PLAY_INSTALLER: "Google Play",
    "com.sec.android.app.samsungapps": "Galaxy Store",
    "com.amazon.venezia": "Amazon Appstore",
    "org.fdroid.fdroid": "F-Droid",
    "com.huawei.appmarket": "AppGallery",
}

#: Permissions that matter for an app nobody can look up: each group's level
#: and plain words. Contacts, texts and calls are about other people too.
PERMISSION_LEVELS: dict[str, tuple[str, str]] = {
    "location": ("high", "location"),
    "microphone": ("high", "the microphone"),
    "camera": ("medium", "the camera"),
    "contacts": ("high", "contacts"),
    "sms": ("high", "text messages"),
    "phone": ("high", "phone calls"),
    "photos": ("medium", "photos and videos"),
    "calendar": ("medium", "the calendar"),
    "nearby": ("medium", "nearby devices"),
}
assert set(PERMISSION_LEVELS) == set(PERMISSION_GROUPS)

LEVELS = concern_monitor.LEVELS
_PACKAGE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+$")
_ISO_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def is_package(value: Any) -> bool:
    return isinstance(value, str) and len(value) <= 255 and bool(_PACKAGE.match(value))


# --- age policy -------------------------------------------------------------


def parse_birth_date(value: Any, today: date) -> date:
    """A birth date a parent typed, as a date; ValueError with the reason."""
    if not isinstance(value, str) or not _ISO_DAY.match(value.strip()):
        raise ValueError("birth_date must be YYYY-MM-DD")
    born = date.fromisoformat(value.strip())
    if born > today:
        raise ValueError("birth_date cannot be in the future")
    if born.year < today.year - 120:
        raise ValueError("birth_date is too long ago")
    return born


def age_on(born: date | None, today: date) -> int | None:
    if born is None:
        return None
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))


def default_max_rating(age: int | None) -> str | None:
    """The highest rating a child of ``age`` may use by default; None when the
    age is unknown (a parent has not set a birth date)."""
    if age is None:
        return None
    for upto, rating in _AGE_BANDS:
        if age <= upto:
            return rating
    return RATINGS[-1]


def rating_rank(rating: str | None) -> int | None:
    return RATINGS.index(rating) if rating in RATINGS else None


def clean_policy(raw: Any) -> dict[str, Any]:
    """The stored ``app_policy`` of a child's rules, with anything malformed
    left out (so an old or hand-edited row never breaks a read)."""
    raw = raw if isinstance(raw, dict) else {}
    out: dict[str, Any] = {}
    if raw.get("max_rating") in RATINGS:
        out["max_rating"] = raw["max_rating"]
    apps = raw.get("apps") if isinstance(raw.get("apps"), dict) else {}
    out["apps"] = {p: m for p, m in apps.items() if is_package(p) and m in APP_OVERRIDES}
    days = raw.get("unused_days")
    low, high = UNUSED_DAYS_RANGE
    out["unused_days"] = days if isinstance(days, int) and not isinstance(days, bool) and low <= days <= high \
        else DEFAULT_UNUSED_DAYS
    for key in ("dismissed", "kept"):
        given = raw.get(key) if isinstance(raw.get(key), dict) else {}
        out[key] = {p: v for p, v in given.items() if is_package(p) and isinstance(v, str)}
    return out


def effective_policy(policy: Any, birth_date: str | None, today: date) -> dict[str, Any]:
    """The policy with the child's age and the rating limit worked out."""
    clean = clean_policy(policy)
    born = None
    if birth_date:
        try:
            born = parse_birth_date(birth_date, today)
        except ValueError:
            born = None
    age = age_on(born, today)
    default = default_max_rating(age)
    return {
        **clean,
        "birth_date": born.isoformat() if born else None,
        "age": age,
        "default_max_rating": default,
        "max_rating": clean.get("max_rating"),
        "effective_max_rating": clean.get("max_rating") or default,
    }


# --- inventory --------------------------------------------------------------


def clean_inventory_app(raw: Any) -> dict[str, Any] | None:
    """One app row a device reported, bounded; None when it is not an app."""
    if not isinstance(raw, dict) or not is_package(raw.get("package")):
        return None

    def ms(key: str) -> int | None:
        value = raw.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            return None
        return int(value)

    perms = raw.get("permissions") if isinstance(raw.get("permissions"), list) else []
    size = raw.get("size_bytes")
    installer = raw.get("installer")
    return {
        "package": raw["package"],
        "label": str(raw.get("label") or "")[:120],
        "installer": installer if is_package(installer) else None,
        "first_install": ms("first_install"),
        "last_update": ms("last_update"),
        "last_used": ms("last_used"),
        "launchable": bool(raw.get("launchable", True)),
        "permissions": sorted({p for p in perms if p in PERMISSION_GROUPS}),
        "size_bytes": int(size) if isinstance(size, (int, float)) and not isinstance(size, bool) and size > 0 else None,
    }


def merge_inventories(devices: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every device's apps as one list per package: a phone and a tablet with
    the same game is one row naming both, last used on either."""
    merged: dict[str, dict[str, Any]] = {}
    for device in devices:
        key = str(device.get("device") or "")
        for app in device.get("apps") or []:
            row = merged.get(app["package"])
            if row is None:
                merged[app["package"]] = {**app, "devices": [key], "usage_access": bool(device.get("usage_access"))}
                continue
            row["devices"].append(key)
            row["label"] = row["label"] or app.get("label") or ""
            row["installer"] = row.get("installer") or app.get("installer")
            row["launchable"] = row["launchable"] or app.get("launchable", False)
            row["permissions"] = sorted(set(row["permissions"]) | set(app.get("permissions") or []))
            row["usage_access"] = row["usage_access"] or bool(device.get("usage_access"))
            for key_name, pick in (("first_install", min), ("last_update", max), ("last_used", max), ("size_bytes", max)):
                values = [v for v in (row.get(key_name), app.get(key_name)) if v is not None]
                row[key_name] = pick(values) if values else None
    return sorted(merged.values(), key=lambda a: (a["label"] or a["package"]).lower())


# --- assessment -------------------------------------------------------------


def max_level(levels: list[str]) -> str | None:
    known = [lv for lv in levels if lv in LEVELS]
    return max(known, key=LEVELS.index) if known else None


def passes(level: str | None, category: str, notify_level: str | None) -> bool:
    """Whether a flag at ``level`` reaches a parent who chose ``notify_level``
    (content monitoring's rule: self-harm always does)."""
    if level is None:
        return False
    return concern_monitor.pages_parent({"category": category, "level": level}, notify_level)


def installer_name(installer: str | None) -> str | None:
    return STORE_INSTALLERS.get(installer or "")


def assess(
    app: dict[str, Any],
    catalog: dict[str, Any] | None,
    policy: dict[str, Any],
) -> dict[str, Any]:
    """One app's verdict for one child.

    ``app`` is an inventory row (or ``{"package", "label"}`` for an app asked
    for but not installed); ``catalog`` is telemetry's entry for the package
    (``{"listing": {...}, "review": {...}}``) or None when it was never looked
    up; ``policy`` is :func:`effective_policy`. The verdict carries every flag
    with its level; who it reaches is decided per parent (:func:`flagged_for`).
    """
    package = app["package"]
    listing_entry = (catalog or {}).get("listing") or {}
    review_entry = (catalog or {}).get("review") or {}
    lookup = listing_entry.get("status") or "checking"
    listing = listing_entry.get("listing") if lookup == "found" else None
    review = review_entry if review_entry.get("status") == "ready" and (
        listing is None or review_entry.get("listing_hash") == listing_entry.get("hash")) else None

    rating = (listing or {}).get("content_rating")
    limit = policy.get("effective_max_rating")
    rank, limit_rank = rating_rank(rating), rating_rank(limit)
    over_age = None if rank is None or limit_rank is None else rank > limit_rank
    override = (policy.get("apps") or {}).get(package)

    flags: list[dict[str, Any]] = []
    if over_age:
        flags.append({
            "reason": "over_age", "category": "rating", "level": "high",
            "text": f"Rated {rating}, above the {limit} limit",
        })
    if override == "flag":
        flags.append({"reason": "parent_flag", "category": "parent", "level": "high", "text": "A parent marked it to watch"})
    concerns = []
    for concern in (review or {}).get("concerns") or []:
        label = CONCERNS.get(concern.get("category") or "")
        if not label or concern.get("severity") not in LEVELS:
            continue
        concerns.append({**concern, "label": label})
        flags.append({
            "reason": "concern", "category": concern["category"], "level": concern["severity"],
            "text": concern.get("why") or label,
        })
    if lookup == "not_on_play":
        held = [g for g in app.get("permissions") or [] if g in PERMISSION_LEVELS]
        if held:
            level = max_level([PERMISSION_LEVELS[g][0] for g in held])
            words = ", ".join(PERMISSION_LEVELS[g][1] for g in held)
            flags.append({
                "reason": "permissions", "category": "data", "level": level,
                "text": f"Not on Google Play, and it can use {words}",
            })
        # Only an installed app says where it came from ("installer" absent:
        # an app asked for, not on the device yet).
        if "installer" in app and installer_name(app.get("installer")) is None:
            flags.append({
                "reason": "sideloaded", "category": "data", "level": "medium",
                "text": "Installed from outside an app store",
            })

    level = None if override == "allow" else max_level([f["level"] for f in flags])
    fingerprint = hashlib.sha256(
        "|".join(sorted(f"{f['reason']}:{f['category']}:{f['level']}" for f in flags)).encode()
    ).hexdigest()[:16] if flags else ""
    dismissed = bool(fingerprint) and (policy.get("dismissed") or {}).get(package) == fingerprint

    if lookup == "found" and review is None:
        review_state = review_entry.get("status") or "checking"
    else:
        review_state = "ready" if review else ("not_needed" if lookup != "found" else "checking")

    return {
        "package": package,
        "label": app.get("label") or (listing or {}).get("title") or package,
        "lookup": lookup,
        "lookup_reason": listing_entry.get("reason"),
        "review_state": review_state,
        "review_reason": review_entry.get("reason") if review is None else None,
        "title": (listing or {}).get("title"),
        "developer": (listing or {}).get("developer"),
        "rating": rating,
        "rating_descriptors": (listing or {}).get("content_descriptors") or [],
        "interactive": (listing or {}).get("interactive_elements") or [],
        "limit": limit,
        "over_age": over_age,
        "ads": (listing or {}).get("contains_ads"),
        "in_app_purchases": (listing or {}).get("in_app_purchases"),
        "data_safety": (listing or {}).get("data_safety") or {},
        "stars": (listing or {}).get("score"),
        "installs": (listing or {}).get("installs"),
        "summary": (review or {}).get("summary"),
        "concerns": concerns,
        "flags": flags,
        "level": level,
        "override": override,
        "fingerprint": fingerprint,
        "dismissed": dismissed,
        "store": installer_name(app.get("installer")),
    }


def fact_words(verdict: dict[str, Any]) -> str:
    """The listing's plain facts a parent weighs: ads, purchases, chat."""
    facts = []
    if verdict.get("ads"):
        facts.append("ads")
    if verdict.get("in_app_purchases"):
        facts.append("in-app purchases")
    interactive = [w for w in verdict.get("interactive") or [] if "purchase" not in w.lower()]
    words = ("Has " + " and ".join(facts)) if facts else ""
    if interactive:
        words = (words + "; " if words else "") + ", ".join(interactive)
    return words


def verdict_line(verdict: dict[str, Any]) -> str:
    """One or two sentences for a notice about a requested app."""
    if verdict.get("lookup") in (None, "pending", "checking"):
        return "Jarvis is checking its Play listing and reviews."
    parts = []
    if verdict.get("lookup") == "not_on_play":
        parts.append("Not on Google Play, so judged on its permissions")
    elif verdict.get("lookup") in ("error", "off"):
        parts.append("Its Play listing could not be checked")
    if verdict.get("rating"):
        limit = verdict.get("limit")
        if verdict.get("over_age"):
            parts.append(f"Rated {verdict['rating']}, above the {limit} limit")
        elif limit:
            parts.append(f"Rated {verdict['rating']}, within the {limit} limit")
        else:
            parts.append(f"Rated {verdict['rating']} (set a birth date to compare)")
    concerns = [c["label"] for c in verdict.get("concerns") or []][:3]
    if concerns:
        parts.append("Watch for: " + ", ".join(concerns))
    elif verdict.get("review_state") == "ready":
        parts.append("The review found nothing to worry about")
    elif verdict.get("review_state") in ("failed", "no_model"):
        parts.append("The local review could not be made")
    facts = fact_words(verdict)
    if facts:
        parts.append(facts)
    return ". ".join(p.rstrip(".") for p in parts) + "."


def flagged_for(verdict: dict[str, Any], notify_level: str | None) -> list[dict[str, Any]]:
    """The flags one parent should see first: the age limit and their own
    flags always, a concern when it reaches their notify level. None when a
    parent allowed the app."""
    if verdict.get("override") == "allow":
        return []
    return [
        f for f in verdict.get("flags") or []
        if f["reason"] in ("over_age", "parent_flag") or passes(f["level"], str(f.get("category") or ""), notify_level)
    ]


def notifies(verdict: dict[str, Any], notify_level: str | None) -> list[dict[str, Any]]:
    """The flags that notify one parent now: those that reach their level (a
    parent at "off" hears only about self-harm), unless they kept the app."""
    if verdict.get("override") == "allow" or verdict.get("dismissed"):
        return []
    return [f for f in verdict.get("flags") or [] if passes(f["level"], str(f.get("category") or ""), notify_level)]


def sort_for_parent(verdicts: list[dict[str, Any]], notify_level: str | None) -> list[dict[str, Any]]:
    """Questionable apps first (most serious first), then everything else by name."""
    def key(v: dict[str, Any]):
        mine = [] if v.get("dismissed") else flagged_for(v, notify_level)
        worst = max_level([f["level"] for f in mine])
        return (0 if mine else 1, -(LEVELS.index(worst) if worst else -1), (v.get("label") or "").lower())
    return sorted(verdicts, key=key)


# --- not used lately --------------------------------------------------------


def unused_apps(apps: list[dict[str, Any]], policy: dict[str, Any], now_ms: int,
                own_package: str | None = None) -> list[dict[str, Any]]:
    """Apps not opened for ``policy["unused_days"]`` days, longest unused first.

    An app counts only when its last use is known, or when a device holding it
    reports usage access and has no use on record since an install older than
    the window (Android keeps about two years of history). Without usage
    access nothing is suggested: no use on record would mean nothing then. A
    parent's "keep" hides a suggestion until the app is used again.
    """
    days = policy.get("unused_days") or DEFAULT_UNUSED_DAYS
    window = days * 86_400_000
    kept = policy.get("kept") or {}
    out = []
    for app in apps:
        if app["package"] == own_package or not app.get("launchable", True):
            continue
        last = app.get("last_used")
        if last is not None:
            if now_ms - last < window:
                continue
        elif not (app.get("usage_access") and app.get("first_install") and now_ms - app["first_install"] >= window):
            continue
        kept_at = kept.get(app["package"])
        if kept_at and (last is None or _iso_ms(kept_at) >= last):
            continue
        out.append({
            "package": app["package"],
            "label": app.get("label") or app["package"],
            "last_used": last,
            "first_install": app.get("first_install"),
            "size_bytes": app.get("size_bytes"),
            "days_unused": (now_ms - (last or app["first_install"])) // 86_400_000,
            "never_used": last is None,
            "devices": app.get("devices") or [],
        })
    return sorted(out, key=lambda a: -a["days_unused"])


def _iso_ms(value: str) -> int:
    try:
        when = datetime.fromisoformat(value)
    except ValueError:
        return 0
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return int(when.timestamp() * 1000)

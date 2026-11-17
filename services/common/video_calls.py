"""Video calls on a child's phone: which apps and sites count, and the parents' choices.

One place for both features that use the list:

- video call checks: while a child is on a video call in one of these apps,
  or on one of these sites in a browser, the phone's guard sends a downscaled
  screenshot every ``interval_s`` seconds (at most ``max_frames`` per call)
  through the same safety scanner photos use;
- the Video calls block: one switch that blocks every app and site in the
  list (a parent's action, never automatic).

The default lists live in ``data/video_calls.json`` (configuration, not code),
read once when the service starts. A parent can replace either list for one
child; a list left out of the rules means the defaults.

The rules shape (``rules["video_calls"]``)::

    {"scan": bool, "interval_s": int, "max_frames": int,
     "corroborate_frames": int, "block": bool,
     "apps": [package, ...], "sites": [domain, ...]}

``scan`` left out follows ``photo_scan`` (on unless a parent switched photo
scanning off). Pure: no I/O after import.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

DEFAULTS_FILE = Path(__file__).resolve().parent / "data" / "video_calls.json"

INTERVAL_DEFAULT_S = 45
INTERVAL_MIN_S = 15
INTERVAL_MAX_S = 600
MAX_FRAMES_DEFAULT = 20
MAX_FRAMES_MIN = 1
MAX_FRAMES_MAX = 120
#: Flagged frames of one category, each confirmed by the second check, needed
#: in one call before a parent is notified. Never below two: a single vision
#: verdict must not page anyone.
CORROBORATE_DEFAULT = 2
CORROBORATE_MIN = 2
CORROBORATE_MAX = 10
LIST_MAX = 200

PACKAGE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+$")
DOMAIN = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


def normalize_site(value: str) -> str:
    """A configured site as the filter compares it: lower case, no scheme,
    path, port, trailing dot or leading ``*.`` (a site covers its subdomains)."""
    s = str(value or "").strip().lower()
    s = re.sub(r"^[a-z][a-z0-9+.-]*://", "", s)
    s = s.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    s = s.rsplit("@", 1)[-1].split(":", 1)[0]
    while s.startswith("*."):
        s = s[2:]
    return s.rstrip(".")


def _load_defaults(path: Path = DEFAULTS_FILE) -> dict[str, Any]:
    """The shipped lists. A missing or broken file is a packaging error, so it
    raises at import rather than leaving the block switch silently empty."""
    data = json.loads(path.read_text(encoding="utf-8"))
    apps = [a for a in data.get("apps") or [] if isinstance(a, dict) and PACKAGE.match(str(a.get("package") or ""))]
    sites = [normalize_site(s) for s in data.get("sites") or []]
    browsers = {
        str(pkg): [str(v) for v in ids if isinstance(v, str)]
        for pkg, ids in (data.get("browsers") or {}).items()
        if PACKAGE.match(str(pkg)) and isinstance(ids, list)
    }
    if not apps or not sites or not browsers:
        raise ValueError(f"{path} must list apps, sites and browsers")
    bad = [s for s in sites if not DOMAIN.match(s)]
    if bad:
        raise ValueError(f"{path} has sites that are not domain names: {', '.join(bad)}")
    return {
        "apps": [a["package"] for a in apps],
        "labels": {a["package"]: str(a.get("label") or a["package"]) for a in apps},
        "sites": list(dict.fromkeys(sites)),
        "browsers": browsers,
    }


DEFAULTS = _load_defaults()


def _int_in(raw: dict, key: str, low: int, high: int) -> int | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
        raise ValueError(f"video_calls.{key} must be a whole number from {low} to {high}")
    return value


def clean(raw: Any) -> dict[str, Any]:
    """A parent's ``video_calls`` rules, checked. Raises ValueError with the
    message to show; keys left out stay out (so they mean the default)."""
    if not isinstance(raw, dict):
        raise ValueError("video_calls must be an object")
    out: dict[str, Any] = {}
    for key in ("scan", "block"):
        if raw.get(key) is not None:
            if not isinstance(raw[key], bool):
                raise ValueError(f"video_calls.{key} must be true or false")
            out[key] = raw[key]
    for key, low, high in (
        ("interval_s", INTERVAL_MIN_S, INTERVAL_MAX_S),
        ("max_frames", MAX_FRAMES_MIN, MAX_FRAMES_MAX),
        ("corroborate_frames", CORROBORATE_MIN, CORROBORATE_MAX),
    ):
        value = _int_in(raw, key, low, high)
        if value is not None:
            out[key] = value
    if raw.get("apps") is not None:
        apps = raw["apps"]
        if not isinstance(apps, list) or any(not isinstance(p, str) or not PACKAGE.match(p.strip()) for p in apps):
            raise ValueError("video_calls.apps must be app package names (like com.example.app)")
        out["apps"] = list(dict.fromkeys(p.strip() for p in apps))[:LIST_MAX]
    if raw.get("sites") is not None:
        sites = raw["sites"]
        if not isinstance(sites, list) or any(not isinstance(s, str) for s in sites):
            raise ValueError("video_calls.sites must be domain names")
        cleaned = [normalize_site(s) for s in sites]
        bad = [s for s in cleaned if not DOMAIN.match(s)]
        if bad:
            raise ValueError(f"video_calls.sites must be domain names (not {bad[0] or 'an empty one'})")
        out["sites"] = list(dict.fromkeys(cleaned))[:LIST_MAX]
    return out


def resolve(rules: dict[str, Any] | None) -> dict[str, Any]:
    """Everything the phone and the scanner need, defaults filled in."""
    r = rules or {}
    raw = r.get("video_calls") if isinstance(r.get("video_calls"), dict) else {}
    scan = raw.get("scan")
    if not isinstance(scan, bool):
        scan = r.get("photo_scan") is not False
    if r.get("monitoring") == "off":
        scan = False

    def bounded(key: str, default: int, low: int, high: int) -> int:
        value = raw.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and low <= value <= high:
            return value
        return default

    custom_apps = isinstance(raw.get("apps"), list)
    custom_sites = isinstance(raw.get("sites"), list)
    return {
        "scan": scan,
        "interval_s": bounded("interval_s", INTERVAL_DEFAULT_S, INTERVAL_MIN_S, INTERVAL_MAX_S),
        "max_frames": bounded("max_frames", MAX_FRAMES_DEFAULT, MAX_FRAMES_MIN, MAX_FRAMES_MAX),
        "corroborate_frames": bounded("corroborate_frames", CORROBORATE_DEFAULT, CORROBORATE_MIN, CORROBORATE_MAX),
        "block": raw.get("block") is True,
        "apps": list(raw["apps"]) if custom_apps else list(DEFAULTS["apps"]),
        "sites": [normalize_site(s) for s in raw["sites"]] if custom_sites else list(DEFAULTS["sites"]),
        "custom_apps": custom_apps,
        "custom_sites": custom_sites,
        "labels": dict(DEFAULTS["labels"]),
        "browsers": {k: list(v) for k, v in DEFAULTS["browsers"].items()},
    }


def blocked_apps(rules: dict[str, Any] | None) -> list[str]:
    """The apps the Video calls switch blocks now (none while it is off)."""
    v = resolve(rules)
    return list(v["apps"]) if v["block"] else []


def blocked_sites(rules: dict[str, Any] | None) -> list[str]:
    """The sites the Video calls switch blocks now (none while it is off)."""
    v = resolve(rules)
    return list(v["sites"]) if v["block"] else []


def call_label(metadata: dict[str, Any] | None) -> str:
    """'FaceTime (facetime.apple.com)'-style words for where a call happened:
    the app's name, the site, or both when a browser showed the site."""
    m = metadata if isinstance(metadata, dict) else {}
    app = str(m.get("app") or "").strip()[:60]
    site = normalize_site(str(m.get("site") or ""))[:80]
    package = str(m.get("package") or "").strip()
    if not app and package:
        app = DEFAULTS["labels"].get(package, package)
    if app and site:
        return f"{app} ({site})"
    return app or site or "a video call"

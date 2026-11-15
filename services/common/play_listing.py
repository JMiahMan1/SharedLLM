"""Reading an app's public Google Play listing (read-only, no account).

The server fetches ``https://play.google.com/store/apps/details?id=<package>``
(only the package name is in the request; see ``listing_url`` and
``REQUEST_HEADERS``) and this module reads the page. Play renders its data
into ``AF_initDataCallback({key: 'ds:N', ..., data: [...]})`` script blocks;
the app's own block is the one whose ``[1][2][77][0]`` is the package, and the
first page of reviews is the block made of review rows. The positions below
are the ones the page uses today (checked against saved public listings in
``services/common/tests/fixtures/play``); when Google moves them the parser
raises ``ListingError`` instead of returning half a listing.

No third-party scraper library: the ones that exist fetch pages themselves
with their own headers and cookies, which would take what is sent to Google
out of this code's hands. Reviewers' names and pictures are never kept.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

#: The Play page: US English, so ratings use the IARC / ESRB US labels that
#: ``app_vetting.RATINGS`` knows.
LISTING_BASE = "https://play.google.com/store/apps/details"
LISTING_LOCALE = {"hl": "en_US", "gl": "US"}
#: A generic browser user agent and nothing else that could identify the
#: household (no cookies are sent or kept; see the telemetry fetcher).
REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

MAX_DESCRIPTION = 3000
MAX_REVIEW_TEXT = 600
MAX_REVIEWS = 40

_BLOCK = re.compile(
    r"AF_initDataCallback\(\{key: '(ds:\d+)', hash: '\d+', data:(.*?), sideChannel: \{\}\}\);</script>",
    re.DOTALL,
)
_TAG = re.compile(r"<[^>]+>")


class ListingError(ValueError):
    """The page is not a listing this parser can read."""


def listing_url(package: str) -> str:
    return f"{LISTING_BASE}?{urlencode({'id': package, **LISTING_LOCALE})}"


def _at(node: Any, *path: int) -> Any:
    for step in path:
        if not isinstance(node, list) or step >= len(node):
            return None
        node = node[step]
    return node


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = html.unescape(_TAG.sub(" ", value.replace("<br>", "\n")))
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\n\s*\n+", "\n", cleaned).strip()
    return cleaned or None


def _list_words(value: Any) -> list[str]:
    """"Users Interact, In-Game Purchases (Includes Random Items)" as a list."""
    text = _text(value)
    if not text:
        return []
    return [part.strip() for part in re.split(r",\s*(?![^()]*\))", text) if part.strip()]


def _blocks(page: str) -> list[Any]:
    out = []
    for _key, data in _BLOCK.findall(page):
        try:
            out.append(json.loads(data))
        except json.JSONDecodeError:
            continue
    return out


def _app_block(blocks: list[Any], package: str) -> list[Any]:
    for block in blocks:
        app = _at(block, 1, 2)
        if isinstance(app, list) and _at(app, 77, 0) == package:
            return app
    raise ListingError("the page has no app data for this package")


def _is_review(row: Any) -> bool:
    return (
        isinstance(row, list) and len(row) > 6 and isinstance(row[0], str)
        and isinstance(row[2], int) and 1 <= row[2] <= 5 and isinstance(row[4], str)
    )


def _reviews(blocks: list[Any]) -> list[dict[str, Any]]:
    for block in blocks:
        rows = _at(block, 0)
        if isinstance(rows, list) and rows and all(_is_review(r) for r in rows):
            out = []
            for row in rows[:MAX_REVIEWS]:
                seconds = _at(row, 5, 0)
                version = row[10] if len(row) > 10 and isinstance(row[10], str) else None
                out.append({
                    # Never the reviewer's name or picture: the words, the stars and when.
                    "stars": row[2],
                    "text": row[4][:MAX_REVIEW_TEXT],
                    "at": datetime.fromtimestamp(seconds, UTC).date().isoformat() if isinstance(seconds, int) else None,
                    "thumbs_up": row[6] if isinstance(row[6], int) else 0,
                    "version": version,
                })
            return out
    return []


def _data_safety(app: list[Any]) -> dict[str, Any]:
    rows = _at(app, 136, 1)
    if not isinstance(rows, list):
        return {}
    lines = []
    for row in rows:
        heading = _text(_at(row, 1))
        if heading:
            lines.append({"heading": heading, "detail": _text(_at(row, 2, 1))})
    headings = [line["heading"].lower() for line in lines]

    def find(prefix: str) -> dict[str, Any] | None:
        return next((line for line in lines if line["heading"].lower().startswith(prefix)), None)

    shares = find("this app may share")
    collects = find("this app may collect")
    return {
        "lines": lines,
        "shares_data": True if shares else (False if any(h.startswith("no data shared") for h in headings) else None),
        "shared_types": (shares or {}).get("detail"),
        "collects_data": True if collects else (False if any(h.startswith("no data collected") for h in headings) else None),
        "collected_types": (collects or {}).get("detail"),
        "encrypted_in_transit": True if any("encrypted in transit" in h for h in headings) else None,
        "deletion_offered": True if any("request that data be deleted" in h for h in headings) else None,
    }


def parse(page: str, package: str) -> dict[str, Any]:
    """The listing facts and the first page of reviews from one Play page.

    Raises ListingError when the page is not this package's listing.
    """
    blocks = _blocks(page or "")
    if not blocks:
        raise ListingError("the page carries no listing data")
    app = _app_block(blocks, package)
    title = _text(_at(app, 0, 0))
    rating = _at(app, 9, 0)
    if not title:
        raise ListingError("the listing has no title")
    iap = _at(app, 19, 0)
    updated = _at(app, 145, 0, 1, 0)
    score = _at(app, 51, 0, 1)
    listing = {
        "package": package,
        "title": title,
        "developer": _text(_at(app, 68, 0)),
        "genre": _text(_at(app, 79, 0, 0, 0)),
        "content_rating": rating if isinstance(rating, str) and rating else None,
        "content_descriptors": _list_words(_at(app, 9, 2, 1)),
        "interactive_elements": _list_words(_at(app, 9, 3, 1)),
        "contains_ads": bool(_at(app, 48)),
        "in_app_purchases": isinstance(iap, str) and bool(iap),
        "in_app_price_range": iap if isinstance(iap, str) else None,
        "score": round(float(score), 2) if isinstance(score, (int, float)) else None,
        "ratings_count": _at(app, 51, 2, 1) if isinstance(_at(app, 51, 2, 1), int) else None,
        "installs": _at(app, 13, 0) if isinstance(_at(app, 13, 0), str) else None,
        "updated": datetime.fromtimestamp(updated, UTC).date().isoformat() if isinstance(updated, int) else None,
        "description": (_text(_at(app, 72, 0, 1)) or "")[:MAX_DESCRIPTION],
        "data_safety": _data_safety(app),
        "reviews": _reviews(blocks),
    }
    listing["hash"] = listing_hash(listing)
    return listing


def listing_hash(listing: dict[str, Any]) -> str:
    """What the local model's review depends on: a new rating, new descriptors,
    a changed data-safety section or a new page of reviews means a new review."""
    keyed = {k: listing.get(k) for k in (
        "content_rating", "content_descriptors", "interactive_elements", "contains_ads",
        "in_app_purchases", "description", "data_safety",
    )}
    keyed["reviews"] = [r.get("text") for r in listing.get("reviews") or []]
    return hashlib.sha256(json.dumps(keyed, sort_keys=True).encode()).hexdigest()[:24]


def summary(listing: dict[str, Any]) -> dict[str, Any]:
    """The listing without its description and reviews (what a card shows)."""
    return {k: v for k, v in listing.items() if k not in ("description", "reviews")}

"""Reading an app's Common Sense Media review page (read-only, no account).

Common Sense Media reviews many apps and games for parents. Its pages live at
``https://www.commonsensemedia.org/app-reviews/<slug>`` and
``.../game-reviews/<slug>``, where the slug is made from the app's name; the
site has no lookup by Android package, and its search results are drawn by
script. So telemetry tries a few slugs made from the app's Play title (only
the app's name goes out), at most ``MAX_CANDIDATES`` pages per app, and keeps
the first page whose reviewed item is that app.

What is read from a page (the positions the page uses today, checked against
saved public pages in ``services/common/tests/fixtures/common_sense``):

- the age rating ("age 13+"), from the page's schema.org ``Review``
  (``typicalAgeRange``), which must agree with the rating shown on the page;
- the "Parents need to know" paragraph;
- when shown, the parents' and kids' review consensus: their age, how many
  reviews it is based on, and the site's one-paragraph summary of them.

When the page does not have the review data or the "Parents need to know"
paragraph, ``parse`` raises ``CommonSenseError`` rather than returning half a
review. Reviewers' names (including the critic's) are never kept.
"""

from __future__ import annotations

import html
import json
import re
import unicodedata
from typing import Any
from urllib.parse import urljoin, urlparse

SITE = "https://www.commonsensemedia.org"
SECTIONS = ("app-reviews", "game-reviews")
MAX_CANDIDATES = 4
MAX_SUMMARY = 1200
MAX_CONSENSUS_SUMMARY = 500
#: The same generic browser headers as the Play lookups: nothing that could
#: identify the household, no cookies (the telemetry session keeps none).
REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}
_ITEM_TYPES = ("MobileApplication", "SoftwareApplication", "VideoGame", "WebApplication")
_LD = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.DOTALL)
_TAG = re.compile(r"<[^>]+>")
_AGE = re.compile(r"^\s*(\d{1,2})\s*\+\s*$")
_SHOWN_AGE = re.compile(r'class="rating__age">\s*age\s+(\d{1,2})\+', re.IGNORECASE)
_PNTK = re.compile(r"<p>\s*(Parents need to know\b.*?)</p>", re.DOTALL | re.IGNORECASE)
_STATS = re.compile(
    r'class="user-reviews-statistics">(.*?)(?=class="user-reviews-statistics"|class="tab__pane|\Z)', re.DOTALL)
_BASED_ON = re.compile(r"Based on\s+([\d,]+)\s+(parent|kid)\s+reviews?", re.IGNORECASE)
_SAYS = re.compile(r"<p>\s*((?:Parents|Kids) say\b.*?)</p>", re.DOTALL)


class CommonSenseError(ValueError):
    """The page is not a Common Sense Media app review this parser can read."""


def slug(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    folded = folded.lower().replace("&", " and ").replace("'", "")
    return re.sub(r"[^a-z0-9]+", "-", folded).strip("-")


def candidate_urls(name: str) -> list[str]:
    """The pages to try for an app called ``name``: its whole name, then the
    part before a subtitle ("Duolingo: Language Lessons" is reviewed as
    "duolingo"), each as an app review and then as a game review."""
    names = [name]
    for sep in (":", " - ", " \u2013 ", " | ", "("):
        if sep in name:
            names.append(name.split(sep, 1)[0])
    slugs = list(dict.fromkeys(s for s in (slug(n) for n in names) if s))
    urls = [f"{SITE}/{section}/{s}" for s in slugs for section in SECTIONS]
    return urls[:MAX_CANDIDATES]


def redirect_target(location: str | None) -> str | None:
    """A redirect worth following: another app or game review on the same
    site (a renamed slug). Anything else (an article, another site) is not."""
    if not location:
        return None
    url = urljoin(SITE + "/", location)
    parts = urlparse(url)
    if parts.scheme != "https" or parts.netloc != urlparse(SITE).netloc:
        return None
    segments = [s for s in parts.path.split("/") if s]
    if len(segments) != 2 or segments[0] not in SECTIONS:
        return None
    return f"{SITE}/{segments[0]}/{segments[1]}"


def _text(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", html.unescape(_TAG.sub(" ", value)))
    return re.sub(r" ([,.;:!?])", r"\1", cleaned).strip()


def _norm_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", unicodedata.normalize("NFKD", value).lower())


def same_app(page_name: str | None, app_name: str) -> bool:
    """Whether the page reviews this app: the names agree once case and
    punctuation are set aside, or one is the other with a subtitle."""
    a, b = _norm_name(page_name or ""), _norm_name(app_name or "")
    if not a or not b:
        return False
    return a == b or (len(a) >= 4 and b.startswith(a)) or (len(b) >= 4 and a.startswith(b))


def _review_ld(page: str) -> dict[str, Any] | None:
    for block in _LD.findall(page):
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        nodes = data.get("@graph") if isinstance(data, dict) and isinstance(data.get("@graph"), list) else [data]
        for node in nodes:
            if not isinstance(node, dict) or node.get("@type") != "Review":
                continue
            item = node.get("itemReviewed")
            if isinstance(item, dict) and item.get("@type") in _ITEM_TYPES:
                return node
    return None


def _consensus(page: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for block in _STATS.findall(page):
        based = _BASED_ON.search(_text(block))
        age = _SHOWN_AGE.search(block)
        if not based or not age:
            continue
        who = "parents" if based.group(2).lower() == "parent" else "kids"
        if who in out:
            continue
        says = _SAYS.search(block)
        out[who] = {
            "age": int(age.group(1)),
            "reviews": int(based.group(1).replace(",", "")),
            "summary": _text(says.group(1))[:MAX_CONSENSUS_SUMMARY] if says else None,
        }
    return out


def parse(page: str, url: str) -> dict[str, Any]:
    """The review's age rating, "Parents need to know" and the parents' and
    kids' consensus. Raises CommonSenseError when the page is not a review."""
    node = _review_ld(page or "")
    if node is None:
        raise CommonSenseError("the page has no app or game review data")
    item = node["itemReviewed"]
    age_match = _AGE.match(str(node.get("typicalAgeRange") or ""))
    if not age_match:
        raise CommonSenseError("the review has no age rating")
    age = int(age_match.group(1))
    shown = _SHOWN_AGE.search(page)
    if shown and int(shown.group(1)) != age:
        raise CommonSenseError(f"the page shows age {shown.group(1)}+ but its review data says {age}+")
    # The page shows a teaser and the whole paragraph; keep the whole one.
    paragraphs = [_text(p) for p in _PNTK.findall(page)]
    if not paragraphs:
        raise CommonSenseError("the review has no \"Parents need to know\" section")
    pntk = max(paragraphs, key=len)
    stars = node.get("reviewRating", {}).get("ratingValue") if isinstance(node.get("reviewRating"), dict) else None
    try:
        quality = int(stars) if stars is not None else None
    except (TypeError, ValueError):
        quality = None
    consensus = _consensus(page)
    return {
        "url": url,
        "name": str(item.get("name") or "")[:120] or None,
        "kind": "game" if "/game-reviews/" in url else "app",
        "age": age,
        "age_label": f"age {age}+",
        "quality_stars": quality if quality and 1 <= quality <= 5 else None,
        "parents_need_to_know": pntk[:MAX_SUMMARY],
        "parents_say": consensus.get("parents"),
        "kids_say": consensus.get("kids"),
        "published": str(node.get("datePublished"))[:10] if node.get("datePublished") else None,
    }

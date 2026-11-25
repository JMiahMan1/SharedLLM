"""What may be searched for and played from the web on a household screen.

Children use the household's TVs, and a voice request is easily misheard: "Play Keepy
Uppy on YouTube" was once heard as "keeping up beyond YouTube", and YouTube's first
result (a celebrity gossip clip) played on the family TV. So every web video search and
play goes through here:

* **Safe search** is the household setting ``video_safe_search``: ``strict`` (the
  default, and what an unreadable or unknown value means), ``moderate`` or ``off``. It is
  a global setting, so only an admin can relax it, and it is never relaxed for a request
  from a child or teen (``UserContext.is_minor``) or from a speaker nobody recognised.
* **Screening** before anything plays on a device: a video marked for ages 13 and over
  is refused on a shared device or for a minor, one marked 18 and over always, and a
  title or channel matching the admin-editable ``video_blocklist`` setting (empty by
  default) is refused. A video whose rating cannot be checked is not played.
* **Matching**: a result is played without asking only when its title (or channel and
  title) is a strong match for what was asked (``match_score``), by the same title and
  sound-alike rules the media finder uses for library titles.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Any

from services.common.media_titles import title_score

log = logging.getLogger("execution.video_safety")

#: Household setting: strict (default) | moderate | off. Admin-only (a global setting).
SAFE_SEARCH_SETTING = "video_safe_search"
#: Household setting: words or phrases, one per line or comma separated, refused in a title or channel.
BLOCKLIST_SETTING = "video_blocklist"
#: SearXNG's ``safesearch`` value for each level.
SEARXNG_SAFESEARCH = {"strict": 2, "moderate": 1, "off": 0}
DEFAULT_LEVEL = "strict"
#: The user a request is made as when no speaker was recognised.
HOUSEHOLD_USER = "household"

#: Refused at this rating or above for a minor or on a shared device (every TV and speaker is one).
SHARED_AGE_LIMIT = 13
#: Refused at this rating or above always.
ADULT_AGE_LIMIT = 18

#: A video is played without asking only at or above this match score.
STRONG_MATCH = 0.85

#: How long a video's checked facts are kept, so choosing and then playing it checks it once.
_CHECK_TTL_S = 600.0
_CHECK_MAX = 64
_checked: dict[str, tuple[float, dict[str, Any]]] = {}

#: Words YouTube titles carry that are not part of what anyone asks for.
_TITLE_FILLER = re.compile(
    r"\b(?:full\s+(?:movie|episodes?)|official\s+(?:music\s+)?(?:video|audio)|lyric\s+video|hd|4k)\b",
    re.IGNORECASE)
#: Where a YouTube title separates its parts ("Show | Episode | Full Episode").
_TITLE_PARTS = re.compile(r"\s*\|\s*|\s*:\s+|\s+[-\u2013\u2014/]\s+")
#: An error from the video site that means the video is age-gated.
_AGE_GATED = re.compile(r"confirm your age|age[- ]restrict|inappropriate for some users|sign in to confirm", re.IGNORECASE)


def is_minor(ctx: Any) -> bool:
    return bool(getattr(ctx, "is_minor", False))


def _relaxable(ctx: Any) -> bool:
    """Only a recognised adult's request may use a relaxed household level."""
    user = str(getattr(ctx, "user", "") or "")
    return bool(ctx) and not is_minor(ctx) and bool(user) and user != HOUSEHOLD_USER


async def _setting(key: str) -> str:
    from services.execution.handlers import media as media_handler

    return await media_handler._global_setting(key)


async def safe_search_level(ctx: Any) -> str:
    """strict, moderate or off for this request: the household setting, never relaxed for a child."""
    raw = (await _setting(SAFE_SEARCH_SETTING)).strip().lower()
    level = raw or DEFAULT_LEVEL
    if level not in SEARXNG_SAFESEARCH:
        log.warning(f"[video/safety] {SAFE_SEARCH_SETTING}={raw!r} is not strict, moderate or off; using strict")
        level = DEFAULT_LEVEL
    if level != DEFAULT_LEVEL and not _relaxable(ctx):
        return DEFAULT_LEVEL
    return level


def parse_blocklist(raw: str) -> list[str]:
    return [w.strip().lower() for w in re.split(r"[,\n]", raw or "") if w.strip()]


async def blocklist() -> list[str]:
    return parse_blocklist(await _setting(BLOCKLIST_SETTING))


def blocked_by(title: str, channel: str, words: list[str]) -> str | None:
    """The blocklist entry the title or channel matches (whole words, any case), or None."""
    text = f"{title or ''} {channel or ''}".lower()
    for w in words:
        if re.search(rf"(?<![a-z0-9]){re.escape(w)}(?![a-z0-9])", text):
            return w
    return None


def age_refused(age_limit: Any, ctx: Any, *, shared: bool = True) -> bool:
    """True when a video rated ``age_limit`` may not play for this request."""
    try:
        age = int(age_limit or 0)
    except (TypeError, ValueError):
        return True  # a rating that cannot be read is not a rating that allows it
    limit = SHARED_AGE_LIMIT if (shared or is_minor(ctx)) else ADULT_AGE_LIMIT
    return age >= limit


def match_score(query: str, title: str, channel: str = "") -> float:
    """How well a video's title (or a part of it, or channel and title) names what was asked, 0..1."""
    if not title:
        return 0.0
    cleaned = " ".join(_TITLE_FILLER.sub(" ", title).split())
    variants = {title, cleaned}
    variants.update(p.strip() for p in _TITLE_PARTS.split(cleaned) if p.strip())
    if channel:
        variants.add(f"{channel} {cleaned}")
    return max(title_score(query, v) for v in variants)


@dataclass
class Verdict:
    ok: bool
    why: str = ""            # "", "age_restricted", "blocked", "unchecked"
    title: str = ""
    channel: str = ""
    age_limit: int | None = None


def _remember(url: str, info: dict[str, Any]) -> None:
    if len(_checked) >= _CHECK_MAX:
        _checked.pop(next(iter(_checked)))
    _checked[url] = (time.monotonic(), info)


async def video_facts(url: str) -> dict[str, Any]:
    """{"title", "channel", "age_limit"} of ``url`` from the site (kept for a few minutes)."""
    hit = _checked.get(url)
    if hit and time.monotonic() - hit[0] < _CHECK_TTL_S:
        return hit[1]
    from services.execution.handlers import video as video_handler

    try:
        info = await video_handler.inspect_video(url)
    except Exception as exc:
        if _AGE_GATED.search(str(exc)):
            info = {"title": "", "channel": "", "age_limit": ADULT_AGE_LIMIT}
        else:
            raise
    _remember(url, info)
    return info


async def screen_url(url: str, ctx: Any, *, words: list[str] | None = None, shared: bool = True) -> Verdict:
    """Whether ``url`` may play on a device for this request (its rating and the blocklist)."""
    try:
        info = await video_facts(url)
    except Exception as exc:
        log.warning(f"[video/safety] could not check a video's rating, so it is not played: {exc}")
        return Verdict(False, "unchecked")
    title, channel = str(info.get("title") or ""), str(info.get("channel") or "")
    age = info.get("age_limit")
    if age_refused(age, ctx, shared=shared):
        return Verdict(False, "age_restricted", title, channel, age)
    if words is None:
        words = await blocklist()
    if blocked_by(title, channel, words):
        return Verdict(False, "blocked", title, channel, age)
    return Verdict(True, "", title, channel, age)


def refusal_message(why: str) -> str:
    if why == "unchecked":
        return "I couldn't check that video is suitable, so I didn't play it."
    return "That video isn't suitable to play here, so I didn't play it."


def log_decision(query: str, title: str, score: float, level: str, action: str, why: str) -> None:
    """One line per web video decision. No user, device or account: only what was asked and found."""
    log.info(f"[Reply] video_search: query={query!r} chosen={title!r} score={round(score, 3)} "
             f"safe_search={level} decision={action} why={why}")

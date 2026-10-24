"""Video / creator theme recommendations (Phase 11, step 3).

Jarvis reads a person's recent watching (the media-session feed) and suggests
a redeemed alternative for a creator or theme that is not appropriate: first
try to redeem the theme (a wholesome creator with the same kind of content),
and if no redeemed equivalent exists, redirect to a different wholesome
activity.

Matching works on themes and styles, not names: a small reviewed map of video
themes, each carrying one vetted recommendation with a plain-language reason.

The raw feed stays parents-only; only the derived suggestion is returned to
the member it is for.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

# A "theme" is a content bucket. Each theme maps to one vetted recommendation.
# `redeemed` is True when the suggestion is the same kind of content made
# wholesome, and False when the suggestion redirects to a different activity.
THEMES: dict[str, dict[str, Any]] = {
    "gaming": {
        "creator": "DanTDM",
        "style": "gaming, family-friendly, adventure",
        "reason": "Same building, exploring and fun, kept light and friendly for everyone.",
        "redeemed": True,
    },
    "comedy": {
        "creator": "Dude Perfect",
        "style": "comedy, sports, trick shots",
        "reason": "Same laughs and big moments, clean and family-friendly.",
        "redeemed": True,
    },
    "science": {
        "creator": "Mark Rober",
        "style": "science, engineering, experiments",
        "reason": "Same curiosity and big experiments, pointed at wonder and learning.",
        "redeemed": True,
    },
    "vlog": {
        "creator": "Yes Theory",
        "style": "vlog, adventure, growth",
        "reason": "Same adventure and storytelling, aimed at growth and connection.",
        "redeemed": True,
    },
    "fitness": {
        "creator": "MadFit",
        "style": "fitness, workouts, movement",
        "reason": "Same energy and movement, aimed at health and strength.",
        "redeemed": True,
    },
    "music video": {
        "creator": "for KING & COUNTRY",
        "style": "music video, cinematic, hopeful",
        "reason": "Same cinematic pop energy, with lyrics that lift people up.",
        "redeemed": True,
    },
    "film": {
        "creator": "Angel Studios",
        "style": "film, story, hope",
        "reason": "Same storytelling and drama, with hope and redemption at the center.",
        "redeemed": True,
    },
    "animation": {
        "creator": "VeggieTales",
        "style": "animation, kids, character",
        "reason": "Same color and fun, with lessons about character and kindness.",
        "redeemed": True,
    },
    "news": {
        "creator": None,
        "style": "family, scripture, conversation",
        "reason": "News and hot takes can weigh heavy; a chapter of Proverbs and a talk together lifts the room instead.",
        "redeemed": False,
        "redirect_to": "read a chapter of Proverbs together",
    },
    "prank": {
        "creator": None,
        "style": "family, play, togetherness",
        "reason": "Pranks can sting; a game night or a walk together builds the same laughs without the hurt.",
        "redeemed": False,
        "redirect_to": "play a board game together",
    },
}

# Creator / channel / platform keywords that signal a theme. First match wins.
CREATOR_TRIGGERS: dict[str, str] = {
    "pewdiepie": "gaming",
    "mrbeast": "comedy",
    "logan paul": "comedy",
    "jake paul": "comedy",
    "dude perfect": "comedy",
    "mark rober": "science",
    "casey neistat": "vlog",
    "emma chamberlain": "vlog",
    "chloe ting": "fitness",
    "madfit": "fitness",
    "ninja": "gaming",
    "shroud": "gaming",
    "jacksepticeye": "gaming",
    "markiplier": "gaming",
    "twitch": "gaming",
    "youtube": "comedy",
}

TOPIC_TRIGGERS: dict[str, str] = {
    "minecraft": "gaming",
    "fortnite": "gaming",
    "roblox": "gaming",
    "valorant": "gaming",
    "apex": "gaming",
    "gaming": "gaming",
    "gamer": "gaming",
    "prank": "prank",
    "challenge": "comedy",
    "skit": "comedy",
    "funny": "comedy",
    "comedy": "comedy",
    "science": "science",
    "experiment": "science",
    "engineering": "science",
    "tech": "science",
    "vlog": "vlog",
    "day in the life": "vlog",
    "travel": "vlog",
    "fitness": "fitness",
    "workout": "fitness",
    "gym": "fitness",
    "exercise": "fitness",
    "music video": "music video",
    "official video": "music video",
    "mv": "music video",
    "movie": "film",
    "trailer": "film",
    "film": "film",
    "cinema": "film",
    "cartoon": "animation",
    "animation": "animation",
    "anime": "animation",
    "news": "news",
    "politics": "news",
    "debate": "news",
    "beauty": "comedy",
    "makeup": "comedy",
}


def _theme_for_text(text: str) -> str | None:
    t = (text or "").strip().lower()
    if not t:
        return None
    # Creator match first (more specific).
    for creator, theme in CREATOR_TRIGGERS.items():
        if creator in t:
            return theme
    # Then topic match.
    for topic, theme in TOPIC_TRIGGERS.items():
        if topic in t:
            return theme
    return None


def recommend_video(recent_items: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    """Return a recommendation dict for the given recent watching items, or None.

    Each item is an activity event (feature media, item title, detail with
    app/artist/album). We scan newest-first and return the first theme we can
    place, so a single off-theme video is enough to suggest an alternative.
    """
    for item in recent_items or []:
        detail = item.get("detail") or {}
        candidates = [
            detail.get("artist"),
            item.get("item"),
            detail.get("app"),
            detail.get("album"),
        ]
        for c in candidates:
            theme = _theme_for_text(str(c) if c is not None else "")
            if theme and theme in THEMES:
                rec = THEMES[theme]
                out = {
                    "theme": theme,
                    "creator": rec["creator"],
                    "style": rec["style"],
                    "reason": rec["reason"],
                    "matched_on": str(c),
                    "kind": "video",
                    "redeemed": bool(rec.get("redeemed", True)),
                }
                if not rec.get("redeemed", True):
                    out["redirect_to"] = rec.get("redirect_to")
                return out
    return None


def all_themes() -> list[dict[str, Any]]:
    return [{"theme": k, **v} for k, v in THEMES.items()]

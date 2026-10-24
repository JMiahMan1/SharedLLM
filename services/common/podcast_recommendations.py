"""Podcast theme recommendations (Phase 11, step 4).

Jarvis reads a person's recent podcast listening (the activity feed) and
suggests a redeemed alternative for a podcast or theme: first try to redeem
the theme (a wholesome Christian show with a similar topic or style), and if
no redeemed equivalent exists, redirect to a wholesome family activity.

The raw feed stays parents-only; only the derived suggestion is returned to
the member it is for.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

THEMES: dict[str, dict[str, Any]] = {
    "worldview news": {
        "show": "The World and Everything in It",
        "host": "WORLD News Group",
        "style": "news, current events, Christian worldview",
        "reason": "Current events reported with clarity, depth, and a faithful biblical worldview.",
        "redeemed": True,
    },
    "theology study": {
        "show": "The Bible Project Podcast",
        "host": "Tim Mackie & Jon Collins",
        "style": "scripture, biblical theology, conversations",
        "reason": "Thoughtful conversations tracing the unifying story of Jesus throughout Scripture.",
        "redeemed": True,
    },
    "history story": {
        "show": "5 Minutes in Church History",
        "host": "Stephen Nichols",
        "style": "church history, biographies, storytelling",
        "reason": "Quick, fascinating insights into the great men and women of Christian history.",
        "redeemed": True,
    },
    "family parenting": {
        "show": "Risen Motherhood",
        "host": "Emily Jensen & Laura Wifler",
        "style": "parenting, family, gospel-centered home",
        "reason": "Encouraging gospel wisdom applied to the daily rhythms of home and family life.",
        "redeemed": True,
    },
    "kids drama": {
        "show": "Adventures in Odyssey",
        "host": "Focus on the Family",
        "style": "audio drama, family, faith adventures",
        "reason": "Imaginative stories that teach faith, character, and moral courage.",
        "redeemed": True,
    },
    "culture conversation": {
        "show": "That Sounds Fun",
        "host": "Annie F. Downs",
        "style": "conversations, faith, encouragement",
        "reason": "Warm and joyful conversations with authors, musicians, and inspiring guests.",
        "redeemed": True,
    },
    "apologetics questions": {
        "show": "Ask Pastor John",
        "host": "John Piper",
        "style": "biblical answers, theology, daily life",
        "reason": "Direct, pastoral answers to tough biblical and theological questions.",
        "redeemed": True,
    },
    "science nature": {
        "show": "Science & Faith Podcast",
        "host": "Discovery Institute",
        "style": "science, intelligent design, wonder",
        "reason": "Exploring the wonder of creation and the scientific evidence for design.",
        "redeemed": True,
    },
    "true crime": {
        "show": None,
        "host": None,
        "style": "scripture, peace, audio drama",
        "reason": "True crime and graphic tragedy bring anxiety; an uplifting audio drama or peaceful walk brings rest.",
        "redeemed": False,
        "redirect_to": "listen to an audio drama or take a walk outside together",
    },
    "gossip drama": {
        "show": None,
        "host": None,
        "style": "fellowship, conversation, gratitude",
        "reason": "Celebrity gossip and drama feed cynicism; sharing what we are grateful for lifts the spirit.",
        "redeemed": False,
        "redirect_to": "share stories of gratitude around the dinner table",
    },
}

SHOW_TRIGGERS: dict[str, str] = {
    "joe rogan": "culture conversation",
    "serial": "true crime",
    "crime junkie": "true crime",
    "my favorite murder": "true crime",
    "morbid": "true crime",
    "call her daddy": "gossip drama",
    "the daily": "worldview news",
    "up first": "worldview news",
    "huberman": "science nature",
    "smartless": "culture conversation",
    "armchair expert": "culture conversation",
    "lex fridman": "science nature",
    "bible project": "theology study",
    "risen motherhood": "family parenting",
    "adventures in odyssey": "kids drama",
}

GENRE_TRIGGERS: dict[str, str] = {
    "true crime": "true crime",
    "crime": "true crime",
    "murder": "true crime",
    "gossip": "gossip drama",
    "drama": "gossip drama",
    "celebrity": "gossip drama",
    "news": "worldview news",
    "politics": "worldview news",
    "theology": "theology study",
    "bible": "theology study",
    "scripture": "theology study",
    "history": "history story",
    "biography": "history story",
    "parenting": "family parenting",
    "motherhood": "family parenting",
    "fatherhood": "family parenting",
    "kids": "kids drama",
    "audio drama": "kids drama",
    "science": "science nature",
    "apologetics": "apologetics questions",
    "interview": "culture conversation",
}


# Matched as whole words, longest trigger first, so "audio drama" wins over
# "drama" and "news" never fires inside a name like "Newsboys".
_TRIGGERS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(rf"(?<![a-z0-9]){re.escape(trigger)}(?![a-z0-9])"), theme)
    for table in (SHOW_TRIGGERS, GENRE_TRIGGERS)
    for trigger, theme in sorted(table.items(), key=lambda kv: -len(kv[0]))
]

# The phone's media-session reader posts every external app as feature
# "media"; such an event counts only from these apps, or when it names its
# show or podcast outright.
PODCAST_APPS = ("podcast", "pocketcasts", "antennapod", "castbox")


def podcast_events(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """The activity events that are podcast listening."""
    picked = []
    for event in events or []:
        feature = str(event.get("feature") or "").lower()
        detail = event.get("detail") or {}
        app = str(detail.get("app") or "").lower()
        named = any(detail.get(key) for key in ("show", "podcast"))
        if feature == "podcast" or (feature == "media" and (named or any(a in app for a in PODCAST_APPS))):
            picked.append(event)
    return picked


def _theme_for_text(text: str) -> str | None:
    t = (text or "").strip().lower()
    if not t:
        return None
    for pattern, theme in _TRIGGERS:
        if pattern.search(t):
            return theme
    return None


def recommend_podcast(recent_items: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    """Return a recommendation dict for recent podcast activity, or None.

    Each item is an activity event (feature media/podcast, item title, detail with
    show/author/genre/app). We scan newest-first and return the first matching theme.
    """
    for item in recent_items or []:
        detail = item.get("detail") or {}
        candidates = [
            detail.get("show"),
            detail.get("podcast"),
            detail.get("author"),
            detail.get("host"),
            detail.get("genre"),
            detail.get("channel"),
            item.get("item"),
            detail.get("app"),
        ]
        for c in candidates:
            theme = _theme_for_text(str(c) if c is not None else "")
            if theme and theme in THEMES:
                rec = THEMES[theme]
                return {
                    "theme": theme,
                    "show": rec["show"],
                    "host": rec["host"],
                    "style": rec["style"],
                    "reason": rec["reason"],
                    "matched_on": str(c),
                    "kind": "podcast",
                    "redeemed": rec["redeemed"],
                    **({"redirect_to": rec["redirect_to"]} if "redirect_to" in rec else {}),
                }
    return None


def all_themes() -> list[dict[str, Any]]:
    return [{"theme": k, **v} for k, v in THEMES.items()]

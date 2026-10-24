"""Book / literature theme recommendations (Phase 11, step 4).

Jarvis reads a person's recent reading activity (the activity feed) and
suggests a redeemed alternative for a book or theme: first try to redeem
the theme (a classic or wholesome book with a similar genre or style), and if
no redeemed equivalent exists, redirect to a wholesome family reading activity.

The raw feed stays parents-only; only the derived suggestion is returned to
the member it is for.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

THEMES: dict[str, dict[str, Any]] = {
    "fantasy adventure": {
        "title": "The Chronicles of Narnia",
        "author": "C.S. Lewis",
        "style": "fantasy, adventure, classic",
        "reason": "Classic adventures filled with wonder, bravery, and the beauty of Aslan's realm.",
        "redeemed": True,
    },
    "epic fantasy": {
        "title": "The Wingfeather Saga",
        "author": "Andrew Peterson",
        "style": "epic fantasy, family, heroic courage",
        "reason": "An extraordinary tale of family sacrifice, redemption, and courage against darkness.",
        "redeemed": True,
    },
    "classic theology": {
        "title": "Mere Christianity",
        "author": "C.S. Lewis",
        "style": "apologetics, faith, classic",
        "reason": "A brilliant, timeless exploration of the bedrock truths of Christian faith.",
        "redeemed": True,
    },
    "encouragement grace": {
        "title": "Gentle and Lowly",
        "author": "Dane Ortlund",
        "style": "devotional, Christ's heart, comfort",
        "reason": "A tender reflection on the deep love and kindness of Jesus toward the weary.",
        "redeemed": True,
    },
    "mystery detective": {
        "title": "The Complete Father Brown Stories",
        "author": "G.K. Chesterton",
        "style": "mystery, detective, wit, wisdom",
        "reason": "Ingenious mysteries solved with humility, psychological insight, and moral clarity.",
        "redeemed": True,
    },
    "biography courage": {
        "title": "The Hiding Place",
        "author": "Corrie ten Boom",
        "style": "biography, wartime courage, forgiveness",
        "reason": "An unforgettable true story of faith, resilience, and supernatural forgiveness.",
        "redeemed": True,
    },
    "sci-fi thought": {
        "title": "Out of the Silent Planet",
        "author": "C.S. Lewis",
        "style": "science fiction, space travel, philosophy",
        "reason": "A space adventure contrasting cosmic wonder and virtue with human pride.",
        "redeemed": True,
    },
    "spiritual growth": {
        "title": "Habits of Grace",
        "author": "David Mathis",
        "style": "spiritual disciplines, habits, joy",
        "reason": "Simple, life-giving rhythms to hear God's voice, have His ear, and belong to His body.",
        "redeemed": True,
    },
    "young readers": {
        "title": "The Green Ember",
        "author": "S.D. Smith",
        "style": "children's adventure, heroic, honor",
        "reason": "Inspiring adventures of rabbits with swords fighting for a fallen kingdom with honor.",
        "redeemed": True,
    },
    "grimdark horror": {
        "title": None,
        "author": None,
        "style": "encouragement, light, scripture",
        "reason": "Dark and nihilistic fiction leaves emptiness in the mind; wholesome literature fills it with hope.",
        "redeemed": False,
        "redirect_to": "read an uplifting classic or a chapter of Psalms together",
    },
}

AUTHOR_TRIGGERS: dict[str, str] = {
    "stephen king": "grimdark horror",
    "george r.r. martin": "grimdark horror",
    "lovecraft": "grimdark horror",
    "colleen hoover": "encouragement grace",
    "tolkien": "epic fantasy",
    "j.r.r. tolkien": "epic fantasy",
    "andrew peterson": "epic fantasy",
    "c.s. lewis": "fantasy adventure",
    "cs lewis": "fantasy adventure",
    "chesterton": "mystery detective",
    "g.k. chesterton": "mystery detective",
    "corrie ten boom": "biography courage",
    "dane ortlund": "encouragement grace",
    "john piper": "classic theology",
    "j.i. packer": "classic theology",
    "richard foster": "spiritual growth",
    "s.d. smith": "young readers",
}

GENRE_TRIGGERS: dict[str, str] = {
    "grimdark": "grimdark horror",
    "horror": "grimdark horror",
    "thriller": "grimdark horror",
    "nihilism": "grimdark horror",
    "fantasy": "fantasy adventure",
    "epic fantasy": "epic fantasy",
    "theology": "classic theology",
    "apologetics": "classic theology",
    "devotional": "encouragement grace",
    "grace": "encouragement grace",
    "mystery": "mystery detective",
    "detective": "mystery detective",
    "biography": "biography courage",
    "memoir": "biography courage",
    "sci-fi": "sci-fi thought",
    "science fiction": "sci-fi thought",
    "habits": "spiritual growth",
    "disciplines": "spiritual growth",
    "kids": "young readers",
    "children": "young readers",
}


# Matched as whole words, longest trigger first, so "epic fantasy" wins over
# "fantasy" and "grace" never fires inside a word like "disgraced".
_TRIGGERS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(rf"(?<![a-z0-9]){re.escape(trigger)}(?![a-z0-9])"), theme)
    for table in (AUTHOR_TRIGGERS, GENRE_TRIGGERS)
    for trigger, theme in sorted(table.items(), key=lambda kv: -len(kv[0]))
]

# The phone's media-session reader posts every external app as feature
# "media"; such an event counts only from these apps, or when it names its
# book or author outright.
BOOK_APPS = ("audible", "libby", "audiobook", "books", "kindle", "storytel")


def book_events(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """The activity events that are reading or audiobook listening."""
    picked = []
    for event in events or []:
        feature = str(event.get("feature") or "").lower()
        detail = event.get("detail") or {}
        app = str(detail.get("app") or "").lower()
        named = any(detail.get(key) for key in ("book", "author"))
        if feature in ("audiobook", "book", "reading") or (feature == "media" and (named or any(a in app for a in BOOK_APPS))):
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


def recommend_book(recent_items: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    """Return a recommendation dict for recent reading activity, or None.

    Each item is an activity event (feature media/book/reading, item title, detail with
    book/title/author/genre/app). We scan newest-first and return the first matching theme.
    """
    for item in recent_items or []:
        detail = item.get("detail") or {}
        candidates = [
            detail.get("book"),
            detail.get("title"),
            detail.get("author"),
            detail.get("genre"),
            detail.get("publisher"),
            item.get("item"),
            detail.get("app"),
        ]
        for c in candidates:
            theme = _theme_for_text(str(c) if c is not None else "")
            if theme and theme in THEMES:
                rec = THEMES[theme]
                return {
                    "theme": theme,
                    "title": rec["title"],
                    "author": rec["author"],
                    "style": rec["style"],
                    "reason": rec["reason"],
                    "matched_on": str(c),
                    "kind": "book",
                    "redeemed": rec["redeemed"],
                    **({"redirect_to": rec["redirect_to"]} if "redirect_to" in rec else {}),
                }
    return None


def all_themes() -> list[dict[str, Any]]:
    return [{"theme": k, **v} for k, v in THEMES.items()]

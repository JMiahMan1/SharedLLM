"""Game recommendations (Phase 11, step 3).

Jarvis reads which games a member actually spends time in -- the phone's
per-app foreground time, plus game titles from media sessions -- and, for a
game whose content is not a good fit, suggests one that keeps what makes it fun
(redeemed), or redirects to something different when nothing like it is
(gambling-style games become a family board game night).

Games already in good shape (Minecraft, Mario, Stardew Valley, the games in
Jarvis) get no suggestion: the point is direction, not nagging. Matching works
on reviewed themes over package names and titles, weighted by time played this
week, so one stray minute of a game does not decide anything.

The raw usage stays parents-only; only the derived suggestion is returned to
the member it is for.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

# Each theme: what the suggestion is, why, and whether it keeps the same kind
# of play (redeemed) or points somewhere else (redirect_to).
THEMES: dict[str, dict[str, Any]] = {
    "shooter": {
        "game": "Rocket League",
        "style": "fast team matches, competitive, no violence",
        "reason": "Same team play, quick rounds and big comebacks, with cars and a ball instead of weapons.",
        "redeemed": True,
    },
    "horror": {
        "game": "Portal 2 (co-op)",
        "style": "puzzle adventure, clever, funny",
        "reason": "Same tension of figuring a way out, built on wit and teamwork instead of fear.",
        "redeemed": True,
    },
    "mature open world": {
        "game": "LEGO City Undercover",
        "style": "open world city, driving, missions",
        "reason": "Same big city to roam and missions to run, with humour instead of crime.",
        "redeemed": True,
    },
    "open sandbox": {
        "game": "Minecraft (a family world)",
        "style": "building, survival, creative",
        "reason": "Same building and exploring, in a world the family shares instead of open chat with strangers.",
        "redeemed": True,
    },
    "fighting": {
        "game": "Nintendo Switch Sports",
        "style": "head-to-head, active, quick matches",
        "reason": "Same one-on-one competition, and it gets everyone off the couch.",
        "redeemed": True,
    },
    "gambling": {
        "game": None,
        "style": "",
        "reason": "Games built on spins and loot boxes train the habit of chasing a win; real play needs no wager.",
        "redeemed": False,
        "redirect_to": "a family board game night (Ticket to Ride or Settlers of Catan)",
    },
}

# Theme triggers over package names and titles: whole words of a package
# ("com.epicgames.fortnite" -> epicgames, fortnite) or a title.
_TRIGGERS: dict[str, tuple[str, ...]] = {
    "shooter": (
        "fortnite", "callofduty", "call of duty", "pubg", "pubgm", "freefire", "freefireth",
        "free fire", "apex", "apexlegends", "valorant", "codm", "standoff2", "warzone",
    ),
    "horror": (
        "fnaf", "five nights", "freddy", "poppy playtime", "poppyplaytime", "granny",
        "dead by daylight", "deadbydaylight", "evil nun", "evilnun", "the backrooms",
    ),
    "mature open world": ("gta", "grand theft auto", "gtasa", "gta5", "red dead", "reddead"),
    "open sandbox": ("roblox",),
    "fighting": ("mortal kombat", "mortalkombat", "mkmobile", "tekken"),
    "gambling": (
        "coinmaster", "coin master", "slots", "casino", "slotomania", "huuuge", "doubleu",
        "bingo blitz", "bingoblitz", "poker",
    ),
}

# Games that are already a good fit: never the reason for a suggestion.
_GOOD_FITS = ("minecraft", "mojang", "stardew", "mario", "nintendo", "lego", "zelda", "animal crossing", "jarvis")

# How a matched game is named back to the member ("Because you've been playing ...").
_LABELS = {
    "fortnite": "Fortnite", "callofduty": "Call of Duty", "call of duty": "Call of Duty", "codm": "Call of Duty",
    "warzone": "Call of Duty", "pubg": "PUBG", "pubgm": "PUBG", "freefire": "Free Fire", "freefireth": "Free Fire",
    "free fire": "Free Fire", "apex": "Apex Legends", "apexlegends": "Apex Legends", "valorant": "Valorant",
    "standoff2": "Standoff 2", "fnaf": "Five Nights at Freddy's", "five nights": "Five Nights at Freddy's",
    "freddy": "Five Nights at Freddy's", "poppy playtime": "Poppy Playtime", "poppyplaytime": "Poppy Playtime",
    "granny": "Granny", "dead by daylight": "Dead by Daylight", "deadbydaylight": "Dead by Daylight",
    "evil nun": "Evil Nun", "evilnun": "Evil Nun", "the backrooms": "The Backrooms", "gta": "Grand Theft Auto",
    "grand theft auto": "Grand Theft Auto", "gtasa": "Grand Theft Auto", "gta5": "Grand Theft Auto",
    "red dead": "Red Dead Redemption", "reddead": "Red Dead Redemption", "roblox": "Roblox",
    "mortal kombat": "Mortal Kombat", "mortalkombat": "Mortal Kombat", "mkmobile": "Mortal Kombat",
    "tekken": "Tekken", "coinmaster": "Coin Master", "coin master": "Coin Master", "slots": "slot games",
    "casino": "casino games", "slotomania": "Slotomania", "huuuge": "Huuuge Casino", "doubleu": "DoubleU Casino",
    "bingo blitz": "Bingo Blitz", "bingoblitz": "Bingo Blitz", "poker": "poker games",
}


def _word_pattern(term: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])")


_GOOD_PATTERNS = [_word_pattern(good) for good in _GOOD_FITS]
_PATTERNS: list[tuple[re.Pattern[str], str, str]] = sorted(
    ((_word_pattern(trigger), theme, trigger) for theme, triggers in _TRIGGERS.items() for trigger in triggers),
    key=lambda entry: -len(entry[2]),
)


def _words(text: str) -> str:
    """A package or title as lowercase words: dots, dashes and underscores split."""
    return re.sub(r"[._\-:]+", " ", str(text or "").lower()).strip()


def match_game(name: str) -> tuple[str, str] | None:
    """(theme, game label) for a package or title, or None."""
    words = _words(name)
    if not words or any(good.search(words) for good in _GOOD_PATTERNS):
        return None
    for pattern, theme, trigger in _PATTERNS:
        if pattern.search(words):
            return theme, _LABELS.get(trigger, trigger.title())
    return None


def recommend_game(
    usage_days: Iterable[dict[str, Any]],
    activity_events: Iterable[dict[str, Any]] = (),
) -> dict[str, Any] | None:
    """One game suggestion from the week's play, or None when nothing calls for one.

    ``usage_days`` are the phone's day records (``apps``: package + millis);
    ``activity_events`` add titles a media session reported (a minute each, so
    real foreground time always outweighs them).
    """
    minutes: dict[str, float] = {}
    seen: dict[str, str] = {}
    for day in usage_days or []:
        for app in day.get("apps") or []:
            hit = match_game(str(app.get("package") or ""))
            if hit:
                minutes[hit[0]] = minutes.get(hit[0], 0.0) + (app.get("millis") or 0) / 60000
                seen.setdefault(hit[0], hit[1])
    for event in activity_events or []:
        detail = event.get("detail") or {}
        for name in (event.get("item"), detail.get("app")):
            hit = match_game(str(name or ""))
            if hit:
                minutes[hit[0]] = minutes.get(hit[0], 0.0) + 1.0
                seen.setdefault(hit[0], hit[1])
    played = {theme: m for theme, m in minutes.items() if m >= 1.0}
    if not played:
        return None
    theme = max(played, key=lambda t: played[t])
    rec = THEMES[theme]
    out: dict[str, Any] = {
        "theme": theme,
        "game": rec["game"],
        "style": rec["style"],
        "reason": rec["reason"],
        "matched_on": seen[theme],
        "kind": "game",
        "redeemed": rec["redeemed"],
    }
    if not rec["redeemed"]:
        out["redirect_to"] = rec["redirect_to"]
    return out


def all_themes() -> list[dict[str, Any]]:
    return [{"theme": k, **v} for k, v in THEMES.items()]

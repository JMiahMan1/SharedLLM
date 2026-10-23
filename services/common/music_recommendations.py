"""Music style matching for the recommendations layer (Phase 11, step 3).

Jarvis reads a person's recent listening (the activity feed) and suggests a
Christian artist with a similar style — genre, era, energy — instead of an
artist whose lyrics are not appropriate. Matching works on themes and styles,
not names: a small reviewed map of lanes, each lane carrying one vetted
recommendation with a plain-language reason.

The raw feed stays parents-only; only the derived suggestion is returned to
the member it is for.
"""

from __future__ import annotations

from typing import Any, Iterable


# A "lane" is a (genre, era, energy) bucket. Each lane maps to one vetted
# recommendation. Keys are lower-case, trimmed; matching is by substring so a
# lane covers the artists and genres that fall into it. Reviewed by hand — add
# a lane only after listening to both sides.
LANES: dict[str, dict[str, str]] = {
    "pop dance": {
        "artist": "TobyMac",
        "style": "pop, dance, upbeat",
        "reason": "Same high-energy pop drive and big hooks, with lyrics that lift people up.",
    },
    "hip hop": {
        "artist": "Lecrae",
        "style": "hip hop, rap, conscious",
        "reason": "Same flow and weight, pointed at hope and purpose instead of emptiness.",
    },
    "r&b soul": {
        "artist": "Kirk Franklin",
        "style": "gospel, R&B, choir",
        "reason": "Same groove and soul, rooted in faith and community.",
    },
    "rock": {
        "artist": "Skillet",
        "style": "rock, hard rock, anthemic",
        "reason": "Same crunch and anthems, aimed at standing firm rather than tearing down.",
    },
    "country": {
        "artist": "Chris Tomlin",
        "style": "country-tinged worship, acoustic",
        "reason": "Same storytelling and warmth, turned toward gratitude and home.",
    },
    "electronic edm": {
        "artist": "Hillsong Young & Free",
        "style": "electronic, EDM, dance worship",
        "reason": "Same drops and shimmer, built for joy and celebration.",
    },
    "indie alternative": {
        "artist": "Colony House",
        "style": "indie rock, alternative, bright",
        "reason": "Same bright, guitar-led indie feel with an honest, hopeful voice.",
    },
    "acoustic folk": {
        "artist": "Jon Foreman",
        "style": "acoustic, folk, singer-songwriter",
        "reason": "Same quiet honesty and finger-picked warmth, pointed at bigger questions.",
    },
    "latin": {
        "artist": "Marcos Witt",
        "style": "latin, worship, passionate",
        "reason": "Same rhythm and passion, carrying praise and testimony.",
    },
    "classical orchestral": {
        "artist": "Hillsong Worship",
        "style": "orchestral, cinematic, worship",
        "reason": "Same sweep and grandeur, aimed at awe and reverence.",
    },
}

# Artists (lower-case) that signal a lane by name. A lane may be triggered by
# several artists; the first match wins.
ARTIST_LANES: dict[str, str] = {
    "drake": "hip hop",
    "kendrick lamar": "hip hop",
    "travis scott": "hip hop",
    "taylor swift": "pop dance",
    "dua lipa": "pop dance",
    "the weeknd": "r&b soul",
    "sza": "r&b soul",
    "imagine dragons": "rock",
    "foo fighters": "rock",
    "morgan wallen": "country",
    "luke combs": "country",
    "avicii": "electronic edm",
    "calvin harris": "electronic edm",
    "tame impala": "indie alternative",
    "the 1975": "indie alternative",
    "ed sheeran": "acoustic folk",
    "noah kahan": "acoustic folk",
    "bad bunny": "latin",
    "karol g": "latin",
    "hans zimmer": "classical orchestral",
}

GENRE_LANES: dict[str, str] = {
    "pop": "pop dance",
    "dance": "pop dance",
    "hip hop": "hip hop",
    "rap": "hip hop",
    "r&b": "r&b soul",
    "soul": "r&b soul",
    "rock": "rock",
    "metal": "rock",
    "country": "country",
    "electronic": "electronic edm",
    "edm": "electronic edm",
    "indie": "indie alternative",
    "alternative": "indie alternative",
    "folk": "acoustic folk",
    "acoustic": "acoustic folk",
    "latin": "latin",
    "reggaeton": "latin",
    "classical": "classical orchestral",
    "orchestral": "classical orchestral",
}


def _lane_for_text(text: str) -> str | None:
    t = (text or "").strip().lower()
    if not t:
        return None
    # Artist match first (more specific).
    for artist, lane in ARTIST_LANES.items():
        if artist in t:
            return lane
    # Then genre match.
    for genre, lane in GENRE_LANES.items():
        if genre in t:
            return lane
    return None


def recommend_music(recent_items: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    """Return a recommendation dict for the given recent listening items, or None.

    Each item is an activity event (feature media/music, item title, detail with
    artist/album/app). We scan newest-first and return the first lane we can
    place, so a single inappropriate track is enough to suggest an alternative.
    """
    for item in recent_items or []:
        detail = item.get("detail") or {}
        candidates = [
            detail.get("artist"),
            detail.get("album"),
            item.get("item"),
            detail.get("app"),
        ]
        for c in candidates:
            lane = _lane_for_text(str(c) if c is not None else "")
            if lane and lane in LANES:
                rec = LANES[lane]
                return {
                    "lane": lane,
                    "artist": rec["artist"],
                    "style": rec["style"],
                    "reason": rec["reason"],
                    "matched_on": str(c),
                    "kind": "music",
                }
    return None


def all_lanes() -> list[dict[str, Any]]:
    return [{"lane": k, **v} for k, v in LANES.items()]

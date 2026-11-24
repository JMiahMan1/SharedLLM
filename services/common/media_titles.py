"""How well a spoken title names a title in a media library, for every service that matches them.

Execution ranks what its media sources return ("Play Moon Garden"); the gateway checks
which library book "Read <title>" names. Both must agree on what counts as the same
title, so the rules live here once: case, punctuation, "(Unabridged)" and leading
articles do not matter, spoken numbers are digits, a title's subtitle may be left off,
and a number that was said must be in the title.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

#: A title matches a spoken name at or above this score.
MATCH_THRESHOLD = 0.7

_NUMBER_WORDS = {
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7", "eight": "8",
    "nine": "9", "ten": "10", "eleven": "11", "twelve": "12", "thirteen": "13", "fourteen": "14",
    "fifteen": "15", "sixteen": "16", "seventeen": "17", "eighteen": "18", "nineteen": "19", "twenty": "20",
    "first": "1", "second": "2", "third": "3", "fourth": "4", "fifth": "5", "sixth": "6", "seventh": "7",
    "eighth": "8", "ninth": "9", "tenth": "10", "1st": "1", "2nd": "2", "3rd": "3", "4th": "4", "5th": "5",
}
_ARTICLES = ("the", "a", "an")


def normalize(text: str | None) -> str:
    """Lower-case words for comparing titles: no "(Unabridged)", punctuation, leading article; numbers as digits."""
    t = str(text or "").lower()
    t = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", t)
    t = t.replace("&", " and ")
    t = re.sub("['" + chr(0x2019) + "]", "", t)
    t = re.sub(r"[^a-z0-9]+", " ", t)
    words = [_NUMBER_WORDS.get(w, w) for w in t.split()]
    while len(words) > 1 and words[0] in _ARTICLES:
        words = words[1:]
    return " ".join(words)


def main_title(title: str) -> str:
    """"Walking the Long Road" from "Walking the Long Road: Notes From a Journey"."""
    return re.split(r"\s*[:|]\s+|\s+-\s+", str(title or ""), maxsplit=1)[0]


def _contains(words: list[str], part: list[str]) -> bool:
    n = len(part)
    return any(words[i:i + n] == part for i in range(len(words) - n + 1)) if n else False


def title_score(query: str, title: str, *, cap: float = 1.0) -> float:
    """0..``cap``: how well a spoken ``query`` names ``title``.

    Exact (after ``normalize``) is ``cap``; the title before its subtitle is just under;
    the query as a run of the title's words scores by how much of the title it covers;
    otherwise a close spelling (speech recognition) scores by similarity. A number in the
    query that the title does not carry halves the score ("Moon Garden 3" is not book 1).
    """
    qn = normalize(query)
    if not qn:
        return 0.0
    best = 0.0
    for variant, top in ((title, cap), (main_title(title), cap * 0.98)):
        tn = normalize(variant)
        if not tn:
            continue
        qw, tw = qn.split(), tn.split()
        if qn == tn:
            score = top
        elif qn.replace(" ", "") == tn.replace(" ", ""):
            score = top * 0.99  # "Kay 9" for "KAY9": speech recognition splits the name
            best = max(best, score)
            continue
        elif _contains(tw, qw):
            score = top * (0.6 + 0.3 * len(qw) / len(tw))
        else:
            ratio = SequenceMatcher(None, qn, tn).ratio()
            score = top * 0.9 * ratio if ratio >= 0.8 else 0.0
        q_nums = {w for w in qw if w.isdigit()}
        if q_nums and not q_nums <= {w for w in tw if w.isdigit()}:
            score *= 0.5
        best = max(best, score)
    return round(best, 4)


def names_match(a: str, b: str) -> bool:
    """True when two names are the same person or band, allowing one to be part of the other."""
    an, bn = normalize(a), normalize(b)
    if not an or not bn:
        return False
    return an == bn or _contains(bn.split(), an.split()) or _contains(an.split(), bn.split()) \
        or SequenceMatcher(None, an, bn).ratio() >= 0.85


def clean_title(title: str) -> str:
    """A title as it is said: no "(Unabridged)" or "[B07...]" tags."""
    t = re.sub(r"\s*\((?:unabridged|abridged|amazonclassics edition)\)", "", str(title or ""), flags=re.IGNORECASE)
    t = re.sub(r"\s*\[[^\]]*\]", "", t)
    t = re.sub(r"\s+\|\s+.*$", "", t)  # Music Assistant's "Title | Subtitle"
    return " ".join(t.split()) or str(title or "")


def title_matches(query: str, title: str) -> bool:
    """True when ``query`` names ``title`` well enough to play or read it."""
    return title_score(query, title) >= MATCH_THRESHOLD

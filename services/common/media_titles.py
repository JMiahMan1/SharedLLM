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
            if not score:
                # Misheard but sounding the same ("moon gardin", "the grey harbors"): the last resort.
                sound = sounds_like(qn, tn)
                score = top * 0.85 * sound if sound else 0.0
        q_nums = set(re.findall(r"\d+", qn))
        if q_nums and not q_nums <= set(re.findall(r"\d+", tn)):
            score *= 0.5
        best = max(best, score)
    return round(best, 4)


#: A phonetic match counts only at or above this similarity of the two keys, and only when
#: the keys are at least ``PHONETIC_MIN_KEY`` long; shorter keys must be identical ("kay 9" /
#: "KAY9"). Short keys are too easy to share ("dune" and "june" are one consonant apart).
PHONETIC_MIN_RATIO = 0.85
PHONETIC_MIN_KEY = 5


def phonetic_key(text: str | None) -> str:
    """A rough sound of a title: its consonants, a word's first vowel as "a", similar letters merged.

    "Sunny Bee Days" and "Sunny B. Days" are both "snbds"; numbers stay as digits, so a
    number heard is still a number required. Not a full phonetic algorithm: enough to forgive
    what speech recognition does to a name, which is mostly vowels and silent letters.
    """
    out = []
    for w in normalize(text).split():
        if w.isdigit():
            out.append(w)
            continue
        w = w.replace("ph", "f")
        w = re.sub(r"c(?=[eiy])", "s", w)
        w = re.sub(r"ck|c|q", "k", w)
        w = w.replace("x", "ks").replace("z", "s")
        w = re.sub(r"gh|h", "", w)
        if not w:
            continue
        first = "a" if w[0] in "aeiouy" else w[0]
        out.append(re.sub(r"(.)\1+", r"\1", first + re.sub(r"[aeiouyw]", "", w[1:])))
    return "".join(out)


def sounds_like(a: str, b: str) -> float:
    """How alike two titles sound (0 when they do not, by the ``PHONETIC_*`` rules), 0..1."""
    ka, kb = phonetic_key(a), phonetic_key(b)
    if not ka or not kb:
        return 0.0
    if ka == kb:
        return 1.0 if min(len(ka), len(kb)) >= 3 else 0.0
    if min(len(ka), len(kb)) < PHONETIC_MIN_KEY:
        return 0.0
    ratio = SequenceMatcher(None, ka, kb).ratio()
    return ratio if ratio >= PHONETIC_MIN_RATIO else 0.0


def spoken_similarity(a: str, b: str) -> float:
    """How alike two titles are as heard, 0..1: their sound (``phonetic_key``) and their letters, averaged.

    The sound forgives vowels and silent letters; the letters keep two titles that share a
    rough sound but not their spelling ("Wild Youth" for "Wild Apart") further apart.
    """
    ka, kb = phonetic_key(a), phonetic_key(b)
    la, lb = normalize(a).replace(" ", ""), normalize(b).replace(" ", "")
    if not (ka and kb and la and lb):
        return 0.0
    return (SequenceMatcher(None, ka, kb).ratio() + SequenceMatcher(None, la, lb).ratio()) / 2


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

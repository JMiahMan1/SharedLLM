"""Matching family voices against their enrolled signatures.

A signature is a unit-length speaker embedding computed by the execution
service (``services/execution/voiceprint.py``, Resemblyzer GE2E, 256-d). Each
person enrolls several short recordings; their profile is the normalised mean
of those samples, and a new recording is scored against every profile by
cosine similarity.

The numbers below were calibrated on eight Kokoro voices, half of them sent
through browser-style WebM/Opus: the same voice scored at least 0.86 against
its own other phrases and 0.93+ against its enrolled profile. Different
voices, including two similar American female voices, never reached 0.83
against a profile. A match therefore needs 0.80 *and* a clear lead over the
runner-up, so two family members who sound alike come back as "not sure"
rather than as the wrong person.
"""

import hashlib
import json
import math

MODEL = "resemblyzer-ge2e-256"
DIMENSIONS = 256
#: Samples before a profile is used for identification. One sentence already
#: separates speakers; three averages out how a single take was spoken.
MIN_SAMPLES = 3
#: More takes stop moving the mean; keeping the newest few lets a profile
#: follow a voice that changes (a child's, or a new microphone).
MAX_SAMPLES = 6
MATCH_THRESHOLD = 0.80
MATCH_MARGIN = 0.04
#: A new take that scores below this against the person's own earlier takes
#: is almost certainly someone else, or mostly noise. Real-microphone
#: same-speaker scores sit well above it; different speakers mostly below.
CONSISTENCY_FLOOR = 0.65
#: A learned take this close to one already in the profile says nothing new;
#: repeating the same command must not push older, more varied takes out.
DUPLICATE_SAMPLE = 0.995

#: Samples added from a satellite without the person present (``source`` "auto",
#: approved by an admin) are kept apart from enrolled ones: they have their own
#: cap and never push an enrolled take out.
AUTO_SOURCE = "auto"
MAX_AUTO_SAMPLES = 6

# --- Calibration (``calibrate``) -------------------------------------------------
#: The threshold calibration may choose is kept inside these bounds. The floor
#: protects people who have not enrolled (their voices are not in the impostor
#: scores); the ceiling keeps a household of very distinct voices usable.
CALIBRATED_MIN_THRESHOLD = 0.72
CALIBRATED_MAX_THRESHOLD = 0.90
#: How far above the highest other-speaker score the threshold sits.
IMPOSTOR_SAFETY = 0.03
#: With only one person enrolled there are no other-speaker scores: the threshold
#: then sits this far under that person's lowest same-speaker score.
SINGLE_PROFILE_SLACK = 0.08
MIN_MARGIN = MATCH_MARGIN
MAX_MARGIN = 0.10
#: A child taken for an adult (or the reverse) is the mistake that matters most,
#: so when the two best candidates are in different age groups the lead and the
#: score both have to be clearly larger.
CROSS_AGE_MARGIN_FACTOR = 2.0
CROSS_AGE_SCORE_LIFT = 0.03
CHILD_ROLES = frozenset({"child", "teen"})

# --- Candidate samples (``candidate_gate``) --------------------------------------
#: A command heard at a satellite assigned to one person, not confident enough to
#: identify them, may become a candidate sample for that person when it scores at
#: least this against their profile and had this much speech.
CANDIDATE_MIN_SCORE = 0.55
CANDIDATE_MIN_SPEECH_SECONDS = 1.5
#: Candidates waiting for an admin, per person; the oldest goes when full.
MAX_CANDIDATES = 12


def _norm(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(_norm(a), _norm(b), strict=True))


def valid_embedding(v: object) -> list[float]:
    """The embedding as floats, or ValueError naming what is wrong with it."""
    if not isinstance(v, list) or len(v) != DIMENSIONS:
        raise ValueError(f"a voice signature has {DIMENSIONS} numbers")
    try:
        out = [float(x) for x in v]
    except (TypeError, ValueError):
        raise ValueError("a voice signature is a list of numbers") from None
    if not all(math.isfinite(x) for x in out) or not any(out):
        raise ValueError("the voice signature is empty or not finite")
    return out


def load_samples(raw: str | None) -> list[dict]:
    try:
        samples = json.loads(raw or "[]")
    except ValueError:
        return []
    return [s for s in samples if isinstance(s, dict) and isinstance(s.get("embedding"), list)]


def centroid(samples: list[dict]) -> list[float]:
    vectors = [_norm(s["embedding"]) for s in samples]
    return _norm([sum(col) / len(vectors) for col in zip(*vectors, strict=True)])


def fingerprint(samples: list[dict]) -> str:
    """A short, stable label for a profile: changes whenever its voice does."""
    digest = hashlib.sha256(json.dumps([round(x, 4) for x in centroid(samples)]).encode()).hexdigest()
    return f"ge2e:{digest[:12]}"


def consistency(samples: list[dict]) -> float | None:
    """How alike a person's own takes are: the lowest take-to-profile score."""
    if len(samples) < 2:
        return None
    c = centroid(samples)
    return round(min(cosine(s["embedding"], c) for s in samples), 3)


def trim(samples: list[dict]) -> list[dict]:
    """The samples a profile keeps: the newest ``MAX_SAMPLES`` enrolled ones and,
    apart from them, the newest ``MAX_AUTO_SAMPLES`` auto-added ones, so an
    auto-added sample never pushes out one the person recorded."""
    enrolled = [s for s in samples if s.get("source") != AUTO_SOURCE][-MAX_SAMPLES:]
    auto = [s for s in samples if s.get("source") == AUTO_SOURCE][-MAX_AUTO_SAMPLES:]
    keep = {id(s) for s in enrolled + auto}
    return [s for s in samples if id(s) in keep]


def age_group(role: str | None) -> str:
    return "child" if (role or "") in CHILD_ROLES else "adult"


def rank(embedding: list[float], profiles: list[dict]) -> list[dict]:
    """``profiles`` are ``{username, name, samples, role?}``; best match first."""
    scored = [
        {
            "username": p["username"],
            "name": p["name"],
            "score": round(cosine(embedding, centroid(p["samples"])), 3),
            "group": age_group(p.get("role")),
        }
        for p in profiles
        if len(p["samples"]) >= MIN_SAMPLES
    ]
    return sorted(scored, key=lambda c: c["score"], reverse=True)


def decide(candidates: list[dict], threshold: float | None = None, margin_needed: float | None = None) -> dict:
    """Who it is, if anyone: the best candidate only when it clears the
    threshold and leads the runner-up by the margin.

    When the two best are a child and an adult the bar is higher
    (``CROSS_AGE_*``): a weak score says nothing rather than risk naming a
    child as a parent or a parent as a child.
    """
    floor = MATCH_THRESHOLD if threshold is None else threshold
    lead = MATCH_MARGIN if margin_needed is None else margin_needed
    best = candidates[0] if candidates else None
    runner = candidates[1] if len(candidates) > 1 else None
    margin = round(best["score"] - runner["score"], 3) if best and runner else None
    cross_age = bool(best and runner and best.get("group") and runner.get("group")
                     and best["group"] != runner["group"])
    need_score = floor + (CROSS_AGE_SCORE_LIFT if cross_age else 0.0)
    need_margin = lead * (CROSS_AGE_MARGIN_FACTOR if cross_age else 1.0)
    matched = bool(best and best["score"] >= need_score and (margin is None or margin >= need_margin))
    return {
        "matched": matched,
        "matched_username": best["username"] if matched else None,
        "matched_name": best["name"] if matched else None,
        "score": best["score"] if best else None,
        "threshold": round(need_score, 3),
        "margin": margin,
        "margin_needed": round(need_margin, 3) if runner else None,
        "cross_age": cross_age,
        "candidates": candidates,
    }


def _quantile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    pos = (len(ordered) - 1) * q
    lo = math.floor(pos)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def _spread(values: list[float]) -> dict | None:
    if not values:
        return None
    return {
        "n": len(values),
        "min": round(min(values), 3),
        "p10": round(_quantile(values, 0.10), 3),
        "median": round(_quantile(values, 0.5), 3),
        "max": round(max(values), 3),
    }


def profiles_key(profiles: list[dict]) -> str:
    """Changes whenever any usable profile does: what a stored calibration was made from."""
    usable = sorted(
        (p["username"], fingerprint(p["samples"]), age_group(p.get("role")))
        for p in profiles if len(p["samples"]) >= MIN_SAMPLES
    )
    return hashlib.sha256(json.dumps(usable).encode()).hexdigest()[:16]


def calibrate(profiles: list[dict]) -> dict:
    """The threshold and margin that separate this household's voices.

    Every enrolled sample is scored against its own person's other samples
    (same-speaker, leave one out) and against every other person's profile
    (other-speaker). The threshold sits just above the highest other-speaker
    score, so nobody enrolled is taken for someone else, within
    ``CALIBRATED_MIN/MAX_THRESHOLD``; the margin is half the typical lead a
    sample has over its closest other person. With one person enrolled there is
    nothing to separate, so the threshold only stays under their own scores.

    ``status`` says how much to trust it: ``default`` (nothing enrolled),
    ``single`` (one person), ``separated`` (other-speaker scores all sit below
    the threshold and same-speaker scores mostly above), ``overlap`` (the two
    overlap: some people will not be recognised until they add recordings).
    Scores only; no signature is ever part of the result.
    """
    usable = [p for p in profiles if len(p["samples"]) >= MIN_SAMPLES]
    same: list[float] = []
    other: list[float] = []
    leads: list[float] = []
    per_person = []
    centroids = {p["username"]: centroid(p["samples"]) for p in usable}
    for p in usable:
        own_scores = []
        for i, sample in enumerate(p["samples"]):
            rest = p["samples"][:i] + p["samples"][i + 1:]
            own = cosine(sample["embedding"], centroid(rest))
            own_scores.append(own)
            others = [cosine(sample["embedding"], c) for u, c in centroids.items() if u != p["username"]]
            other.extend(others)
            if others:
                leads.append(own - max(others))
        same.extend(own_scores)
        per_person.append({
            "username": p["username"],
            "samples": len(p["samples"]),
            "same_min": round(min(own_scores), 3),
            "group": age_group(p.get("role")),
        })
    result = {
        "people": len(usable),
        "per_person": per_person,
        "same_speaker": _spread(same),
        "other_speaker": _spread(other),
        "key": profiles_key(profiles),
    }
    if not usable:
        return {**result, "status": "default", "threshold": MATCH_THRESHOLD, "margin": MATCH_MARGIN}
    clamp = lambda x: min(CALIBRATED_MAX_THRESHOLD, max(CALIBRATED_MIN_THRESHOLD, x))  # noqa: E731
    if not other:
        threshold = clamp(min(same) - SINGLE_PROFILE_SLACK)
        return {**result, "status": "single", "threshold": round(threshold, 3), "margin": MATCH_MARGIN}
    threshold = clamp(max(other) + IMPOSTOR_SAFETY)
    margin = min(MAX_MARGIN, max(MIN_MARGIN, _quantile(leads, 0.5) / 2)) if leads else MATCH_MARGIN
    separated = max(other) < threshold and _quantile(same, 0.10) >= threshold
    return {
        **result,
        "status": "separated" if separated else "overlap",
        "threshold": round(threshold, 3),
        "margin": round(margin, 3),
    }


def candidate_gate(
    embedding: list[float], username: str, profiles: list[dict], speech_seconds: float | None
) -> tuple[bool, str, list[dict]]:
    """Whether a command heard at ``username``'s own satellite may be offered to
    an admin as a sample of their voice: (ok, why not, ranking).

    It must have enough speech, the person must be enrolled, it must score at
    least ``CANDIDATE_MIN_SCORE`` against their profile, and nobody else may
    score as high. It is only ever a candidate: an admin approves it first.
    """
    if not isinstance(speech_seconds, (int, float)) or speech_seconds < CANDIDATE_MIN_SPEECH_SECONDS:
        return False, "too little speech", []
    ranked = rank(embedding, profiles)
    mine = next((c for c in ranked if c["username"] == username), None)
    if mine is None:
        return False, "not enrolled", ranked
    if mine["score"] < CANDIDATE_MIN_SCORE:
        return False, "not close to their profile", ranked
    if ranked[0]["username"] != username:
        return False, "closer to someone else", ranked
    return True, "", ranked

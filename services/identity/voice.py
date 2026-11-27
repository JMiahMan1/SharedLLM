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
#: Windows kept per take: one every 1.6 s, which is the encoder's own slice.
MAX_PARTIALS = 8
#: More takes stop moving the mean; keeping the newest few lets a profile
#: follow a voice that changes (a child's, or a new microphone).
MAX_SAMPLES = 6
MATCH_THRESHOLD = 0.80
MATCH_MARGIN = 0.04
#: How far the best candidate must stand out from the mean of the household's
#: other profiles when its absolute lead is too small to trust. Measured on the
#: enrolled takes, every own take stood at least +0.04 clear of that mean and
#: every other-speaker take below +0.02, so a score can be low and still be one
#: voice by a wide margin where the absolute lead has shrunk away.
REL_MARGIN = 0.03
#: The relative route still refuses a weak absolute score: an unenrolled voice
#: can lead a quiet cohort without being anybody in it.
RELATIVE_SCORE_SLACK = 0.07
#: The relative threshold sits just above the highest impostor standing, inside
#: these bounds.
MIN_REL_MARGIN = 0.02
MAX_REL_MARGIN = 0.15
#: Below this many cross-person scores, use the worst one as the floor: a
#: quantile over a handful of numbers is not a distribution.
CALIBRATION_TAIL = 8

#: Bumped whenever scoring or the thresholds change. A calibration stored by an
#: older version is stale even though the enrolled samples are unchanged, and
#: serving it kept the pre-0.72 floor long after the code that made it changed.
CALIBRATION_VERSION = 2
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


def valid_partials(raw: object) -> list[list[float]]:
    """The usable window vectors from a request or a stored take (capped)."""
    out: list[list[float]] = []
    if not isinstance(raw, list):
        return out
    for w in raw[:MAX_PARTIALS]:
        try:
            out.append(valid_embedding(w))
        except ValueError:
            continue
    return out


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


def _windows(sample: dict) -> list[list[float]]:
    """The per-window vectors of one take, when the recording kept them."""
    out = []
    for w in sample.get("partials") or []:
        try:
            out.append(valid_embedding(w))
        except ValueError:
            continue
    return out


def _agreed_window(probe: list[list[float]], stored: list[list[float]]) -> float | None:
    """How close the probe's windows sit, counting only windows that agree.

    A single window is a 1.6 s glimpse of a room, so it can resemble a stranger by
    luck. Taking the best two windows and averaging them keeps that from being
    enough on its own: the same luck would have to happen twice.
    """
    if not probe or not stored:
        return None
    best = sorted((max(cosine(w, v) for v in stored) for w in probe), reverse=True)
    if len(best) >= 2:
        return (best[0] + best[1]) / 2
    return best[0]


def rank(embedding: list[float], profiles: list[dict], partials: list[list[float]] | None = None) -> list[dict]:
    """``profiles`` are ``{username, name, samples, role?}``; best match first.

    A profile is scored by its centroid, by its closest single take, and - when the
    recordings kept their windows - by the best two agreeing windows, the highest of
    the three counting. A take recorded on a good microphone can sit much closer to
    the voice than the mean of five takes does, and a short or quiet command is
    mostly one or two windows, which the mean washes out. That combination is what
    made quiet commands score just under the line.
    """
    probe = [embedding]
    for w in partials or []:
        try:
            probe.append(valid_embedding(w))
        except ValueError:
            continue
    scored = []
    for p in profiles:
        if len(p["samples"]) < MIN_SAMPLES:
            continue
        centroid_score = cosine(embedding, centroid(p["samples"]))
        take_score = max(cosine(embedding, s["embedding"]) for s in p["samples"])
        stored = [centroid(p["samples"])]
        for s in p["samples"]:
            stored.append(s["embedding"])
            stored.extend(_windows(s))
        window_score = _agreed_window(probe[1:], stored) if len(probe) > 1 else None
        best = max(centroid_score, take_score, window_score or 0.0)
        scored.append(
            {
                "username": p["username"],
                "name": p["name"],
                "score": round(best, 3),
                "centroid_score": round(centroid_score, 3),
                "take_score": round(take_score, 3),
                "window_score": round(window_score, 3) if window_score is not None else None,
                "group": age_group(p.get("role")),
            }
        )
    return sorted(scored, key=lambda c: c["score"], reverse=True)


def decide(
    candidates: list[dict],
    threshold: float | None = None,
    margin_needed: float | None = None,
    relative_needed: float | None = None,
) -> dict:
    """Who it is, if anyone: the best candidate only when it clears the
    threshold and leads the runner-up by the margin - or when it stands clearly
    out from the household's other profiles (``REL_MARGIN``).

    The second route exists because a quiet take scores low against everyone:
    the leader is still the leader, yet its lead over the runner-up shrinks with
    every score and falls under a fixed margin. Measured on the enrolled takes,
    the lead over the mean of the other profiles separates own from other takes
    perfectly (every own take above +0.04, every other take below +0.02) where
    the absolute score overlaps. It never accepts a weak score:
    ``RELATIVE_SCORE_SLACK`` keeps it just under the calibrated threshold.

    When the two best are a child and an adult the score must clear a slightly
    higher bar (``CROSS_AGE_SCORE_LIFT``) and lead by at least ``MAX_MARGIN``
    rather than the calibrated margin, and the cohort route asks for double the
    relative lead. It used to ask for double the absolute margin too, which
    rejected a parent whose own child was simply the runner-up: in a house with
    one adult and two children, every request is cross-age and a 0.18 lead is
    not an ambiguity.
    """
    floor = MATCH_THRESHOLD if threshold is None else threshold
    lead = MATCH_MARGIN if margin_needed is None else margin_needed
    rel_lead = REL_MARGIN if relative_needed is None else relative_needed
    best = candidates[0] if candidates else None
    runner = candidates[1] if len(candidates) > 1 else None
    margin = round(best["score"] - runner["score"], 3) if best and runner else None
    cohort = [c["score"] for c in candidates[1:]] if best else []
    relative = round(best["score"] - sum(cohort) / len(cohort), 3) if cohort else None
    cross_age = bool(best and runner and best.get("group") and runner.get("group")
                     and best["group"] != runner["group"])
    need_score = floor + (CROSS_AGE_SCORE_LIFT if cross_age else 0.0)
    need_margin = max(lead, MAX_MARGIN) if cross_age else lead
    need_relative = rel_lead * (CROSS_AGE_MARGIN_FACTOR if cross_age else 1.0)
    relative_floor = round(max(0.0, need_score - RELATIVE_SCORE_SLACK), 3)
    certain = bool(best and best["score"] >= need_score and (margin is None or margin >= need_margin))
    # Standing out from the household still has to beat the runner-up by the
    # margin the calibrated data asked for, so a near-tie is never settled by
    # the cohort alone - naming the wrong person is worse than saying nothing.
    stands_out = bool(best and relative is not None and relative >= need_relative
                      and best["score"] >= relative_floor
                      and (margin is None or margin >= need_margin))
    matched = certain or stands_out
    return {
        "matched": matched,
        "matched_username": best["username"] if matched else None,
        "matched_name": best["name"] if matched else None,
        "score": best["score"] if best else None,
        "threshold": round(need_score, 3),
        "margin": margin,
        "margin_needed": round(need_margin, 3) if runner else None,
        "relative": relative,
        "relative_needed": round(need_relative, 3) if relative is not None else None,
        "relative_floor": relative_floor if relative is not None else None,
        "decided_by": ("score" if certain else "cohort") if matched else None,
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
    rel_same: list[float] = []
    rel_other: list[float] = []
    per_person = []
    centroids = {p["username"]: centroid(p["samples"]) for p in usable}
    for p in usable:
        own_scores = []
        for i, sample in enumerate(p["samples"]):
            rest = p["samples"][:i] + p["samples"][i + 1:]
            own = cosine(sample["embedding"], centroid(rest))
            own_scores.append(own)
            scores = {u: cosine(sample["embedding"], c) for u, c in centroids.items()}
            others = [s for u, s in scores.items() if u != p["username"]]
            other.extend(others)
            if others:
                leads.append(own - max(others))
                rel_same.append(own - sum(others) / len(others))
                # How far every other person stands out when this take is scored
                # against them: the highest of those is what a stranger could
                # reach by luck, and the relative threshold sits just above it.
                for u, score in scores.items():
                    if u == p["username"]:
                        continue
                    rest_scores = [s for o, s in scores.items() if o != u]
                    if rest_scores:
                        rel_other.append(score - sum(rest_scores) / len(rest_scores))
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
        "relative_speaker": _spread(rel_same),
        "relative_impostor": _spread(rel_other),
        "key": profiles_key(profiles),
    }
    relative = (
        min(MAX_REL_MARGIN, max(MIN_REL_MARGIN, max(rel_other) + IMPOSTOR_SAFETY / 3))
        if rel_other
        else REL_MARGIN
    )
    if not usable:
        return {
            **result,
            "status": "default",
            "threshold": MATCH_THRESHOLD,
            "margin": MATCH_MARGIN,
            "relative": REL_MARGIN,
            "version": CALIBRATION_VERSION,
        }
    clamp = lambda x: min(CALIBRATED_MAX_THRESHOLD, max(CALIBRATED_MIN_THRESHOLD, x))  # noqa: E731
    if not other:
        threshold = clamp(min(same) - SINGLE_PROFILE_SLACK)
        return {
            **result,
            "status": "single",
            "threshold": round(threshold, 3),
            "margin": MATCH_MARGIN,
            "relative": REL_MARGIN,
            "version": CALIBRATION_VERSION,
        }
    # The worst other-speaker score is one unlucky take, and holding every
    # recording above it rejects legitimate ones. A high quantile is the score a
    # stranger actually reaches; the max is kept as the floor of last resort.
    base = max(other) if len(other) < CALIBRATION_TAIL else _quantile(other, 0.90)
    threshold = clamp(base + IMPOSTOR_SAFETY)
    margin = min(MAX_MARGIN, max(MIN_MARGIN, _quantile(leads, 0.5) / 2)) if leads else MATCH_MARGIN
    separated = _quantile(other, 0.95) < threshold and _quantile(same, 0.10) >= threshold
    return {
        **result,
        "status": "separated" if separated else "overlap",
        "threshold": round(threshold, 3),
        "margin": round(margin, 3),
        "relative": round(relative, 3),
        "version": CALIBRATION_VERSION,
    }


def calibration_is_current(stored: dict | None, key: str) -> bool:
    """Whether a stored calibration can still be served: same enrolled samples
    and the same scoring, so a change of algorithm re-calibrates on first use."""
    return bool(
        isinstance(stored, dict)
        and stored.get("key") == key
        and stored.get("version") == CALIBRATION_VERSION
    )


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


# --- New voices (``new_voice_gate``, ``services/identity/new_voices.py``) ----------
#: A command is a possible new voice only when it is far from every profile: under
#: this score (``CANDIDATE_MIN_SCORE``, so it can never also be a candidate sample of
#: someone) and ``NEW_VOICE_GAP`` under the threshold, with enrolment's minimum of
#: speech. The same rule runs in execution (``services/common/voice_speaker.looks_new``);
#: identity checks it again against its own profiles before counting anything.
NEW_VOICE_MAX_SCORE = CANDIDATE_MIN_SCORE
NEW_VOICE_GAP = 0.15
NEW_VOICE_MIN_SPEECH_SECONDS = 2.0


def new_voice_gate(
    embedding: list[float], profiles: list[dict], threshold: float, speech_seconds: float | None
) -> tuple[bool, str, float | None]:
    """Whether a command sounds like nobody enrolled: (ok, why not, best score).

    Nobody enrolled is not a new voice either: there is nothing to tell it apart from.
    """
    if not isinstance(speech_seconds, (int, float)) or speech_seconds < NEW_VOICE_MIN_SPEECH_SECONDS:
        return False, "too little speech", None
    ranked = rank(embedding, profiles)
    if not ranked:
        return False, "nobody enrolled", None
    best = ranked[0]["score"]
    if best >= NEW_VOICE_MAX_SCORE or best > threshold - NEW_VOICE_GAP:
        return False, "close to someone enrolled", best
    return True, "", best


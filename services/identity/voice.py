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


def rank(embedding: list[float], profiles: list[dict]) -> list[dict]:
    """``profiles`` are ``{username, name, samples}``; best match first."""
    scored = [
        {"username": p["username"], "name": p["name"], "score": round(cosine(embedding, centroid(p["samples"])), 3)}
        for p in profiles
        if len(p["samples"]) >= MIN_SAMPLES
    ]
    return sorted(scored, key=lambda c: c["score"], reverse=True)


def decide(candidates: list[dict], threshold: float | None = None) -> dict:
    """Who it is, if anyone: the best candidate only when it clears the
    threshold and leads the runner-up by ``MATCH_MARGIN``."""
    floor = MATCH_THRESHOLD if threshold is None else threshold
    best = candidates[0] if candidates else None
    margin = round(best["score"] - candidates[1]["score"], 3) if len(candidates) > 1 else None
    matched = bool(best and best["score"] >= floor and (margin is None or margin >= MATCH_MARGIN))
    return {
        "matched": matched,
        "matched_username": best["username"] if matched else None,
        "matched_name": best["name"] if matched else None,
        "score": best["score"] if best else None,
        "threshold": floor,
        "margin": margin,
        "candidates": candidates,
    }

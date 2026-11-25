"""Voices nobody has enrolled, heard at the satellites, kept as anonymous clusters for a week.

When a command sounds like nobody enrolled (``voice.new_voice_gate``), Jarvis
answers it as usual and may add one friendly line inviting the speaker to say
"learn my voice". So that the same person is not asked every time, and so an
admin can see that someone new keeps talking to a satellite, each satellite
keeps a few clusters in Redis: the normalised mean of the commands that sounded
alike (``SAME_CLUSTER``), how many times it was heard in the last week, and
when it was last asked.

A cluster is not a profile and never becomes one: it has no name, it cannot
identify anyone, it is never used to answer as anybody, and it expires a week
after it was last heard (``TTL_S``). A voice profile is only ever made when the
person says "learn my voice" at a satellite (the teaching session) or an admin
approves samples. The admin sees counts only: "a new voice talked to the Office
Echo 5 times this week".
"""
from __future__ import annotations

import json
import logging
import secrets
import time
from typing import Any

from services.identity import voice

log = logging.getLogger("identity.new_voices")

KEY_PREFIX = "voice:newvoice:clusters:"
#: A cluster, and every visit it counts, lasts a week after the voice was last heard.
TTL_S = 7 * 24 * 3600
WEEK_S = 7 * 24 * 3600
#: The invitation is said at most once a day per voice and satellite (children's voices
#: change; nobody should be nagged).
ASK_EVERY_S = 24 * 3600
#: A new command joins an earlier unknown voice at the same satellite when it scores at least
#: this against that voice's mean. Identity's ``CONSISTENCY_FLOOR`` (0.65) is where a take is
#: "almost certainly someone else"; joining just above it errs towards one cluster for two
#: similar voices (asked once between them) rather than two for one person (asked twice).
SAME_CLUSTER = 0.70
#: Clusters kept per satellite; the one heard longest ago goes first.
MAX_CLUSTERS = 8
#: A cluster's mean stops moving much after this many commands.
MEAN_WEIGHT_CAP = 10
#: Visits in a week before the admin page mentions a new voice.
SUGGEST_VISITS = 3


def _key(device: str) -> str:
    return KEY_PREFIX + device


def _load(redis: Any, device: str) -> list[dict]:
    raw = redis.get(_key(device))
    try:
        doc = json.loads(raw) if raw else []
    except (TypeError, ValueError):
        return []
    return [c for c in doc if isinstance(c, dict) and isinstance(c.get("centroid"), list)] if isinstance(doc, list) else []


def _save(redis: Any, device: str, clusters: list[dict]) -> None:
    if clusters:
        redis.set(_key(device), json.dumps(clusters), ex=TTL_S)
    else:
        redis.delete(_key(device))


def _recent(visits: Any, now: float) -> list[float]:
    return [float(t) for t in visits or [] if isinstance(t, (int, float)) and now - float(t) <= WEEK_S]


def heard(redis: Any, device: str, label: str | None, embedding: list[float], now: float | None = None) -> dict:
    """Count one command of an unknown voice at ``device``; returns whether to invite it.

    ``{"cluster": id, "visits_week": n, "ask": bool, "new": bool}``. ``ask`` is true at most once
    per ``ASK_EVERY_S`` per cluster. No signature is ever returned.
    """
    now = time.time() if now is None else now
    clusters = []
    for c in _load(redis, device):
        c["visits"] = _recent(c.get("visits"), now)
        if c["visits"]:
            clusters.append(c)
    best, best_score = None, -1.0
    for c in clusters:
        try:
            score = voice.cosine(embedding, c["centroid"])
        except (ValueError, TypeError):
            continue
        if score > best_score:
            best, best_score = c, score
    fresh = best is None or best_score < SAME_CLUSTER
    if fresh:
        best = {"id": secrets.token_hex(4), "centroid": voice._norm(embedding), "n": 1, "visits": [],
                "asked_at": None, "first_heard": now}
        clusters.append(best)
    else:
        weight = min(int(best.get("n") or 1), MEAN_WEIGHT_CAP)
        old = voice._norm(best["centroid"])
        new = voice._norm(embedding)
        best["centroid"] = [round(x, 6) for x in voice._norm(
            [(weight * a + b) / (weight + 1) for a, b in zip(old, new, strict=True)])]
        best["n"] = int(best.get("n") or 1) + 1
    best["visits"].append(now)
    best["label"] = (label or "")[:120] or best.get("label")
    asked_at = best.get("asked_at")
    ask = not isinstance(asked_at, (int, float)) or now - float(asked_at) >= ASK_EVERY_S
    if ask:
        best["asked_at"] = now
    clusters.sort(key=lambda c: max(c["visits"]), reverse=True)
    _save(redis, device, clusters[:MAX_CLUSTERS])
    return {"cluster": best["id"], "visits_week": len(best["visits"]), "ask": ask, "new": fresh}


def overview(redis: Any, now: float | None = None) -> list[dict]:
    """Unknown voices heard ``SUGGEST_VISITS`` times or more this week, per satellite: counts only."""
    now = time.time() if now is None else now
    out = []
    for key in redis.scan_iter(match=KEY_PREFIX + "*", count=100):
        device = key[len(KEY_PREFIX):] if isinstance(key, str) else key.decode()[len(KEY_PREFIX):]
        for c in _load(redis, device):
            visits = _recent(c.get("visits"), now)
            if len(visits) >= SUGGEST_VISITS:
                out.append({"id": c.get("id"), "device": device, "label": c.get("label") or None,
                            "visits_week": len(visits), "last_heard": max(visits)})
    return sorted(out, key=lambda c: (-c["visits_week"], -c["last_heard"]))


def forget(redis: Any, device: str, cluster_id: str) -> bool:
    """Drop one unknown voice (an admin dismissed it). Returns whether it was there."""
    clusters = _load(redis, device)
    kept = [c for c in clusters if c.get("id") != cluster_id]
    if len(kept) == len(clusters):
        return False
    _save(redis, device, kept)
    return True


__all__ = ["ASK_EVERY_S", "SAME_CLUSTER", "SUGGEST_VISITS", "TTL_S", "forget", "heard", "overview"]

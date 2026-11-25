"""Family phones near each other, told by Bluetooth LE with no network.

Each Jarvis phone advertises a short-lived identifier derived from a
household key, and the family's phones resolve it to a person on the phone
itself; strangers see only random bytes that change every period. A phone
estimates distance from signal strength as a band (next to you / same room /
nearby), never in metres, and uploads what it saw when it is online:

    {"person": "<username>", "band": "same_room", "first_seen": t0,
     "last_seen": t1, "samples": 12, "rssi": -71}

Geo keeps those per observer (``geo:proximity:<observer>``) and uses them for:

* **Travelling together**: two family members' trips that overlap in time,
  with their phones near each other (next to you or same room) for a good
  part of the overlap, or both trips detected in the same linked vehicle
  (vehicle_bluetooth). The trip then says "with <name> in the <vehicle>".
* **Who is near whom now**, for the family cards.

Visibility follows the family's existing location sharing (``_viewer_may_see``
in main): nothing new to switch on or off. A paused person is neither
recorded as seen nor recorded seeing anyone.

The household key is made here once (32 random bytes) and handed to signed-in
family members' phones over the authenticated API; rotating it (an admin,
e.g. after a phone is lost) makes every old identifier meaningless.
"""
from __future__ import annotations

import base64
import hashlib
import re
import secrets
from typing import Any

BANDS = ("next_to", "same_room", "nearby")
BAND_RANK = {b: i for i, b in enumerate(BANDS)}
#: Bands that count as "with" someone (in the same car, at the same table).
CLOSE_BANDS = frozenset({"next_to", "same_room"})
#: How long one advertised identifier lives (seconds).
PERIOD_S = 900
#: How long sightings are kept.
RETENTION_S = 30 * 86400
#: Most sightings taken from one upload.
MAX_EVENTS = 500
#: A sighting reported this far in the future is a clock fault, dropped.
FUTURE_SLACK_S = 300
#: "Near now" means seen within this long.
NEAR_NOW_S = 600

#: Two trips overlap enough to be one journey when they share this share of the shorter one.
OVERLAP_SHARE = 0.4
#: Close sightings must cover this share of the overlap (or CLOSE_MIN_S, whichever is less).
CLOSE_SHARE = 0.3
CLOSE_MIN_S = 300.0
#: A sighting window stands for a little time either side of it (phones scan in bursts).
SIGHTING_PAD_S = 30.0

_USER = re.compile(r"[a-z0-9_.\-]{1,64}")

KEY_HASH = "geo:proximity:keys"


def new_key() -> str:
    return base64.b64encode(secrets.token_bytes(32)).decode()


def key_id(key_b64: str) -> str:
    """A short public name for a key (so a phone can tell it holds the current one)."""
    return hashlib.sha256(base64.b64decode(key_b64)).hexdigest()[:12]


def _user(value: Any) -> str | None:
    text = str(value or "").split(".")[-1].strip().lower()
    return text if _USER.fullmatch(text) else None


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if n == n else None  # NaN


def clean_event(raw: Any, observer: str, now: float) -> dict | None:
    """One sighting as kept, or None for anything malformed."""
    if not isinstance(raw, dict):
        return None
    person = _user(raw.get("person"))
    band = str(raw.get("band") or "")
    first, last = _num(raw.get("first_seen")), _num(raw.get("last_seen"))
    if not person or person == observer or band not in BANDS or first is None or last is None:
        return None
    if last < first or last > now + FUTURE_SLACK_S or last < now - RETENTION_S or last - first > 6 * 3600:
        return None
    samples = _num(raw.get("samples"))
    event = {
        "observer": observer,
        "person": person,
        "band": band,
        "first_seen": round(first, 1),
        "last_seen": round(last, 1),
        "samples": int(max(1, min(10000, samples or 1))),
    }
    rssi = _num(raw.get("rssi"))
    if rssi is not None and -127 <= rssi <= 20:
        event["rssi"] = int(rssi)
    return event


def _interval(trip: dict) -> tuple[float, float]:
    start = float(trip.get("start_time") or 0)
    end = float(trip.get("end_time") or trip.get("last_moving_time") or trip.get("last_point_time") or start)
    return start, max(start, end)


def overlap(t1: dict, t2: dict) -> tuple[float, float] | None:
    """The shared stretch of two trips, when it is a real share of the shorter one."""
    s1, e1 = _interval(t1)
    s2, e2 = _interval(t2)
    lo, hi = max(s1, s2), min(e1, e2)
    if hi - lo < 60:
        return None
    shorter = max(60.0, min(e1 - s1, e2 - s2))
    return (lo, hi) if (hi - lo) / shorter >= OVERLAP_SHARE else None


def _covered(intervals: list[tuple[float, float]], lo: float, hi: float) -> float:
    """Seconds of [lo, hi] covered by the union of ``intervals``."""
    clipped = sorted((max(lo, a), min(hi, b)) for a, b in intervals if b > lo and a < hi)
    total, cur_a, cur_b = 0.0, None, None
    for a, b in clipped:
        if cur_b is None or a > cur_b:
            if cur_b is not None:
                total += cur_b - cur_a
            cur_a, cur_b = a, b
        else:
            cur_b = max(cur_b, b)
    if cur_b is not None:
        total += cur_b - cur_a
    return total


def close_seconds(events: list[dict], a: str, b: str, lo: float, hi: float) -> float:
    """How long ``a``'s and ``b``'s phones were close (either one seeing the other) within [lo, hi]."""
    spans = [
        (float(e["first_seen"]) - SIGHTING_PAD_S, float(e["last_seen"]) + SIGHTING_PAD_S)
        for e in events
        if e.get("band") in CLOSE_BANDS and {e.get("observer"), e.get("person")} == {a, b}
    ]
    return _covered(spans, lo, hi)


def same_vehicle(t1: dict, t2: dict) -> str | None:
    """The vehicle both trips were detected in by Bluetooth, if it is the same one."""
    v1 = t1.get("bt_vehicle_id") or (t1.get("vehicle_id") if t1.get("vehicle_source") == "bluetooth" else None)
    v2 = t2.get("bt_vehicle_id") or (t2.get("vehicle_id") if t2.get("vehicle_source") == "bluetooth" else None)
    return v1 if v1 and v1 == v2 else None


def together(t1: dict, t2: dict, events: list[dict]) -> dict | None:
    """Whether two family members' trips were one journey taken together, and how it is known."""
    u1, u2 = str(t1.get("user_id") or ""), str(t2.get("user_id") or "")
    if not u1 or not u2 or u1 == u2:
        return None
    window = overlap(t1, t2)
    if window is None:
        return None
    lo, hi = window
    via: list[str] = []
    vehicle = same_vehicle(t1, t2)
    if vehicle:
        via.append("same_vehicle")
    if close_seconds(events, u1, u2, lo, hi) >= min(CLOSE_MIN_S, CLOSE_SHARE * (hi - lo)):
        via.append("nearby")
    if not via:
        return None
    return {"via": via, "vehicle_id": vehicle}


def annotate_together(trips: list[dict], events: list[dict]) -> list[dict]:
    """Give each trip ``together``: the family members it was taken with, and in which vehicle.

    Only trips in ``trips`` are compared, which are the ones the viewer may
    see, so a trip never names someone whose travel the viewer cannot see."""
    for t in trips:
        t.pop("together", None)
    for i, t1 in enumerate(trips):
        for t2 in trips[i + 1:]:
            found = together(t1, t2, events)
            if not found:
                continue
            vehicle_id = found["vehicle_id"]
            for mine, theirs in ((t1, t2), (t2, t1)):
                vehicle_name = None
                if vehicle_id:
                    vehicle_name = mine.get("vehicle_name") if mine.get("vehicle_id") == vehicle_id else theirs.get("vehicle_name")
                elif mine.get("vehicle_source") == "bluetooth":
                    vehicle_name = mine.get("vehicle_name")
                elif theirs.get("vehicle_source") == "bluetooth":
                    vehicle_name = theirs.get("vehicle_name")
                entry = {
                    "user_id": theirs.get("user_id"),
                    "user_name": theirs.get("user_name") or str(theirs.get("user_id") or "").title(),
                    "trip_id": theirs.get("id"),
                    "via": list(found["via"]),
                    "vehicle_id": vehicle_id,
                    "vehicle_name": vehicle_name,
                }
                existing = mine.setdefault("together", [])
                if not any(x.get("user_id") == entry["user_id"] for x in existing):
                    existing.append(entry)
    return trips


def near_now(events: list[dict], viewer: str, now: float, window_s: float = NEAR_NOW_S) -> list[dict]:
    """Who has been near ``viewer`` lately (either phone seeing the other), the closest band each."""
    best: dict[str, dict] = {}
    for e in events:
        if float(e.get("last_seen") or 0) < now - window_s:
            continue
        if e.get("observer") == viewer:
            other = e.get("person")
        elif e.get("person") == viewer:
            other = e.get("observer")
        else:
            continue
        if not other or other == viewer:
            continue
        cur = best.get(other)
        rank = BAND_RANK.get(str(e.get("band")), 99)
        if cur is None or rank < BAND_RANK[cur["band"]] or (
                rank == BAND_RANK[cur["band"]] and e["last_seen"] > cur["last_seen"]):
            best[other] = {"user_id": other, "band": e["band"], "last_seen": e["last_seen"]}
    return sorted(best.values(), key=lambda x: (BAND_RANK[x["band"]], -x["last_seen"]))

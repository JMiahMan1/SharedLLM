"""Which of a person's devices speaks for where they are.

Anyone can be signed in to Jarvis on more than one device: a phone that goes
everywhere and a tablet that never leaves the kitchen. Both upload locations
as that person, and before uploads named their device the two streams were
mixed: the tablet's fix every ten minutes put a parent "home" while his phone
was 17 km away, and the next phone fix had him "leave" again -- five times in
one hour.

Each upload now says which device sent it (``device_id``: a random id made
once per install). This module keeps, per person, every device's last fix and
when it last really moved, and picks the *lead* device: the one that moved
most recently. Only the lead's fixes move the person -- their position on the
map, home and away, the breadcrumb trail, battery alerts, Home Assistant. A
device parked at home never moves, so it never outranks the phone that went
out with them.

The lead hands over only when it has said nothing for LEAD_STALE_S: a phone
that dies on a trip must not let the tablet at home announce "arrived home".
A new phone takes over as soon as it moves, or once the old one has gone
quiet that long.

Pure functions over a plain dict (stored as JSON by identity), so the rule is
tested without a database.
"""

from __future__ import annotations

import math

#: Moving this far from where a device last settled (beyond its own fix
#: uncertainty) counts as the device really moving.
MOVE_M = 300.0
#: Fixes vaguer than this say nothing about whether a device moved.
MAX_MOVE_ACCURACY_M = 100.0
#: The lead keeps the lead until it has been silent this long.
LEAD_STALE_S = 6 * 3600.0
#: Devices not heard from in this long are forgotten.
FORGET_S = 30 * 86400.0
#: Uploads from app builds that do not name their device.
UNKNOWN_DEVICE = "unknown"


def _distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def device_key(device_id: str | None) -> str:
    """A safe, bounded key for the device (the id is client-made)."""
    clean = "".join(ch for ch in (device_id or "") if ch.isalnum() or ch in "-_")[:64]
    return clean or UNKNOWN_DEVICE


def note_fix(devices: dict, device: str, name: str | None, lat: float, lon: float,
             accuracy: float | None, t: float) -> dict:
    """Record a device's fix in ``devices`` (changed in place, and returned)."""
    d = devices.get(device)
    if d is None:
        d = {"name": name or "", "lat": lat, "lon": lon, "t": t,
             "anchor": [lat, lon], "moved_at": 0.0}
        devices[device] = d
    if name:
        d["name"] = name[:80]
    if t >= float(d.get("t") or 0):
        d.update(lat=lat, lon=lon, t=t)
    acc = float(accuracy or 0.0)
    if acc <= MAX_MOVE_ACCURACY_M:
        alat, alon = d.get("anchor") or [lat, lon]
        if _distance_m(alat, alon, lat, lon) > MOVE_M + acc:
            d["anchor"] = [lat, lon]
            d["moved_at"] = max(float(d.get("moved_at") or 0.0), t)
    for key in [k for k, v in devices.items() if t - float(v.get("t") or 0) > FORGET_S]:
        devices.pop(key, None)
    return devices


def lead(devices: dict, now: float) -> str | None:
    """The device that speaks for the person now."""
    if not devices:
        return None
    best = max(devices, key=lambda k: (float(devices[k].get("moved_at") or 0), float(devices[k].get("t") or 0)))
    if now - float(devices[best].get("t") or 0) <= LEAD_STALE_S:
        return best
    # The lead has gone quiet for hours: the most recent voice takes over.
    return max(devices, key=lambda k: float(devices[k].get("t") or 0))


def accept(devices: dict, device: str, name: str | None, lat: float, lon: float,
           accuracy: float | None, t: float, now: float) -> bool:
    """Note the fix; True when it comes from the lead device (so it counts)."""
    note_fix(devices, device, name, lat, lon, accuracy, t)
    return lead(devices, now) == device

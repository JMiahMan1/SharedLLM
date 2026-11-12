"""Room presence from Bluetooth: iBeacon identities, RSSI smoothing, the room estimate and the room map.

Pure logic, no radios and no hub: ``bluetooth.py`` feeds it advertisements and
publishes what it decides. Everything takes the time as an argument (monotonic
seconds), so the tests drive it with a fake clock.

**Identities.** A tracked device is an iBeacon (Apple's manufacturer data
``0x004C`` with the ``02 15`` prefix: a 16 byte UUID, major, minor and the
measured power at 1 m). Its key is ``<uuid>-<major>-<minor>`` with the UUID in
lower case, which is also how the API names a beacon.

**Readings.** Every scanner that hears a beacon keeps an exponential moving
average of the RSSI (``ema_alpha``) and when it last heard it. A reading older
than ``reading_window_s`` does not count, and nor does a scanner that has heard
nothing at all for ``scanner_online_s`` (it has gone: unplugged, rebooting).

**Two ways to place a beacon in a room:**

- ``nearest_scanner``: the area of the scanner with the strongest calibrated
  RSSI (the scanner's ``rssi_offset`` added). Used while fewer than two rooms
  have been mapped.
- ``fingerprint``: once two or more rooms have been mapped (a person walked
  the beacon around each room while the hub recorded what every scanner
  heard), each room is a per-scanner Gaussian over RSSI plus the probability
  that the scanner hears the beacon at all from there. The live vector is
  scored against every room; a scanner not heard counts as "not heard", a
  scanner that has gone offline is left out of the score entirely. The
  posterior (tempered by the number of scanners, so one scanner cannot make
  it certain) is the confidence. A room that was never mapped can still win
  through its own scanner, but only when that scanner is ``nearest_margin_db``
  stronger than any scanner of another room and at least ``near_rssi`` loud.

**Switching rooms** needs the new room to be the answer for ``dwell_s`` in a
row (hysteresis against a beacon on the edge of two rooms); the first estimate,
and coming back from ``not_home``, apply at once. Nothing heard for
``away_s`` means ``not_home``; never heard means ``unknown``.

**Distance** uses the log-distance path loss model
``d = 10 ** ((P - rssi) / (10 * n))`` with ``P`` the RSSI at 1 m: the
tracker's calibrated ``tx_power`` (registered by the phone or the admin), else
the measured power the beacon advertises, else the scanner's ``ref_power``,
else ``DEFAULT_REF_POWER``.

**Cross-validation** of the map is leave-one-session-out: every recorded
session of a room with at least two sessions is classified, sample by sample,
by a map built without it. A room with one session has no accuracy (``None``).
"""
from __future__ import annotations

import math
import struct
import uuid as uuidlib
from dataclasses import dataclass, field
from typing import Any

APPLE_COMPANY_ID = 0x004C
DEFAULT_REF_POWER = -59
#: A Gaussian's spread never goes below this (dB): two sessions of a steady beacon would otherwise be needles.
MIN_SIGMA_DB = 4.0
#: The mean RSSI assumed for a scanner a room never heard the beacon on.
UNHEARD_MEAN_DB = -100.0
UNHEARD_SIGMA_DB = 8.0
#: Fewer samples than this and a mapping session failed.
MIN_SESSION_SAMPLES = 5


@dataclass
class PresenceParams:
    ema_alpha: float = 0.35
    reading_window_s: float = 20.0
    scanner_online_s: float = 60.0
    dwell_s: float = 12.0
    away_s: float = 180.0
    nearest_margin_db: float = 8.0
    near_rssi: float = -75.0
    path_loss_n: float = 2.0
    publish_min_s: float = 10.0

    def public(self) -> dict[str, float]:
        return dict(self.__dict__)


# -- identities --------------------------------------------------------------------------

@dataclass(frozen=True)
class Beacon:
    uuid: str
    major: int
    minor: int
    tx_power: int | None = None

    @property
    def key(self) -> str:
        return beacon_key(self.uuid, self.major, self.minor)


def beacon_key(uuid: str, major: int, minor: int) -> str:
    return f"{str(uuid).lower()}-{int(major)}-{int(minor)}"


def parse_beacon_key(value: str) -> tuple[str, int, int]:
    """``<uuid>-<major>-<minor>`` -> its parts; ValueError naming what is wrong."""
    parts = str(value or "").strip().rsplit("-", 2)
    if len(parts) != 3:
        raise ValueError(f"{value!r} is not a beacon id (<uuid>-<major>-<minor>)")
    raw_uuid, major, minor = parts
    beacon = beacon_from_dict({"uuid": raw_uuid, "major": major, "minor": minor})
    return beacon.uuid, beacon.major, beacon.minor


def _int_field(data: dict[str, Any], name: str, low: int, high: int, *, optional: bool = False) -> int | None:
    value = data.get(name)
    if value is None and optional:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a whole number, not {value!r}")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a whole number, not {value!r}") from None
    if not number.is_integer():
        raise ValueError(f"{name} must be a whole number, not {value!r}")
    if not low <= number <= high:
        raise ValueError(f"{name} must be between {low} and {high}, not {int(number)}")
    return int(number)


def beacon_from_dict(data: Any) -> Beacon:
    """The ``ble_beacon`` a phone registers (``{uuid, major, minor, tx_power}``), validated; ValueError says why."""
    if not isinstance(data, dict):
        raise ValueError("ble_beacon must be an object with uuid, major, minor and tx_power")
    try:
        parsed = uuidlib.UUID(str(data.get("uuid") or ""))
    except ValueError:
        raise ValueError(f"uuid {data.get('uuid')!r} is not a UUID") from None
    major = _int_field(data, "major", 0, 65535)
    minor = _int_field(data, "minor", 0, 65535)
    tx_power = _int_field(data, "tx_power", -127, 20, optional=True)
    return Beacon(str(parsed), int(major or 0), int(minor or 0), tx_power)


def parse_ibeacon(manufacturer_data: dict[int, bytes]) -> Beacon | None:
    """An iBeacon frame in the advertisement's manufacturer data, with its measured power, or None."""
    raw = manufacturer_data.get(APPLE_COMPANY_ID)
    if not raw or len(raw) < 23 or raw[0] != 0x02 or raw[1] != 0x15:
        return None
    beacon_uuid = str(uuidlib.UUID(bytes=bytes(raw[2:18])))
    major, minor = struct.unpack(">HH", bytes(raw[18:22]))
    power = struct.unpack(">b", bytes(raw[22:23]))[0]
    return Beacon(beacon_uuid, major, minor, power if power < 0 else None)


def ibeacon_bytes(beacon_uuid: str, major: int, minor: int, power: int = DEFAULT_REF_POWER) -> bytes:
    """The manufacturer data an iBeacon advertises (for tests and fakes)."""
    return b"\x02\x15" + uuidlib.UUID(beacon_uuid).bytes + struct.pack(">HHb", major, minor, power)


# -- readings -----------------------------------------------------------------------------

@dataclass
class Reading:
    ema: float
    last: float
    count: int = 1


@dataclass
class Sighting:
    """Everything the scanners heard from one beacon."""

    key: str
    readings: dict[str, Reading] = field(default_factory=dict)
    measured_power: int | None = None
    last_heard: float | None = None

    def hear(self, scanner_id: str, rssi: float, t: float, alpha: float, measured_power: int | None = None) -> None:
        r = self.readings.get(scanner_id)
        if r is None or t - r.last > 60.0:
            self.readings[scanner_id] = Reading(float(rssi), t)
        else:
            r.ema = alpha * float(rssi) + (1 - alpha) * r.ema
            r.last = t
            r.count += 1
        if measured_power is not None:
            self.measured_power = measured_power
        self.last_heard = t if self.last_heard is None else max(self.last_heard, t)

    def current(self, now: float, window_s: float, online: set[str]) -> dict[str, float]:
        """Smoothed RSSI per scanner that heard it within the window and is still online."""
        return {sid: r.ema for sid, r in self.readings.items() if now - r.last <= window_s and sid in online}


def distance_m(rssi: float, ref_power: float, n: float) -> float:
    d = 10 ** ((ref_power - rssi) / (10.0 * n))
    return round(min(max(d, 0.1), 50.0), 1)


# -- the room map -------------------------------------------------------------------------

Sample = dict[str, float | None]


@dataclass
class AreaModel:
    #: scanner -> (p_heard, mean, sigma)
    scanners: dict[str, tuple[float, float, float]]
    samples: int


def build_models(sessions_by_area: dict[str, list[dict[str, Any]]]) -> dict[str, AreaModel]:
    """One Gaussian per (area, scanner) plus how often that scanner heard the beacon there."""
    models: dict[str, AreaModel] = {}
    all_scanners: set[str] = set()
    for sessions in sessions_by_area.values():
        for s in sessions:
            for sample in s.get("samples") or []:
                all_scanners.update(sample)
    for area, sessions in sessions_by_area.items():
        samples = [sample for s in sessions for sample in (s.get("samples") or [])]
        if not samples:
            continue
        per: dict[str, tuple[float, float, float]] = {}
        n = len(samples)
        for sid in all_scanners:
            values = [float(v) for sample in samples if (v := sample.get(sid)) is not None]
            # Laplace smoothing: a scanner always or never heard there is never certain either way.
            p = (len(values) + 1) / (n + 2)
            if values:
                mean = sum(values) / len(values)
                var = sum((v - mean) ** 2 for v in values) / len(values)
                sigma = math.sqrt(var + MIN_SIGMA_DB ** 2)
            else:
                mean, sigma = UNHEARD_MEAN_DB, UNHEARD_SIGMA_DB
            per[sid] = (p, mean, sigma)
        models[area] = AreaModel(per, n)
    return models


def _log_gauss(x: float, mean: float, sigma: float) -> float:
    return -0.5 * ((x - mean) / sigma) ** 2 - math.log(sigma * math.sqrt(2 * math.pi))


def classify(models: dict[str, AreaModel], vector: Sample, usable: set[str] | None = None
             ) -> tuple[str | None, float, dict[str, float]]:
    """``(area, confidence, posterior)`` for one RSSI vector (``None`` = not heard).

    ``usable``: the scanners that are online now; one that has gone is left out of the score (its silence
    says nothing about the room). ``None`` uses every scanner the map or the vector names.
    """
    if not models:
        return None, 0.0, {}
    names = set(vector)
    for m in models.values():
        names.update(m.scanners)
    if usable is not None:
        names &= usable
    if not names or not any(vector.get(s) is not None for s in names):
        return None, 0.0, {}
    scores: dict[str, float] = {}
    for area, model in models.items():
        ll = 0.0
        for sid in names:
            p, mean, sigma = model.scanners.get(sid, (1 / (model.samples + 2), UNHEARD_MEAN_DB, UNHEARD_SIGMA_DB))
            x = vector.get(sid)
            if x is None:
                ll += math.log(1 - p)
            else:
                ll += math.log(p) + _log_gauss(float(x), mean, sigma)
        # Tempered by the number of scanners: the samples are not independent, and an untempered product of
        # Gaussians calls every answer certain.
        scores[area] = ll / max(1, len(names))
    top = max(scores.values())
    weights = {a: math.exp(s - top) for a, s in scores.items()}
    total = sum(weights.values())
    posterior = {a: w / total for a, w in weights.items()}
    best = max(posterior, key=lambda a: (posterior[a], a))
    return best, round(posterior[best], 3), posterior


def cross_validate(sessions_by_area: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """Leave-one-session-out accuracy per area, the confusion matrix and the overall accuracy.

    Only areas with two or more sessions can be checked (the left-out session needs another one of its
    room to be compared with). Overall accuracy needs two such areas; otherwise it is ``None``.
    """
    per_area: dict[str, float | None] = {a: None for a in sessions_by_area}
    confusion: dict[str, dict[str, int]] = {}
    checked = [a for a, sessions in sessions_by_area.items() if len(sessions) >= 2]
    if len([a for a, s in sessions_by_area.items() if s]) < 2:
        return {"per_area": per_area, "confusion": confusion, "overall": None}
    right_total = all_total = 0
    for area in checked:
        right = total = 0
        for i, held in enumerate(sessions_by_area[area]):
            train = {a: [s for j, s in enumerate(ss) if not (a == area and j == i)]
                     for a, ss in sessions_by_area.items()}
            models = build_models(train)
            for sample in held.get("samples") or []:
                guess, _conf, _post = classify(models, sample)
                if guess is None:
                    continue
                confusion.setdefault(area, {}).setdefault(guess, 0)
                confusion[area][guess] += 1
                total += 1
                right += int(guess == area)
        per_area[area] = round(right / total, 3) if total else None
        right_total += right
        all_total += total
    overall = round(right_total / all_total, 3) if all_total and len(checked) >= 2 else None
    return {"per_area": per_area, "confusion": confusion, "overall": overall}


# -- the estimate -------------------------------------------------------------------------

@dataclass
class Candidate:
    area_id: str
    method: str
    confidence: float
    scanner_id: str
    rssi: float


@dataclass
class RoomState:
    """What a tracker's room sensor says, and the switch it may be waiting on."""

    area_id: str | None = None
    status: str = "unknown"          # "room" | "not_home" | "unknown"
    method: str | None = None
    confidence: float = 0.0
    pending: str | None = None
    pending_since: float = 0.0
    scanner_id: str | None = None
    rssi: float | None = None
    scanners_heard: int = 0
    distance_m: float | None = None


def nearest_candidate(readings: dict[str, float], scanner_area: dict[str, str | None],
                      offsets: dict[str, float]) -> Candidate | None:
    """The area of the loudest calibrated scanner; confidence from its lead over the next room's best."""
    adjusted = sorted(((rssi + offsets.get(sid, 0.0), sid) for sid, rssi in readings.items()
                       if scanner_area.get(sid)), reverse=True)
    if not adjusted:
        return None
    best_rssi, best_sid = adjusted[0]
    area = str(scanner_area[best_sid])
    other = next((r for r, sid in adjusted[1:] if scanner_area.get(sid) != area), None)
    margin = best_rssi - other if other is not None else None
    confidence = 0.5 if margin is None else 1 - 0.5 * math.exp(-max(margin, 0.0) / 6.0)
    return Candidate(area, "nearest_scanner", round(confidence, 3), best_sid, best_rssi)


def choose(readings: dict[str, float], scanner_area: dict[str, str | None], offsets: dict[str, float],
           models: dict[str, AreaModel], online: set[str], params: PresenceParams) -> Candidate | None:
    """This moment's answer, before dwell: the fingerprint once two rooms are mapped, else the nearest scanner."""
    nearest = nearest_candidate(readings, scanner_area, offsets)
    if len(models) < 2:
        return nearest
    vector: Sample = {sid: readings.get(sid) for sid in online}
    area, confidence, _post = classify(models, vector, online)
    if nearest is not None and nearest.area_id not in models:
        # A room nobody mapped wins only when its own scanner is clearly the loudest and close.
        others = [rssi + offsets.get(sid, 0.0) for sid, rssi in readings.items()
                  if scanner_area.get(sid) and scanner_area.get(sid) != nearest.area_id]
        lead = nearest.rssi - max(others) if others else float("inf")
        if lead >= params.nearest_margin_db and nearest.rssi >= params.near_rssi:
            return nearest
    if area is None:
        return nearest
    strongest = max(readings, key=lambda s: readings[s] + offsets.get(s, 0.0))
    return Candidate(area, "fingerprint", confidence, strongest, readings[strongest] + offsets.get(strongest, 0.0))


def step(state: RoomState, candidate: Candidate | None, *, now: float, heard: bool, last_heard: float | None,
         params: PresenceParams) -> RoomState:
    """Apply one moment's answer to the room state: dwell before a switch, ``not_home`` after ``away_s``."""
    if last_heard is None:
        return state
    if not heard:
        if now - last_heard >= params.away_s:
            state.status, state.area_id, state.pending, state.method = "not_home", None, None, None
            state.confidence, state.scanners_heard, state.distance_m = 0.0, 0, None
        return state
    if candidate is None:
        return state
    if state.status != "room" or state.area_id is None:
        state.status, state.area_id, state.pending = "room", candidate.area_id, None
    elif candidate.area_id == state.area_id:
        state.pending = None
    elif state.pending != candidate.area_id:
        state.pending, state.pending_since = candidate.area_id, now
    elif now - state.pending_since >= params.dwell_s:
        state.area_id, state.pending = candidate.area_id, None
    state.method = candidate.method
    state.confidence = candidate.confidence if candidate.area_id == state.area_id \
        else round(max(0.0, 1 - candidate.confidence), 3)
    return state

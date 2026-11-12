"""Long-term statistics, with Home Assistant's recorder semantics.

What HA's recorder keeps besides raw state history, and what its energy and
statistics cards read:

- **Short-term statistics**: one row per sensor per 5 minutes, kept
  ``SHORT_TERM_KEEP_DAYS`` (HA keeps 10 days).
- **Hourly statistics**: one row per sensor per hour, kept for good.

Only sensors with a ``state_class`` are compiled, as in HA:

- ``measurement`` (and ``measurement_angle``): time-weighted mean, min and max
  over the period, the state that held at the period's start included.
- ``total`` and ``total_increasing``: ``state`` (the last value), ``sum`` (the
  running total across meter resets) and ``last_reset``. A new cycle is a
  changed ``last_reset`` attribute (``total``) or a drop below 90% of the last
  value (``total_increasing``). This is HA's ``compile_statistics`` line for
  line, with one deliberate difference: a stored ``last_reset`` that lies in
  the future cannot have marked a real cycle (HA's Emporia integration wrote
  such values), so moving away from it does not count as a reset. Without
  that, the first compile after importing HA's data would double-count a
  month of usage.

Hourly rows are compiled from the short-term rows of the hour (mean of the
means, min of the mins, max of the maxes, the last row's sum/state/last_reset),
as HA does. Day, week and month periods are built from hourly rows in the
hub's time zone when they are asked for.

External statistics (``source:object_id``, e.g. a utility's hourly meter
readings) are written by drivers through :meth:`Statistics.add_hourly_amounts`.

Everything lives in its own SQLite file (``statistics.db`` next to the hub's
database): years of hourly rows for hundreds of sensors are millions of rows,
and keeping them apart keeps the registry database small and its lock free.
Compiling runs in a worker thread, never on the event loop.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
import os
import sqlite3
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

log = logging.getLogger("homehub.statistics")

SHORT_TERM_S = 300
HOUR_S = 3600
SHORT_TERM_KEEP_DAYS = int(os.getenv("HOMEHUB_STATS_SHORT_TERM_DAYS", "10"))
#: How far back the first compile (or one after a long outage) reaches into raw history.
CATCHUP_DAYS = int(os.getenv("HOMEHUB_STATS_CATCHUP_DAYS", "10"))
#: Seconds after a period ends before it is compiled, so late writes land first.
COMPILE_DELAY_S = 12.0

STATE_CLASSES_MEAN = {"measurement": 1, "measurement_angle": 2}
STATE_CLASSES_SUM = {"total", "total_increasing"}

ORIGIN_COMPILED, ORIGIN_IMPORTED, ORIGIN_EXTERNAL = 0, 1, 2

ALL_TYPES = frozenset({"change", "last_reset", "max", "mean", "min", "state", "sum"})
PERIODS = ("5minute", "hour", "day", "week", "month")

SCHEMA = """
CREATE TABLE IF NOT EXISTS statistics_meta (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    statistic_id TEXT NOT NULL UNIQUE,
    source TEXT NOT NULL,
    unit_of_measurement TEXT,
    unit_class TEXT,
    has_sum INTEGER NOT NULL DEFAULT 0,
    mean_type INTEGER NOT NULL DEFAULT 0,
    name TEXT
);
CREATE TABLE IF NOT EXISTS statistics (
    metadata_id INTEGER NOT NULL,
    start_ts REAL NOT NULL,
    mean REAL, min REAL, max REAL,
    last_reset_ts REAL, state REAL, sum REAL,
    origin INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (metadata_id, start_ts)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS statistics_short_term (
    metadata_id INTEGER NOT NULL,
    start_ts REAL NOT NULL,
    mean REAL, min REAL, max REAL,
    last_reset_ts REAL, state REAL, sum REAL,
    origin INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (metadata_id, start_ts)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS statistics_start ON statistics(start_ts);
CREATE INDEX IF NOT EXISTS statistics_short_term_start ON statistics_short_term(start_ts);
CREATE TABLE IF NOT EXISTS stats_kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

TABLES = ("statistics", "statistics_short_term")

# -- units ------------------------------------------------------------------
_FACTORS: dict[str, dict[str, float]] = {
    "power": {"mW": 1e-3, "W": 1.0, "kW": 1e3, "MW": 1e6, "GW": 1e9, "TW": 1e12},
    "energy": {"mWh": 1e-3, "Wh": 1.0, "kWh": 1e3, "MWh": 1e6, "GWh": 1e9, "TWh": 1e12,
               "MJ": 1e6 / 3600 * 1, "GJ": 1e9 / 3600},
    "volume": {"L": 1.0, "mL": 1e-3, "m³": 1e3, "ft³": 28.316846592, "CCF": 2831.6846592,
               "gal": 3.785411784},
    "pressure": {"Pa": 1.0, "hPa": 100.0, "kPa": 1e3, "bar": 1e5, "mbar": 100.0, "psi": 6894.757293168,
                 "inHg": 3386.389, "mmHg": 133.322387415},
    "distance": {"m": 1.0, "km": 1e3, "cm": 1e-2, "mm": 1e-3, "mi": 1609.344, "ft": 0.3048, "in": 0.0254,
                 "yd": 0.9144},
    "data_rate": {"bit/s": 1.0, "kbit/s": 1e3, "Mbit/s": 1e6, "Gbit/s": 1e9, "B/s": 8.0, "kB/s": 8e3,
                  "MB/s": 8e6, "GB/s": 8e9, "KiB/s": 8 * 1024.0, "MiB/s": 8 * 1024.0 ** 2},
}
_TEMPERATURE = {"°C", "°F", "K"}


def unit_class(unit: str | None) -> str | None:
    if unit in _TEMPERATURE:
        return "temperature"
    for cls, factors in _FACTORS.items():
        if unit in factors:
            return cls
    return None


def convertible(src: str | None, dst: str | None) -> bool:
    if src == dst:
        return True
    cls = unit_class(src)
    return cls is not None and cls == unit_class(dst)


def convert(value: float, src: str | None, dst: str | None, *, delta: bool = False) -> float:
    """``value`` from ``src`` to ``dst``; ``delta`` for differences (a temperature change has no offset)."""
    if src == dst or value is None:
        return value
    cls = unit_class(src)
    if cls == "temperature":
        if src == "°F":
            c = value * 5 / 9 if delta else (value - 32) * 5 / 9
        elif src == "K":
            c = value if delta else value - 273.15
        else:
            c = value
        if dst == "°F":
            return c * 9 / 5 if delta else c * 9 / 5 + 32
        if dst == "K":
            return c if delta else c + 273.15
        return c
    if cls is None or unit_class(dst) != cls:
        raise ValueError(f"cannot convert {src} to {dst}")
    return value * _FACTORS[cls][src] / _FACTORS[cls][dst]


# -- time helpers -------------------------------------------------------------
def floor_to(ts: float, step: int) -> float:
    return math.floor(ts / step) * step


def parse_dt(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _ts_or_none(value: Any) -> float | None:
    dt = parse_dt(value)
    return dt.timestamp() if dt else None


def _float(state: Any) -> float | None:
    try:
        f = float(state)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


@dataclass
class Target:
    """A sensor to compile, as the event loop saw it when the compile started."""

    entity_id: str
    state_class: str
    unit: str | None
    name: str | None = None


def targets_from_states(states: Iterable[Any], entities: dict[str, Any] | None = None) -> list[Target]:
    """Every entity with a state class HA compiles statistics for.

    The state's own ``state_class`` wins; otherwise the registry's
    ``capabilities.state_class`` when the device confirmed it (a flow or the
    driver wrote it; ``EntityEntry.trusted``). A value only the import carried
    is not used: history HA kept stays, but new statistics follow what the
    device says.
    """
    out = []
    for st in states:
        attrs = st.attributes or {}
        sc = attrs.get("state_class")
        if sc is None and entities is not None and (ent := entities.get(st.entity_id)) is not None:
            trusted = getattr(ent, "trusted", None)
            sc = ((trusted("capabilities") if callable(trusted) else None) or {}).get("state_class")
        if sc in STATE_CLASSES_MEAN or sc in STATE_CLASSES_SUM:
            out.append(Target(st.entity_id, sc, attrs.get("unit_of_measurement"), attrs.get("friendly_name")))
    return out


class Statistics:
    """The statistics database plus the compiler that fills it."""

    def __init__(self, path: str, history_db: str | None = None, *, time_zone: Callable[[], str] | str = "UTC") -> None:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.path = path
        self.history_db = history_db
        self._tz = time_zone
        self._lock = threading.RLock()
        #: Held while compiling or importing, so the two never interleave.
        self.compile_lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.executescript(SCHEMA)
        self._hist: sqlite3.Connection | None = None
        self.last_error: str | None = None

    # -- plumbing -------------------------------------------------------------
    @property
    def tz(self) -> ZoneInfo:
        name = self._tz() if callable(self._tz) else self._tz
        try:
            return ZoneInfo(name or "UTC")
        except Exception:
            return ZoneInfo("UTC")

    def close(self) -> None:
        with self._lock:
            self._db.close()
            if self._hist is not None:
                self._hist.close()

    def _exec(self, sql: str, args: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._db.execute(sql, tuple(args))

    @contextlib.contextmanager
    def _tx(self):
        with self._lock:
            self._db.execute("BEGIN")
            try:
                yield self._db
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
            self._db.execute("COMMIT")

    def get_kv(self, key: str, default: Any = None) -> Any:
        row = self._exec("SELECT value FROM stats_kv WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_kv(self, key: str, value: Any) -> None:
        self._exec("INSERT OR REPLACE INTO stats_kv(key, value) VALUES (?, ?)", (key, json.dumps(value, default=str)))

    def _history(self) -> sqlite3.Connection:
        """A read-only connection to the hub's history (WAL lets it read while the hub writes)."""
        if self._hist is None:
            if not self.history_db:
                raise RuntimeError("no history database to compile from")
            self._hist = sqlite3.connect(f"file:{self.history_db}?mode=ro", uri=True, check_same_thread=False)
        return self._hist

    # -- metadata -------------------------------------------------------------
    def _meta_row(self, row: tuple) -> dict[str, Any]:
        mid, sid, source, unit, ucls, has_sum, mean_type, name = row
        return {"id": mid, "statistic_id": sid, "source": source, "unit_of_measurement": unit,
                "unit_class": ucls, "has_sum": bool(has_sum), "mean_type": int(mean_type or 0), "name": name}

    def get_meta(self, statistic_id: str) -> dict[str, Any] | None:
        row = self._exec("SELECT id, statistic_id, source, unit_of_measurement, unit_class, has_sum, mean_type, name "
                         "FROM statistics_meta WHERE statistic_id=?", (statistic_id,)).fetchone()
        return self._meta_row(row) if row else None

    def all_meta(self, statistic_ids: Iterable[str] | None = None) -> list[dict[str, Any]]:
        sql = ("SELECT id, statistic_id, source, unit_of_measurement, unit_class, has_sum, mean_type, name "
               "FROM statistics_meta")
        ids = list(statistic_ids) if statistic_ids is not None else None
        if ids is not None:
            if not ids:
                return []
            sql += f" WHERE statistic_id IN ({','.join('?' * len(ids))})"
        return [self._meta_row(r) for r in self._exec(sql + " ORDER BY statistic_id", ids or ()).fetchall()]

    def upsert_meta(self, statistic_id: str, *, source: str, unit: str | None, has_sum: bool, mean_type: int,
                    name: str | None = None) -> dict[str, Any]:
        cur = self.get_meta(statistic_id)
        ucls = unit_class(unit)
        if cur is None:
            self._exec("INSERT INTO statistics_meta(statistic_id, source, unit_of_measurement, unit_class, has_sum, "
                       "mean_type, name) VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (statistic_id, source, unit, ucls, int(has_sum), int(mean_type), name))
        elif (cur["has_sum"], cur["mean_type"], cur["source"], cur["name"]) != (has_sum, mean_type, source, name) \
                and (has_sum or mean_type):
            self._exec("UPDATE statistics_meta SET has_sum=?, mean_type=?, source=?, name=? WHERE id=?",
                       (int(has_sum), int(mean_type), source, name, cur["id"]))
        return self.get_meta(statistic_id)  # type: ignore[return-value]

    @staticmethod
    def public_meta(meta: dict[str, Any]) -> dict[str, Any]:
        """HA's ``recorder/list_statistic_ids`` / ``get_statistics_metadata`` item."""
        return {
            "statistic_id": meta["statistic_id"],
            "display_unit_of_measurement": meta["unit_of_measurement"],
            "has_mean": meta["mean_type"] == 1,
            "mean_type": meta["mean_type"],
            "has_sum": meta["has_sum"],
            "name": meta["name"],
            "source": meta["source"],
            "statistics_unit_of_measurement": meta["unit_of_measurement"],
            "unit_class": meta["unit_class"],
        }

    def list_statistic_ids(self, statistic_type: str | None = None,
                           statistic_ids: Iterable[str] | None = None) -> list[dict[str, Any]]:
        metas = self.all_meta(statistic_ids)
        if statistic_type == "mean":
            metas = [m for m in metas if m["mean_type"]]
        elif statistic_type == "sum":
            metas = [m for m in metas if m["has_sum"]]
        return [self.public_meta(m) for m in metas]

    def _last_row(self, table: str, metadata_id: int, before: float | None = None) -> dict[str, Any] | None:
        sql = f"SELECT start_ts, mean, min, max, last_reset_ts, state, sum FROM {table} WHERE metadata_id=?"
        args: list[Any] = [metadata_id]
        if before is not None:
            sql += " AND start_ts < ?"
            args.append(before)
        row = self._exec(sql + " ORDER BY start_ts DESC LIMIT 1", args).fetchone()
        if row is None:
            return None
        return dict(zip(("start", "mean", "min", "max", "last_reset", "state", "sum"), row, strict=True))

    # -- compiling ------------------------------------------------------------
    def compile(self, start: float, end: float, targets: list[Target]) -> dict[str, int]:
        """Compile every whole 5-minute period in ``[start, end)`` and the whole hours they finish."""
        start, end = floor_to(start, SHORT_TERM_S), floor_to(end, SHORT_TERM_S)
        if end <= start:
            return {"short_term": 0, "hourly": 0}
        with self.compile_lock:
            short = self._compile_short_term(start, end, targets)
            hourly = self._compile_hourly(floor_to(start, HOUR_S), floor_to(end, HOUR_S))
        return {"short_term": short, "hourly": hourly}

    def _history_rows(self, entity_id: str, start: float, end: float) -> tuple[tuple | None, list[tuple]]:
        """(the last row before ``start``, the rows in ``[start, end)``) as (ts, state, attributes)."""
        db = self._history()
        prior = db.execute("SELECT last_updated, state, attributes FROM history WHERE entity_id=? AND last_updated < ? "
                           "ORDER BY last_updated DESC LIMIT 1", (entity_id, start)).fetchone()
        rows = db.execute("SELECT last_updated, state, attributes FROM history WHERE entity_id=? AND last_updated >= ? "
                          "AND last_updated < ? ORDER BY last_updated", (entity_id, start, end)).fetchall()
        return prior, rows

    def _compile_short_term(self, start: float, end: float, targets: list[Target]) -> int:
        written = 0
        for t in targets:
            try:
                written += self._compile_entity(t, start, end)
            except Exception:
                log.exception("compiling statistics for %s failed", t.entity_id)
        return written

    def _compile_entity(self, t: Target, start: float, end: float) -> int:
        is_sum = t.state_class in STATE_CLASSES_SUM
        mean_type = STATE_CLASSES_MEAN.get(t.state_class, 0)
        meta = self.get_meta(t.entity_id)
        if meta is not None and meta["source"] != "recorder":
            return 0
        last = self._last_row("statistics_short_term", meta["id"]) if meta else None
        if last is not None and last["start"] >= end - SHORT_TERM_S:
            return 0
        first = max(start, last["start"] + SHORT_TERM_S) if last else start
        prior, rows = self._history_rows(t.entity_id, first, end)
        unit = meta["unit_of_measurement"] if meta else None

        def value(state: str, attrs_raw: str) -> tuple[float | None, dict[str, Any]]:
            nonlocal unit
            v = _float(state)
            if v is None:
                return None, {}
            try:
                attrs = json.loads(attrs_raw) if isinstance(attrs_raw, str) else dict(attrs_raw or {})
            except ValueError:
                attrs = {}
            u = attrs.get("unit_of_measurement")
            if unit is None and meta is None:
                unit = u
            if not convertible(u, unit):
                return None, attrs
            return convert(v, u, unit), attrs

        samples: list[tuple[float, float, dict[str, Any]]] = []
        if prior is not None:
            v, a = value(prior[1], prior[2])
            if v is not None:
                samples.append((first, v, a))
        for ts, st, at in rows:
            v, a = value(st, at)
            if v is not None:
                samples.append((ts, v, a))
        if not samples:
            return 0
        if meta is None:
            meta = self.upsert_meta(t.entity_id, source="recorder", unit=unit, has_sum=is_sum, mean_type=mean_type)
        elif (meta["has_sum"], meta["mean_type"]) != (is_sum, mean_type):
            meta = self.upsert_meta(t.entity_id, source="recorder", unit=meta["unit_of_measurement"], has_sum=is_sum,
                                    mean_type=mean_type, name=meta["name"])

        out: list[tuple] = []
        if is_sum:
            out = self._sum_periods(t.state_class, samples, first, end, last)
        else:
            out = self._mean_periods(samples, first, end, circular=mean_type == 2)
        if out:
            with self._tx() as db:
                db.executemany(
                    "INSERT OR REPLACE INTO statistics_short_term(metadata_id, start_ts, mean, min, max, last_reset_ts, "
                    "state, sum, origin) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [(meta["id"], *row, ORIGIN_COMPILED) for row in out])
        return len(out)

    @staticmethod
    def _mean_periods(samples: list[tuple[float, float, dict]], first: float, end: float, *,
                      circular: bool) -> list[tuple]:
        out = []
        idx = 0
        current: float | None = None
        p = first
        while p < end:
            p_end = p + SHORT_TERM_S
            # The value in force at the period's start.
            while idx < len(samples) and samples[idx][0] <= p:
                current = samples[idx][1]
                idx += 1
            values: list[tuple[float, float]] = []  # (value, seconds)
            t0, v0 = p, current
            j = idx
            while j < len(samples) and samples[j][0] < p_end:
                ts, v, _a = samples[j]
                if v0 is not None and ts > t0:
                    values.append((v0, ts - t0))
                t0, v0 = ts, v
                j += 1
            if v0 is not None:
                values.append((v0, p_end - t0))
            seen = [v for v, _ in values] + ([current] if current is not None else [])
            if values:
                total = sum(w for _, w in values) or 1.0
                if circular:
                    x = sum(math.cos(math.radians(v)) * w for v, w in values) / total
                    y = sum(math.sin(math.radians(v)) * w for v, w in values) / total
                    mean = math.degrees(math.atan2(y, x)) % 360
                else:
                    mean = sum(v * w for v, w in values) / total
                out.append((p, mean, min(seen), max(seen), None, None, None))
            current = v0
            idx = j
            p = p_end
        return out

    @staticmethod
    def _sum_periods(state_class: str, samples: list[tuple[float, float, dict]], first: float, end: float,
                     last: dict[str, Any] | None) -> list[tuple]:
        """HA's sum compile (``homeassistant/components/sensor/recorder.py``), one period at a time."""
        old_last_reset: str | None = None
        last_reset: str | None = None
        new_state = old_state = None
        total = 0.0
        if last is not None:
            last_reset = _iso_utc(last["last_reset"])
            old_last_reset = last_reset
            new_state = old_state = last["state"]
            total = last["sum"] or 0.0
        out = []
        idx = 0
        p = first
        while p < end:
            p_end = p + SHORT_TERM_S
            period: list[tuple[float, dict]] = []
            # The value in force at the start counts, as HA includes the start-time state.
            while idx < len(samples) and samples[idx][0] <= p:
                carried = samples[idx]
                idx += 1
                if idx == len(samples) or samples[idx][0] > p:
                    period.append((carried[1], carried[2]))
            while idx < len(samples) and samples[idx][0] < p_end:
                period.append((samples[idx][1], samples[idx][2]))
                idx += 1
            if not period and new_state is not None:
                carried_attrs = {"last_reset": old_last_reset} if old_last_reset else {}
                period.append((new_state, carried_attrs))
            for fstate, attrs in period:
                reset = False
                if state_class != "total_increasing" and \
                        (last_reset := _iso_utc(attrs.get("last_reset"))) != old_last_reset and last_reset is not None:
                    future = parse_dt(old_last_reset)
                    if old_state is not None and future is not None and future.timestamp() > p_end:
                        # The old reset time had not happened yet: it never marked a cycle.
                        old_last_reset = last_reset
                    else:
                        reset = True
                elif (old_state is None and last_reset is None) or (state_class == "total_increasing" and (
                        old_state is None or (new_state is not None and fstate < 0.9 * new_state))):
                    reset = True
                if reset:
                    if old_state is not None and new_state is not None:
                        total += new_state - old_state
                    new_state = fstate
                    old_last_reset = last_reset
                    old_state = 0.0 if old_state is not None else new_state
                else:
                    new_state = fstate
            if new_state is not None and old_state is not None:
                total += new_state - old_state
                out.append((p, None, None, None, _ts_or_none(last_reset), new_state, total))
                old_state = new_state
            p = p_end
        return out

    def _compile_hourly(self, start: float, end: float) -> int:
        """Hours in ``[start, end)`` from their short-term rows, for each statistic not compiled that far."""
        if end <= start:
            return 0
        rows = self._exec(
            "SELECT s.metadata_id, CAST(s.start_ts / 3600 AS INTEGER) * 3600 AS hour, AVG(s.mean), MIN(s.min), "
            "MAX(s.max), COUNT(*) FROM statistics_short_term s JOIN statistics_meta m ON m.id = s.metadata_id "
            "WHERE s.start_ts >= ? AND s.start_ts < ? AND m.source = 'recorder' GROUP BY s.metadata_id, hour",
            (start, end)).fetchall()
        written = 0
        last_hour: dict[int, float | None] = {}
        batch = []
        for mid, hour, mean, mn, mx, _n in rows:
            if mid not in last_hour:
                lr = self._last_row("statistics", mid)
                last_hour[mid] = lr["start"] if lr else None
            done = last_hour[mid]
            if done is not None and hour <= done:
                continue
            tail = self._exec("SELECT last_reset_ts, state, sum FROM statistics_short_term WHERE metadata_id=? AND "
                              "start_ts >= ? AND start_ts < ? ORDER BY start_ts DESC LIMIT 1",
                              (mid, hour, hour + HOUR_S)).fetchone()
            batch.append((mid, float(hour), mean, mn, mx, *(tail or (None, None, None)), ORIGIN_COMPILED))
            written += 1
        if batch:
            with self._tx() as db:
                db.executemany("INSERT OR REPLACE INTO statistics(metadata_id, start_ts, mean, min, max, last_reset_ts, "
                               "state, sum, origin) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", batch)
        return written

    def prune_short_term(self, keep_days: int = SHORT_TERM_KEEP_DAYS) -> int:
        cutoff = time.time() - keep_days * 86400
        return self._exec("DELETE FROM statistics_short_term WHERE start_ts < ?", (cutoff,)).rowcount

    # -- external statistics -----------------------------------------------------
    def add_hourly_amounts(self, statistic_id: str, *, source: str, unit: str, name: str | None,
                           amounts: Iterable[tuple[datetime, float]]) -> int:
        """Append hourly amounts (energy per hour, cost per hour) as a summed external statistic.

        Hours at or before the last stored one are skipped, so a driver can pass
        an overlapping window every poll. The running sum continues from the
        last stored row, as HA's ``async_add_external_statistics`` callers do.
        """
        if ":" not in statistic_id:
            raise ValueError("an external statistic id is 'source:object_id'")
        with self.compile_lock:
            meta = self.upsert_meta(statistic_id, source=source, unit=unit, has_sum=True, mean_type=0, name=name)
            last = self._last_row("statistics", meta["id"])
            total = (last["sum"] or 0.0) if last else 0.0
            done = last["start"] if last else None
            batch = []
            for when, amount in sorted(((floor_to(parse_dt(w).timestamp(), HOUR_S), float(a)) for w, a in amounts)):
                if done is not None and when <= done:
                    continue
                total += amount
                batch.append((meta["id"], when, None, None, None, None, amount, total, ORIGIN_EXTERNAL))
                done = when
            if batch:
                with self._tx() as db:
                    db.executemany("INSERT OR REPLACE INTO statistics(metadata_id, start_ts, mean, min, max, "
                                   "last_reset_ts, state, sum, origin) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", batch)
            return len(batch)

    def last_start(self, statistic_id: str) -> datetime | None:
        meta = self.get_meta(statistic_id)
        last = self._last_row("statistics", meta["id"]) if meta else None
        return datetime.fromtimestamp(last["start"], UTC) if last else None

    # -- reading ----------------------------------------------------------------
    def _rows(self, table: str, metadata_id: int, start: float, end: float | None) -> list[dict[str, Any]]:
        sql = (f"SELECT start_ts, mean, min, max, last_reset_ts, state, sum FROM {table} "
               "WHERE metadata_id=? AND start_ts >= ?")
        args: list[Any] = [metadata_id, start]
        if end is not None:
            sql += " AND start_ts < ?"
            args.append(end)
        keys = ("start", "mean", "min", "max", "last_reset", "state", "sum")
        return [dict(zip(keys, r, strict=True)) for r in self._exec(sql + " ORDER BY start_ts", args).fetchall()]

    def _bucket_start(self, ts: float, period: str) -> float:
        if period == "5minute":
            return floor_to(ts, SHORT_TERM_S)
        if period == "hour":
            return floor_to(ts, HOUR_S)
        local = datetime.fromtimestamp(ts, self.tz)
        day = local.replace(hour=0, minute=0, second=0, microsecond=0)
        if period == "week":
            day = day - timedelta(days=day.weekday())
        elif period == "month":
            day = day.replace(day=1)
        return day.timestamp()

    def _bucket_end(self, start: float, period: str) -> float:
        if period == "5minute":
            return start + SHORT_TERM_S
        if period == "hour":
            return start + HOUR_S
        local = datetime.fromtimestamp(start, self.tz)
        if period == "day":
            nxt = (local + timedelta(days=1, hours=2)).replace(hour=0)
        elif period == "week":
            nxt = (local + timedelta(days=7, hours=2)).replace(hour=0)
        else:
            nxt = (local.replace(day=28) + timedelta(days=5)).replace(day=1, hour=0)
        return nxt.replace(minute=0, second=0, microsecond=0).timestamp()

    def during_period(self, start: datetime, end: datetime | None, statistic_ids: Iterable[str] | None,
                      period: str = "hour", types: Iterable[str] | None = None,
                      units: dict[str, str] | None = None) -> dict[str, list[dict[str, Any]]]:
        """HA's ``statistics_during_period``: ``{statistic_id: [{start, end, mean, ..., change}]}`` (ms epochs)."""
        if period not in PERIODS:
            raise ValueError(f"period must be one of {', '.join(PERIODS)}")
        wanted = set(types or ALL_TYPES) & ALL_TYPES
        table = "statistics_short_term" if period == "5minute" else "statistics"
        start_ts = start.timestamp()
        end_ts = end.timestamp() if end else None
        result: dict[str, list[dict[str, Any]]] = {}
        ids = list(statistic_ids) if statistic_ids is not None else None
        for meta in self.all_meta(ids):
            rows = self._rows(table, meta["id"], start_ts, end_ts)
            if not rows:
                continue
            target_unit = _target_unit(meta, units)
            prior = self._last_row(table, meta["id"], before=start_ts) if "change" in wanted and meta["has_sum"] \
                else None
            prev_sum = prior["sum"] if prior and prior["sum"] is not None else None
            buckets: list[dict[str, Any]] = []
            if period in ("5minute", "hour"):
                for r in rows:
                    buckets.append({**r, "end": r["start"] + (SHORT_TERM_S if period == "5minute" else HOUR_S)})
            else:
                cur: dict[str, Any] | None = None
                means: list[float] = []
                for r in rows:
                    b = self._bucket_start(r["start"], period)
                    if cur is None or cur["start"] != b:
                        if cur is not None:
                            cur["mean"] = sum(means) / len(means) if means else None
                            buckets.append(cur)
                        cur = {"start": b, "end": self._bucket_end(b, period), "min": None, "max": None}
                        means = []
                    if r["mean"] is not None:
                        means.append(r["mean"])
                    if r["min"] is not None:
                        cur["min"] = r["min"] if cur["min"] is None else min(cur["min"], r["min"])
                    if r["max"] is not None:
                        cur["max"] = r["max"] if cur["max"] is None else max(cur["max"], r["max"])
                    cur["state"], cur["sum"], cur["last_reset"] = r["state"], r["sum"], r["last_reset"]
                if cur is not None:
                    cur["mean"] = sum(means) / len(means) if means else None
                    buckets.append(cur)
            out = []
            src_unit = meta["unit_of_measurement"]
            for b in buckets:
                item: dict[str, Any] = {"start": b["start"] * 1000, "end": b["end"] * 1000}
                if meta["mean_type"]:
                    for k in ("mean", "min", "max"):
                        if k in wanted:
                            item[k] = _conv(b.get(k), src_unit, target_unit)
                if meta["has_sum"]:
                    if "state" in wanted:
                        item["state"] = _conv(b.get("state"), src_unit, target_unit)
                    if "sum" in wanted:
                        item["sum"] = _conv(b.get("sum"), src_unit, target_unit, delta=True)
                    if "last_reset" in wanted:
                        item["last_reset"] = b["last_reset"] * 1000 if b.get("last_reset") is not None else None
                    if "change" in wanted:
                        s = b.get("sum")
                        change = None if s is None else s - (prev_sum or 0.0)
                        item["change"] = _conv(change, src_unit, target_unit, delta=True)
                        if s is not None:
                            prev_sum = s
                out.append(item)
            result[meta["statistic_id"]] = out
        return result

    def summary(self) -> dict[str, Any]:
        out: dict[str, Any] = {"metadata": self._exec("SELECT COUNT(*) FROM statistics_meta").fetchone()[0]}
        for table in TABLES:
            n, lo, hi = self._exec(f"SELECT COUNT(*), MIN(start_ts), MAX(start_ts) FROM {table}").fetchone()
            out[table] = {"rows": n,
                          "first": datetime.fromtimestamp(lo, UTC).isoformat() if lo else None,
                          "last": datetime.fromtimestamp(hi, UTC).isoformat() if hi else None}
        out["compiled_until"] = self.get_kv("compiled_until")
        out["last_import"] = self.get_kv("ha_import_report")
        out["last_error"] = self.last_error
        return out

    # -- importing HA's recorder ----------------------------------------------------
    def import_ha(self, db_path: str, *, chunk: int = 20000) -> dict[str, Any]:
        """Copy HA's ``statistics``, ``statistics_short_term`` and ``statistics_meta`` (read-only open).

        Idempotent: HA's rows replace whatever is stored for the same statistic
        and start time, and any rows the hub compiled for a statistic before
        HA's last row are dropped. Sums the hub compiled after HA's last row
        are moved onto HA's running total, so a sum never jumps back to zero.
        """
        if not os.path.exists(db_path):
            raise FileNotFoundError(db_path)
        src = sqlite3.connect(f"file:{db_path}?mode=ro&immutable=1", uri=True)
        report: dict[str, Any] = {"source": os.path.basename(db_path), "statistics": {}, "metadata": 0}
        try:
            cols = {r[1] for r in src.execute("PRAGMA table_info(statistics_meta)").fetchall()}
            mean_expr = "mean_type" if "mean_type" in cols else "CASE WHEN has_mean THEN 1 ELSE 0 END"
            name_expr = "name" if "name" in cols else "NULL"
            metas = src.execute(f"SELECT id, statistic_id, source, unit_of_measurement, has_sum, {mean_expr}, "
                                f"{name_expr} FROM statistics_meta").fetchall()
            with self.compile_lock:
                id_map: dict[int, int] = {}
                for ha_id, sid, source, unit, has_sum, mean_type, name in metas:
                    id_map[ha_id] = self._adopt_meta(sid, source or "recorder", unit, bool(has_sum),
                                                     int(mean_type or 0), name)
                report["metadata"] = len(id_map)
                for table in TABLES:
                    report["statistics"][table] = self._import_table(src, table, id_map, chunk)
        finally:
            src.close()
        report["imported_at"] = datetime.now(UTC).isoformat()
        self.set_kv("ha_import_report", report)
        return report

    def _adopt_meta(self, sid: str, source: str, unit: str | None, has_sum: bool, mean_type: int,
                    name: str | None) -> int:
        cur = self.get_meta(sid)
        if cur is not None and cur["unit_of_measurement"] != unit and convertible(cur["unit_of_measurement"], unit):
            # The hub compiled in another unit first (a sensor shown in kW that HA recorded in W): HA's wins.
            old = cur["unit_of_measurement"]
            for table in TABLES:
                rows = self._exec(f"SELECT start_ts, mean, min, max, state, sum FROM {table} WHERE metadata_id=?",
                                  (cur["id"],)).fetchall()
                with self._tx() as db:
                    db.executemany(
                        f"UPDATE {table} SET mean=?, min=?, max=?, state=?, sum=? WHERE metadata_id=? AND start_ts=?",
                        [(_conv(m, old, unit), _conv(lo, old, unit), _conv(hi, old, unit), _conv(s, old, unit),
                          _conv(sm, old, unit, delta=True), cur["id"], ts) for ts, m, lo, hi, s, sm in rows])
        if cur is None:
            self._exec("INSERT INTO statistics_meta(statistic_id, source, unit_of_measurement, unit_class, has_sum, "
                       "mean_type, name) VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (sid, source, unit, unit_class(unit), int(has_sum), mean_type, name))
        else:
            self._exec("UPDATE statistics_meta SET source=?, unit_of_measurement=?, unit_class=?, has_sum=?, "
                       "mean_type=?, name=? WHERE id=?",
                       (source, unit, unit_class(unit), int(has_sum), mean_type, name, cur["id"]))
        return self.get_meta(sid)["id"]  # type: ignore[index]

    def _import_table(self, src: sqlite3.Connection, table: str, id_map: dict[int, int], chunk: int) -> dict[str, Any]:
        cur = src.execute(f"SELECT metadata_id, start_ts, mean, min, max, last_reset_ts, state, sum FROM {table} "
                          "WHERE start_ts IS NOT NULL")
        n = 0
        lo = hi = None
        tails: dict[int, float] = {}
        while True:
            batch = cur.fetchmany(chunk)
            if not batch:
                break
            rows = []
            for ha_mid, start, mean, mn, mx, lr, state, total in batch:
                mid = id_map.get(ha_mid)
                if mid is None:
                    continue
                rows.append((mid, float(start), mean, mn, mx, lr, state, total, ORIGIN_IMPORTED))
                tails[mid] = max(tails.get(mid, start), start)
                lo = start if lo is None else min(lo, start)
                hi = start if hi is None else max(hi, start)
            with self._tx() as db:
                db.executemany(f"INSERT OR REPLACE INTO {table}(metadata_id, start_ts, mean, min, max, last_reset_ts, "
                               "state, sum, origin) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
            n += len(rows)
        rebased = 0
        for mid, tail in tails.items():
            rebased += self._rebase_after(table, mid, tail)
        return {"rows": n, "statistics": len(tails), "rebased": rebased,
                "first": datetime.fromtimestamp(lo, UTC).isoformat() if lo else None,
                "last": datetime.fromtimestamp(hi, UTC).isoformat() if hi else None}

    def _rebase_after(self, table: str, metadata_id: int, tail: float) -> int:
        """Drop compiled rows up to the imported tail; continue later compiled sums from the tail's sum."""
        self._exec(f"DELETE FROM {table} WHERE metadata_id=? AND origin=? AND start_ts <= ?",
                   (metadata_id, ORIGIN_COMPILED, tail))
        last = self._exec(f"SELECT state, sum, last_reset_ts FROM {table} WHERE metadata_id=? AND start_ts=?",
                          (metadata_id, tail)).fetchone()
        if last is None or last[1] is None:
            return 0
        rows = self._exec(f"SELECT start_ts, state, sum, last_reset_ts FROM {table} WHERE metadata_id=? AND "
                          "start_ts > ? AND origin=? ORDER BY start_ts", (metadata_id, tail, ORIGIN_COMPILED)).fetchall()
        if not rows or rows[0][2] is None:
            return 0
        t_state, t_sum, t_reset = last
        first_state, first_sum, first_reset = rows[0][1], rows[0][2], rows[0][3]
        dropped = first_state is not None and t_state is not None and first_state < 0.9 * t_state
        # As in the compiler: a stored reset time still in the future never marked a cycle.
        new_cycle = first_reset is not None and first_reset != t_reset and not (
            t_reset is not None and t_reset > rows[0][0] + SHORT_TERM_S)
        if new_cycle or dropped:
            base = t_sum + (first_state or 0.0)  # a new cycle started between the two
        else:
            base = t_sum + ((first_state or 0.0) - (t_state or 0.0))
        offset = base - first_sum
        if abs(offset) < 1e-9:
            return 0
        with self._tx() as db:
            db.executemany(f"UPDATE {table} SET sum = sum + ? WHERE metadata_id=? AND start_ts=?",
                           [(offset, metadata_id, r[0]) for r in rows if r[2] is not None])
        return len(rows)


def _iso_utc(value: Any) -> str | None:
    """A last_reset (ISO text or epoch seconds) as a UTC ISO string, the form HA compares."""
    if value is None or value == "":
        return None
    if isinstance(value, int | float):
        return datetime.fromtimestamp(float(value), UTC).isoformat()
    dt = parse_dt(value)
    return dt.astimezone(UTC).isoformat() if dt else None


def _target_unit(meta: dict[str, Any], units: dict[str, str] | None) -> str | None:
    src = meta["unit_of_measurement"]
    want = (units or {}).get(meta["unit_class"] or "")
    return want if want and convertible(src, want) else src


def _conv(value: float | None, src: str | None, dst: str | None, *, delta: bool = False) -> float | None:
    if value is None or src == dst:
        return value
    try:
        return convert(value, src, dst, delta=delta)
    except ValueError:
        return value


# -- running inside the hub ------------------------------------------------------------
def default_path(store: Any) -> str:
    return os.getenv("HOMEHUB_STATS_DB") or os.path.join(os.path.dirname(os.path.abspath(store.path)), "statistics.db")


async def start(runtime: Any) -> None:
    """Open the statistics database (once per process) and compile every five minutes until cancelled.

    main.py starts this as a background task; an import restarts the hub's
    tasks, which only restarts this loop, never reopens the database.
    """
    hub = runtime.hub
    stats = getattr(runtime, "statistics", None)
    if stats is None:
        stats = Statistics(default_path(runtime.store), runtime.store.path, time_zone=lambda: hub.time_zone)
        runtime.statistics = stats
    hub.statistics = stats  # drivers that feed external statistics (srp_energy) find it here
    await run_compiler(stats, hub)


async def run_compiler(stats: Statistics, hub: Any, *, sleep: Callable[[float], Any] = asyncio.sleep) -> None:
    last_prune = 0.0
    while True:
        now = time.time()
        end = floor_to(now - COMPILE_DELAY_S, SHORT_TERM_S)
        done = stats.get_kv("compiled_until")
        start = max(float(done) if done else 0.0, end - CATCHUP_DAYS * 86400)
        if end > start:
            targets = targets_from_states(hub.states.all(), hub.entities)
            try:
                counts = await asyncio.to_thread(stats.compile, start, end, targets)
                stats.set_kv("compiled_until", end)
                stats.last_error = None
                if end - start > SHORT_TERM_S:
                    log.info("statistics caught up %s to %s: %s", datetime.fromtimestamp(start, UTC).isoformat(),
                             datetime.fromtimestamp(end, UTC).isoformat(), counts)
            except Exception as exc:
                stats.last_error = f"{type(exc).__name__}: {exc}"
                log.exception("statistics compile failed")
        if now - last_prune > 6 * 3600:
            with contextlib.suppress(Exception):
                n = await asyncio.to_thread(stats.prune_short_term)
                if n:
                    log.info("pruned %d short-term statistics rows", n)
            last_prune = now
        nxt = floor_to(time.time(), SHORT_TERM_S) + SHORT_TERM_S + COMPILE_DELAY_S
        await sleep(max(1.0, nxt - time.time()))

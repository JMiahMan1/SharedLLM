"""Jarvis's own record of every device on the home network.

Stored in the same SQLite file as the router's log
(``data/network/router.db``), so a device's MAC is the key and its alias,
kind, make, model, owner and notes live next to the DNS lookups that helped
identify it (docs/NETWORK.md).
"""
from __future__ import annotations

import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

log = logging.getLogger("control_plane.network_devices")

DATA_DIR = Path(os.getenv("NETWORK_DATA_DIR", "/data/network"))

KINDS = ("phone", "tablet", "laptop", "desktop", "tv", "streaming", "speaker",
         "smart_home", "camera", "printer", "console", "network", "wearable",
         "other", "unknown")

SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
    mac TEXT PRIMARY KEY,
    alias TEXT,
    kind TEXT,
    make TEXT,
    model TEXT,
    os TEXT,
    owner TEXT,
    notes TEXT,
    first_seen REAL,
    last_seen REAL,
    private_mac INTEGER NOT NULL DEFAULT 0,
    kind_source TEXT,
    make_source TEXT,
    model_source TEXT,
    os_source TEXT
);
"""


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    out = {k: row[k] for k in list(row.keys())}
    out["private_mac"] = bool(out.get("private_mac"))
    out["mac"] = (out.get("mac") or "").lower()
    return out


class Store:
    """The SQLite file; one writer at a time, readers open their own connections."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.execute("PRAGMA journal_mode=WAL")
        db.row_factory = sqlite3.Row
        return db

    def get(self, mac: str) -> dict[str, Any] | None:
        mac = (mac or "").lower()
        with self._connect() as db:
            row = db.execute("SELECT * FROM devices WHERE mac = ?", (mac,)).fetchone()
        return _row_to_dict(row) if row is not None else None

    def list(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM devices ORDER BY first_seen, mac").fetchall()
        return [_row_to_dict(r) for r in rows]

    def ensure(self, mac: str, now: float, touch: bool = True) -> None:
        """Insert a device the first time it is seen; touch last_seen when asked."""
        mac = (mac or "").lower()
        with self._connect() as db:
            row = db.execute("SELECT 1 FROM devices WHERE mac = ?", (mac,)).fetchone()
            if row is None:
                db.execute(
                    "INSERT INTO devices (mac, first_seen, last_seen, private_mac) VALUES (?,?,?,0)",
                    (mac, now, now),
                )
            elif touch:
                db.execute("UPDATE devices SET last_seen = ? WHERE mac = ?", (now, mac))

    def delete(self, mac: str) -> None:
        with self._connect() as db:
            db.execute("DELETE FROM devices WHERE mac = ?", ((mac or "").lower(),))

    def set_private_mac(self, mac: str, value: bool) -> None:
        with self._connect() as db:
            db.execute("UPDATE devices SET private_mac = ? WHERE mac = ?",
                       (1 if value else 0, (mac or "").lower()))

    def apply_detection(self, mac: str, updates: dict[str, Any], source: str,
                        only_if_missing: bool = False, overwrite: bool = True) -> None:
        """Apply detected values, never touching a value whose source is 'manual'.

        ``only_if_missing`` leaves an already detected value alone (used by the
        lower priority sources such as DNS and hostname patterns).
        """
        mac = (mac or "").lower()
        rec = self.get(mac)
        if rec is None:
            return
        sets: list[str] = []
        params: list[Any] = []
        for field in ("kind", "make", "model", "os"):
            value = updates.get(field) if isinstance(updates, dict) else None
            if value is None or value == "":
                continue
            src_col = f"{field}_source"
            if rec.get(src_col) == "manual":
                continue
            if only_if_missing and rec.get(field):
                continue
            if not overwrite and rec.get(field):
                continue
            sets.append(f"{field} = ?")
            params.append(value)
            sets.append(f"{src_col} = ?")
            params.append(source)
        if not sets:
            return
        params.append(mac)
        with self._connect() as db:
            db.execute(f"UPDATE devices SET {', '.join(sets)} WHERE mac = ?", params)

    def set_manual(self, mac: str, fields: dict[str, Any]) -> None:
        """Apply a hand edit: provided fields become source 'manual'; '' clears."""
        mac = (mac or "").lower()
        self.ensure(mac, time.time(), touch=False)
        sets: list[str] = []
        params: list[Any] = []
        for field in ("alias", "kind", "make", "model", "os", "owner", "notes"):
            if field not in fields:
                continue
            value = fields[field]
            if value == "":
                sets.append(f"{field} = NULL")
                if field in ("kind", "make", "model", "os"):
                    sets.append(f"{field}_source = NULL")
                continue
            if value is None:
                continue
            sets.append(f"{field} = ?")
            params.append(value)
            if field in ("kind", "make", "model", "os"):
                sets.append(f"{field}_source = ?")
                params.append("manual")
        if not sets:
            return
        params.append(mac)
        with self._connect() as db:
            db.execute(f"UPDATE devices SET {', '.join(sets)} WHERE mac = ?", params)

    def touch(self, mac: str, now: float) -> None:
        mac = (mac or "").lower()
        with self._connect() as db:
            db.execute("UPDATE devices SET last_seen = ? WHERE mac = ?", (now, mac))


DeviceStore = Store

_store: Store | None = None


def store() -> Store | None:
    global _store
    if _store is None:
        try:
            _store = Store(DATA_DIR / "router.db")
        except Exception as e:  # pragma: no cover - defensive
            log.error(f"[network-devices] cannot open store: {e}")
            return None
    return _store

get_store = store

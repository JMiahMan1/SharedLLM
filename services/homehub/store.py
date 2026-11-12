"""Persistence: one SQLite file holding the registry, the credentials and history.

Config entry ``data`` holds device credentials (ESPHome keys, HomeKit pairing
keys, TV tokens, cloud passwords), so it is Fernet-encrypted at rest with the
stack's ``FERNET_KEY``. Without a key the hub refuses to store credentials
rather than writing them in the clear.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from dataclasses import asdict, fields
from datetime import UTC, datetime, timedelta
from typing import Any

from services.homehub.core import Area, ConfigEntry, DeviceEntry, EntityEntry, State

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS config_entries (entry_id TEXT PRIMARY KEY, doc TEXT NOT NULL, secret BLOB);
CREATE TABLE IF NOT EXISTS devices (id TEXT PRIMARY KEY, doc TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS entities (entity_id TEXT PRIMARY KEY, doc TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS areas (area_id TEXT PRIMARY KEY, doc TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS last_states (entity_id TEXT PRIMARY KEY, doc TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id TEXT NOT NULL,
    state TEXT NOT NULL,
    attributes TEXT NOT NULL,
    last_changed REAL NOT NULL,
    last_updated REAL NOT NULL,
    state_changed INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS history_entity_time ON history(entity_id, last_updated);
CREATE INDEX IF NOT EXISTS history_time ON history(last_updated);
CREATE TABLE IF NOT EXISTS automations (id TEXT PRIMARY KEY, kind TEXT NOT NULL, doc TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS refresh_tokens (id TEXT PRIMARY KEY, doc TEXT NOT NULL, secret BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS blobs (name TEXT PRIMARY KEY, secret BLOB NOT NULL);
"""


class SecretBox:
    def __init__(self, key: str | None) -> None:
        self._fernet = None
        if key:
            from cryptography.fernet import Fernet

            self._fernet = Fernet(key.encode() if isinstance(key, str) else key)

    @property
    def enabled(self) -> bool:
        return self._fernet is not None

    def seal(self, value: Any) -> bytes:
        if self._fernet is None:
            raise RuntimeError("FERNET_KEY is not set; refusing to store device credentials unencrypted")
        return self._fernet.encrypt(json.dumps(value).encode())

    def open(self, blob: bytes | None) -> Any:
        if not blob:
            return {}
        if self._fernet is None:
            raise RuntimeError("FERNET_KEY is not set; cannot read stored device credentials")
        return json.loads(self._fernet.decrypt(blob))


def _doc(obj: Any) -> str:
    return json.dumps(asdict(obj), default=str)


def _load(cls: type, raw: str) -> Any:
    data = json.loads(raw)
    names = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in names})


class Store:
    def __init__(self, path: str, fernet_key: str | None = None) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.path = path
        self.box = SecretBox(fernet_key)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # -- generic ------------------------------------------------------------
    def _exec(self, sql: str, args: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._db.execute(sql, args)

    def get_kv(self, key: str, default: Any = None) -> Any:
        row = self._exec("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_kv(self, key: str, value: Any) -> None:
        self._exec("INSERT OR REPLACE INTO kv(key, value) VALUES (?, ?)", (key, json.dumps(value, default=str)))

    # -- registry -----------------------------------------------------------
    def save_config_entry(self, entry: ConfigEntry) -> None:
        public = asdict(entry)
        secret = public.pop("data")
        public["state"] = "not_loaded"
        public["reason"] = None
        self._exec("INSERT OR REPLACE INTO config_entries(entry_id, doc, secret) VALUES (?, ?, ?)",
                   (entry.entry_id, json.dumps(public, default=str), self.box.seal(secret)))

    def load_config_entries(self) -> list[ConfigEntry]:
        out = []
        for _eid, doc, secret in self._exec("SELECT entry_id, doc, secret FROM config_entries").fetchall():
            data = json.loads(doc)
            data["data"] = self.box.open(secret)
            out.append(_load(ConfigEntry, json.dumps(data)))
        return out

    def delete_config_entry(self, entry_id: str) -> None:
        self._exec("DELETE FROM config_entries WHERE entry_id=?", (entry_id,))

    def save_device(self, dev: DeviceEntry) -> None:
        self._exec("INSERT OR REPLACE INTO devices(id, doc) VALUES (?, ?)", (dev.id, _doc(dev)))

    def load_devices(self) -> list[DeviceEntry]:
        return [_load(DeviceEntry, r[0]) for r in self._exec("SELECT doc FROM devices").fetchall()]

    def save_entity(self, ent: EntityEntry) -> None:
        self._exec("INSERT OR REPLACE INTO entities(entity_id, doc) VALUES (?, ?)", (ent.entity_id, _doc(ent)))

    def load_entities(self) -> list[EntityEntry]:
        return [_load(EntityEntry, r[0]) for r in self._exec("SELECT doc FROM entities").fetchall()]

    def save_area(self, area: Area) -> None:
        self._exec("INSERT OR REPLACE INTO areas(area_id, doc) VALUES (?, ?)", (area.area_id, _doc(area)))

    def load_areas(self) -> list[Area]:
        return [_load(Area, r[0]) for r in self._exec("SELECT doc FROM areas").fetchall()]

    def save_blob(self, name: str, value: Any) -> None:
        """A named secret (certificates, token stores) sealed like entry data."""
        self._exec("INSERT OR REPLACE INTO blobs(name, secret) VALUES (?, ?)", (name, self.box.seal(value)))

    def load_blob(self, name: str, default: Any = None) -> Any:
        row = self._exec("SELECT secret FROM blobs WHERE name=?", (name,)).fetchone()
        return self.box.open(row[0]) if row else default

    def save_refresh_token(self, token_id: str, public: dict[str, Any], jwt_key: str) -> None:
        self._exec("INSERT OR REPLACE INTO refresh_tokens(id, doc, secret) VALUES (?, ?, ?)",
                   (token_id, json.dumps(public, default=str), self.box.seal({"jwt_key": jwt_key})))

    def refresh_token(self, token_id: str) -> tuple[dict[str, Any], str] | None:
        row = self._exec("SELECT doc, secret FROM refresh_tokens WHERE id=?", (token_id,)).fetchone()
        if not row:
            return None
        return json.loads(row[0]), self.box.open(row[1])["jwt_key"]

    def count(self, table: str) -> int:
        assert table in {"config_entries", "devices", "entities", "areas", "history", "automations",
                         "refresh_tokens", "last_states", "blobs"}
        return self._exec(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    # -- automations / scripts / scenes ---------------------------------------
    def save_automation(self, item_id: str, kind: str, doc: dict[str, Any]) -> None:
        self._exec("INSERT OR REPLACE INTO automations(id, kind, doc) VALUES (?, ?, ?)",
                   (f"{kind}:{item_id}", kind, json.dumps(doc, default=str)))

    def load_automations(self, kind: str) -> list[dict[str, Any]]:
        rows = self._exec("SELECT doc FROM automations WHERE kind=? ORDER BY id", (kind,)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def delete_automation(self, item_id: str, kind: str) -> None:
        self._exec("DELETE FROM automations WHERE id=?", (f"{kind}:{item_id}",))

    # -- states and history -------------------------------------------------
    def save_last_state(self, state: State) -> None:
        self._exec("INSERT OR REPLACE INTO last_states(entity_id, doc) VALUES (?, ?)",
                   (state.entity_id, json.dumps(state.as_dict(), default=str)))

    def load_last_states(self) -> list[State]:
        return [State.from_dict(json.loads(r[0])) for r in self._exec("SELECT doc FROM last_states").fetchall()]

    def record(self, state: State, state_changed: bool) -> None:
        self._exec(
            "INSERT INTO history(entity_id, state, attributes, last_changed, last_updated, state_changed) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (state.entity_id, state.state, json.dumps(state.attributes, default=str),
             state.last_changed.timestamp(), state.last_updated.timestamp(), int(state_changed)),
        )

    def history(self, entity_ids: list[str] | None, start: datetime, end: datetime | None = None,
                *, changes_only: bool = False, limit: int = 50000) -> list[dict[str, Any]]:
        sql = "SELECT entity_id, state, attributes, last_changed, last_updated FROM history WHERE last_updated >= ?"
        args: list[Any] = [start.timestamp()]
        if end is not None:
            sql += " AND last_updated <= ?"
            args.append(end.timestamp())
        if entity_ids:
            sql += f" AND entity_id IN ({','.join('?' * len(entity_ids))})"
            args.extend(entity_ids)
        if changes_only:
            sql += " AND state_changed = 1"
        sql += " ORDER BY last_updated LIMIT ?"
        args.append(limit)
        out = []
        for eid, st, attrs, lc, lu in self._exec(sql, tuple(args)).fetchall():
            out.append({
                "entity_id": eid, "state": st, "attributes": json.loads(attrs),
                "last_changed": datetime.fromtimestamp(lc, UTC).isoformat(),
                "last_updated": datetime.fromtimestamp(lu, UTC).isoformat(),
            })
        return out

    def prune_history(self, keep_days: int) -> int:
        cutoff = (datetime.now(UTC) - timedelta(days=keep_days)).timestamp()
        return self._exec("DELETE FROM history WHERE last_updated < ?", (cutoff,)).rowcount

    def import_history_rows(self, rows: list[tuple[str, str, str, float, float]]) -> None:
        with self._lock:
            self._db.execute("BEGIN")
            try:
                self._db.executemany(
                    "INSERT INTO history(entity_id, state, attributes, last_changed, last_updated, state_changed) "
                    "VALUES (?, ?, ?, ?, ?, 1)", rows)
                self._db.execute("COMMIT")
            except Exception:
                self._db.execute("ROLLBACK")
                raise


def monotonic() -> float:
    return time.monotonic()

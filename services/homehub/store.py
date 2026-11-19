"""Persistence: one SQLite file holding the registry, the credentials and history.

Config entry ``data`` holds device credentials (ESPHome keys, HomeKit pairing
keys, TV tokens, cloud passwords), so it is Fernet-encrypted at rest with the
stack's ``FERNET_KEY``. Without a key the hub refuses to store credentials
rather than writing them in the clear.
"""
from __future__ import annotations

import contextlib
import fnmatch
import hashlib
import json
import os
import sqlite3
import threading
import time
from collections.abc import Callable
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
CREATE TABLE IF NOT EXISTS state_attributes (
    attributes_id INTEGER PRIMARY KEY AUTOINCREMENT,
    hash TEXT NOT NULL UNIQUE,
    shared_attrs TEXT NOT NULL
);
"""

#: History rows read their attributes from the shared table when they name one (rows written since it came),
#: else from their own column (older rows, imported rows). Every reader selects through this.
HISTORY_ATTRS = "COALESCE(sa.shared_attrs, h.attributes)"
HISTORY_FROM = "history h LEFT JOIN state_attributes sa ON sa.attributes_id = h.attributes_id"
#: How many attribute hashes the writer remembers the row id of (a few thousand entities, most stable).
ATTR_CACHE_SIZE = 20000


def canonical_attrs(attrs: dict[str, Any]) -> str:
    """The one spelling of a set of attributes, so equal sets share one ``state_attributes`` row."""
    return json.dumps(attrs, default=str, sort_keys=True, separators=(",", ":"))


def attrs_hash(canonical: str) -> str:
    return hashlib.blake2b(canonical.encode(), digest_size=16).hexdigest()


def _migrate(db: sqlite3.Connection) -> None:
    """Additive only: old history rows keep their own ``attributes`` and stay readable as they are."""
    cols = {r[1] for r in db.execute("PRAGMA table_info(history)").fetchall()}
    if "attributes_id" not in cols:
        db.execute("ALTER TABLE history ADD COLUMN attributes_id INTEGER")
    # Partial: the existing rows (all NULL) cost nothing to index, and purging orphans stays an index lookup.
    db.execute("CREATE INDEX IF NOT EXISTS history_attributes_id ON history(attributes_id) "
               "WHERE attributes_id IS NOT NULL")


def entity_matcher(entity_ids: list[str] | None = None, domains: list[str] | None = None,
                   globs: list[str] | None = None) -> Callable[[str], bool]:
    """A predicate over entity ids: named, in one of the domains, or matching one of the globs."""
    ids, doms, pats = set(entity_ids or ()), set(domains or ()), list(globs or ())

    def match(eid: str) -> bool:
        return eid in ids or eid.split(".", 1)[0] in doms or any(fnmatch.fnmatchcase(eid, p) for p in pats)

    return match


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
        _migrate(self._db)
        #: attribute hash -> state_attributes row id, shared by every connection that writes history.
        self._attr_ids: dict[str, int] = {}
        self._attr_lock = threading.Lock()
        #: The recorder's writer thread (recorder.py) when one runs; readers wait for what it holds.
        self.writer: Any = None

    def connect(self) -> sqlite3.Connection:
        """Another connection to this file (the recorder's writer thread keeps its own)."""
        db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None, timeout=10.0)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=NORMAL")
        return db

    def _sync_writer(self) -> None:
        """Rows queued before a read are committed before it runs (a reader sees what it just caused)."""
        w = self.writer
        if w is not None:
            w.sync()

    def close(self) -> None:
        w, self.writer = self.writer, None
        if w is not None:
            w.stop()  # what the recorder still holds (restore states included) is committed first
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
        public["needs"] = None
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

    def record(self, state: State, state_changed: bool, attributes: dict[str, Any] | None = None) -> None:
        """One history row, written now on this connection. ``attributes`` are the recorded ones (default all)."""
        canonical = canonical_attrs(state.attributes if attributes is None else attributes)
        with self._lock:
            self.write_history(self._db, [(state.entity_id, state.state, canonical, attrs_hash(canonical),
                                           state.last_changed.timestamp(), state.last_updated.timestamp(),
                                           int(state_changed))])

    def attributes_id(self, db: sqlite3.Connection, canonical: str, digest: str) -> int:
        with self._attr_lock:
            got = self._attr_ids.get(digest)
        if got is not None:
            return got
        db.execute("INSERT OR IGNORE INTO state_attributes(hash, shared_attrs) VALUES (?, ?)", (digest, canonical))
        got = db.execute("SELECT attributes_id FROM state_attributes WHERE hash=?", (digest,)).fetchone()[0]
        with self._attr_lock:
            if len(self._attr_ids) >= ATTR_CACHE_SIZE:
                self._attr_ids.clear()
            self._attr_ids[digest] = got
        return got

    def write_history(self, db: sqlite3.Connection, rows: list[tuple[str, str, str, str, float, float, int]]) -> None:
        """Rows of (entity_id, state, canonical attributes, their hash, last_changed, last_updated, state_changed).

        The caller holds the connection (and any transaction). The row's own ``attributes`` column is left
        empty: the attributes live once in ``state_attributes``.
        """
        out = [(eid, st, self.attributes_id(db, canonical, digest), lc, lu, changed)
               for eid, st, canonical, digest, lc, lu, changed in rows]
        db.executemany("INSERT INTO history(entity_id, state, attributes, attributes_id, last_changed, last_updated, "
                       "state_changed) VALUES (?, ?, '', ?, ?, ?, ?)", out)

    def forget_attribute_ids(self) -> None:
        """After orphaned attribute rows are deleted no cached id may be reused."""
        with self._attr_lock:
            self._attr_ids.clear()

    def history(self, entity_ids: list[str] | None, start: datetime, end: datetime | None = None,
                *, changes_only: bool = False, limit: int = 50000) -> list[dict[str, Any]]:
        return self.history_page(entity_ids, start, end, changes_only=changes_only, limit=limit)[0]

    def history_page(self, entity_ids: list[str] | None, start: datetime, end: datetime | None = None,
                     *, changes_only: bool = False, limit: int = 50000) -> tuple[list[dict[str, Any]], bool]:
        """Rows in ``[start, end]``, oldest first, and whether older rows were left out to stay under ``limit``.

        The newest rows are the ones kept: a window longer than ``limit`` rows loses its oldest part, never
        the hours a person is most likely looking at.
        """
        self._sync_writer()
        sql = (f"SELECT h.entity_id, h.state, {HISTORY_ATTRS}, h.last_changed, h.last_updated FROM {HISTORY_FROM} "
               "WHERE h.last_updated >= ?")
        args: list[Any] = [start.timestamp()]
        if end is not None:
            sql += " AND h.last_updated <= ?"
            args.append(end.timestamp())
        if entity_ids:
            sql += f" AND h.entity_id IN ({','.join('?' * len(entity_ids))})"
            args.extend(entity_ids)
        if changes_only:
            sql += " AND h.state_changed = 1"
        sql += " ORDER BY h.last_updated DESC LIMIT ?"
        args.append(limit + 1)
        rows = self._exec(sql, tuple(args)).fetchall()
        truncated = len(rows) > limit
        return [self._history_row(*r) for r in reversed(rows[:limit])], truncated

    def states_at(self, entity_ids: list[str], when: datetime) -> dict[str, dict[str, Any]]:
        """Each entity's last recorded state before ``when`` (HA's start-time state); entities with none are left out."""
        self._sync_writer()
        out: dict[str, dict[str, Any]] = {}
        sql = (f"SELECT h.entity_id, h.state, {HISTORY_ATTRS}, h.last_changed, h.last_updated FROM {HISTORY_FROM} "
               "WHERE h.entity_id = ? AND h.last_updated < ? ORDER BY h.last_updated DESC, h.id DESC LIMIT 1")
        for eid in dict.fromkeys(entity_ids):
            row = self._exec(sql, (eid, when.timestamp())).fetchone()
            if row is not None:
                out[eid] = self._history_row(*row)
        return out

    @staticmethod
    def _history_row(eid: str, st: str, attrs: str, lc: float, lu: float) -> dict[str, Any]:
        return {
            "entity_id": eid, "state": st, "attributes": json.loads(attrs) if attrs else {},
            "last_changed": datetime.fromtimestamp(lc, UTC).isoformat(),
            "last_updated": datetime.fromtimestamp(lu, UTC).isoformat(),
        }

    def _using(self, db: sqlite3.Connection | None) -> tuple[sqlite3.Connection, Any]:
        """The connection to run on: the caller's own (the writer thread's, unlocked) or this store's, locked."""
        return (db, contextlib.nullcontext()) if db is not None else (self._db, self._lock)

    def prune_history(self, keep_days: float, db: sqlite3.Connection | None = None) -> int:
        """Rows older than ``keep_days`` go, then the attribute rows nothing names any more."""
        cutoff = (datetime.now(UTC) - timedelta(days=keep_days)).timestamp()
        conn, lock = self._using(db)
        with lock:
            n = conn.execute("DELETE FROM history WHERE last_updated < ?", (cutoff,)).rowcount
        self.purge_orphan_attributes(db)
        return n

    def purge_entities(self, match: Callable[[str], bool], keep_days: float = 0,
                       db: sqlite3.Connection | None = None) -> int:
        """Rows of every entity ``match(entity_id)`` accepts, older than ``keep_days`` (0: all of them)."""
        cutoff = (datetime.now(UTC) - timedelta(days=keep_days)).timestamp()
        conn, lock = self._using(db)
        with lock:
            ids = [r[0] for r in conn.execute("SELECT DISTINCT entity_id FROM history").fetchall() if match(r[0])]
            n = 0
            for i in range(0, len(ids), 500):
                chunk = ids[i:i + 500]
                n += conn.execute(f"DELETE FROM history WHERE last_updated < ? AND entity_id IN "
                                  f"({','.join('?' * len(chunk))})", (cutoff, *chunk)).rowcount
        self.purge_orphan_attributes(db)
        return n

    def purge_orphan_attributes(self, db: sqlite3.Connection | None = None) -> int:
        conn, lock = self._using(db)
        with lock:
            n = conn.execute("DELETE FROM state_attributes WHERE attributes_id NOT IN "
                             "(SELECT attributes_id FROM history WHERE attributes_id IS NOT NULL)").rowcount
        self.forget_attribute_ids()
        return n

    def repack(self, db: sqlite3.Connection | None = None) -> None:
        """Give the space deleted rows held back to the disk (HA's ``repack``). Locks the file while it runs."""
        conn, lock = self._using(db)
        with lock:
            conn.execute("VACUUM")
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def size_report(self, now: datetime | None = None, days: int = 7) -> dict[str, Any]:
        """How big the recorder is and what fills it: rows per day (UTC) and the busiest entities of the last day."""
        self._sync_writer()
        now = now or datetime.now(UTC)
        db_bytes = os.path.getsize(self.path) if os.path.exists(self.path) else 0
        wal = self.path + "-wal"
        wal_bytes = os.path.getsize(wal) if os.path.exists(wal) else 0
        since = (now - timedelta(days=days)).timestamp()
        day_ago = (now - timedelta(days=1)).timestamp()
        q = self._exec
        page_size = q("PRAGMA page_size").fetchone()[0]
        free = q("PRAGMA freelist_count").fetchone()[0] * page_size
        per_day = q("SELECT date(last_updated, 'unixepoch') d, COUNT(*) FROM history WHERE last_updated >= ? "
                    "GROUP BY d ORDER BY d", (since,)).fetchall()
        top = q("SELECT entity_id, COUNT(*) c FROM history WHERE last_updated >= ? GROUP BY entity_id "
                "ORDER BY c DESC, entity_id LIMIT 10", (day_ago,)).fetchall()
        oldest = q("SELECT MIN(last_updated) FROM history").fetchone()[0]
        return {
            "db_bytes": db_bytes, "wal_bytes": wal_bytes, "free_bytes": free,
            "history_rows": q("SELECT COUNT(*) FROM history").fetchone()[0],
            "legacy_attribute_rows": q("SELECT COUNT(*) FROM history WHERE attributes_id IS NULL").fetchone()[0],
            "state_attributes_rows": q("SELECT COUNT(*) FROM state_attributes").fetchone()[0],
            "oldest": datetime.fromtimestamp(oldest, UTC).isoformat() if oldest is not None else None,
            "rows_per_day": [{"day": d, "rows": n} for d, n in per_day],
            "rows_last_24h": q("SELECT COUNT(*) FROM history WHERE last_updated >= ?", (day_ago,)).fetchone()[0],
            "top_entities_24h": [{"entity_id": e, "rows": n} for e, n in top],
        }

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

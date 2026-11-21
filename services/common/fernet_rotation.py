"""Re-encrypt everything sealed with the old ``FERNET_KEY`` under the new one.

Run inside each service's container (it has the database and the keys), by
``scripts/rotate_fernet_key.sh``::

    python -m services.common.fernet_rotation --store identity --mode plan
    python -m services.common.fernet_rotation --store identity --mode apply
    python -m services.common.fernet_rotation --store identity --mode verify

Stores (every place the stack seals with ``FERNET_KEY``):

=============  =====================================================================
``identity``   ``IDENTITY_DATABASE_URL`` (SQLite): every text column of every table is
               scanned, so a new ``*_enc`` column or an encrypted setting is never missed.
               ``user.api_key_hash`` is re-computed from the re-encrypted key, because it
               is an HMAC keyed with ``FERNET_KEY``.
``workspace``  ``WORKSPACE_DATABASE_URL`` (SQLite): workspace env and webhook secrets.
``homehub``    ``HOMEHUB_DB`` (SQLite): sealed config entries, refresh-token keys, blobs.
``telemetry``  ``REDIS_URL``: concern images (``tel:concern-image:*``), TTL kept.
=============  =====================================================================

Modes:

* ``plan``   reads only. Counts the tokens per column and how many open with the primary
  key alone, with a previous key, or with none. Run it before anything changes: with no
  previous key set it proves the *current* key opens every stored value.
* ``apply``  re-encrypts every token that does not already open with the primary key, one
  database transaction (SQLite) per store, so a failure leaves it as it was. Re-running is
  safe: what is already under the new key is skipped, so an interrupted run resumes.
* ``verify`` reads only, and fails unless every token opens with the primary key alone,
  i.e. the previous key can be dropped.

A token no key opens is reported by place (table, column, row) and fails the run: it was
already unreadable, and dropping the old key must never be what hides that. ``--allow-
undecryptable`` accepts the listed ones after a person has looked at them.

Nothing here prints a key, a token or a decrypted value. Output is counts and places.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from cryptography.fernet import InvalidToken

from services.common.fernet_keys import KeyRing, hmac_digest, looks_like_token

STORES = ("identity", "workspace", "homehub", "telemetry")
TELEMETRY_PREFIXES = ("tel:concern-image:",)


@dataclass
class ColumnCount:
    tokens: int = 0
    primary: int = 0
    previous: int = 0
    undecryptable: int = 0
    rotated: int = 0


@dataclass
class Report:
    store: str
    mode: str
    columns: dict[str, ColumnCount] = field(default_factory=dict)
    undecryptable: list[str] = field(default_factory=list)
    rehashed: int = 0
    notes: list[str] = field(default_factory=list)

    def count(self, place: str) -> ColumnCount:
        return self.columns.setdefault(place, ColumnCount())

    @property
    def totals(self) -> ColumnCount:
        t = ColumnCount()
        for c in self.columns.values():
            t.tokens += c.tokens
            t.primary += c.primary
            t.previous += c.previous
            t.undecryptable += c.undecryptable
            t.rotated += c.rotated
        return t

    def as_dict(self) -> dict[str, Any]:
        t = self.totals
        return {
            "store": self.store,
            "mode": self.mode,
            "totals": vars(t),
            "columns": {k: vars(v) for k, v in sorted(self.columns.items())},
            "undecryptable": self.undecryptable,
            "rehashed": self.rehashed,
            "notes": self.notes,
        }


def _classify(ring: KeyRing, token: bytes) -> str:
    if ring.opens_with_primary(token):
        return "primary"
    try:
        ring.decrypt(token)
    except InvalidToken:
        return "undecryptable"
    return "previous"


def _to_bytes(value: Any) -> bytes:
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value)
    return str(value).encode()


def _like_stored(original: Any, token: bytes) -> Any:
    """The rotated token in the stored value's own type (text stays text, blob stays blob)."""
    return token if isinstance(original, (bytes, bytearray, memoryview)) else token.decode()


# -- SQLite -------------------------------------------------------------------------

def sqlite_path(url_or_path: str) -> str:
    """``sqlite:////data/x.db`` or a plain path -> the file path. Anything else is refused."""
    value = (url_or_path or "").strip()
    if value.startswith("sqlite:///"):
        return value[len("sqlite:///"):]
    if "://" in value:
        raise SystemExit(f"only SQLite databases are supported here, not {value.split('://', 1)[0]}")
    if not value:
        raise SystemExit("no database path configured")
    return value


def _text_columns(db: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in db.execute(f'PRAGMA table_info("{table}")').fetchall()]


def _tables(db: sqlite3.Connection) -> list[str]:
    rows = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
    return sorted(r[0] for r in rows)


def _token_rows(db: sqlite3.Connection, table: str, column: str) -> Iterator[tuple[int, Any]]:
    sql = (f'SELECT rowid, "{column}" FROM "{table}" WHERE typeof("{column}") IN (\'text\', \'blob\') '
           f'AND CAST(substr("{column}", 1, 6) AS TEXT) = \'gAAAAA\'')
    try:
        yield from db.execute(sql).fetchall()
    except sqlite3.OperationalError as exc:  # a WITHOUT ROWID table: no rowid to address a row by
        raise SystemExit(f"cannot scan {table}.{column}: {exc}") from exc


def rotate_sqlite(path: str, ring: KeyRing, mode: str, store: str,
                  tables: tuple[str, ...] | None = None) -> Report:
    if not os.path.exists(path):
        raise SystemExit(f"{store}: database not found at {path}")
    report = Report(store=store, mode=mode)
    db = sqlite3.connect(path, timeout=30.0, isolation_level=None)
    try:
        db.execute("PRAGMA busy_timeout=30000")
        if mode == "apply":
            # One write transaction: the service's own writes wait, and a failure undoes everything.
            db.execute("BEGIN IMMEDIATE")
        for table in tables or _tables(db):
            for column in _text_columns(db, table):
                for rowid, value in _token_rows(db, table, column):
                    if not looks_like_token(value):
                        continue
                    place = f"{table}.{column}"
                    counts = report.count(place)
                    counts.tokens += 1
                    token = _to_bytes(value)
                    kind = _classify(ring, token)
                    setattr(counts, kind, getattr(counts, kind) + 1)
                    if kind == "undecryptable":
                        report.undecryptable.append(f"{place} rowid={rowid}")
                    elif kind == "previous" and mode == "apply":
                        new = ring.rotate(token)
                        db.execute(f'UPDATE "{table}" SET "{column}" = ? WHERE rowid = ?',
                                   (_like_stored(value, new), rowid))
                        counts.rotated += 1
        if store == "identity":
            report.rehashed = _rehash_user_api_keys(db, ring, mode)
            if mode != "plan":
                stale = _stale_lookup_hashes(db)
                if stale:
                    report.notes.append(
                        f"{stale} extra API key(s) or PC agent key(s) are stored only as a hash keyed with "
                        "FERNET_KEY. One made before the rotation is re-hashed on its next use while "
                        "FERNET_KEY_PREVIOUS is set; one not used before the old key is dropped stops working "
                        "(the sign-in reset, scripts/reset_sign_ins.sh, re-issues them all anyway)."
                    )
        if mode == "apply":
            db.execute("COMMIT")
    except BaseException:
        if mode == "apply" and db.in_transaction:
            db.execute("ROLLBACK")
        raise
    finally:
        db.close()
    return report


def _rehash_user_api_keys(db: sqlite3.Connection, ring: KeyRing, mode: str) -> int:
    """``user.api_key_hash`` is an HMAC keyed with FERNET_KEY: re-compute it from the stored key."""
    cols = set(_text_columns(db, "user")) if "user" in _tables(db) else set()
    if not {"api_key_enc", "api_key_hash"} <= cols:
        return 0
    changed = 0
    for rowid, enc, current in db.execute(
        'SELECT rowid, api_key_enc, api_key_hash FROM "user" WHERE api_key_enc IS NOT NULL AND api_key_enc != \'\''
    ).fetchall():
        try:
            plain = ring.decrypt(_to_bytes(enc)).decode()
        except InvalidToken:
            continue  # already reported as undecryptable
        wanted = ring.digest(plain)
        if wanted != current:
            changed += 1
            if mode == "apply":
                db.execute('UPDATE "user" SET api_key_hash = ? WHERE rowid = ?', (wanted, rowid))
    return changed


def _stale_lookup_hashes(db: sqlite3.Connection) -> int:
    """Hashes with no stored plaintext to re-compute them from (only counted, see the note)."""
    total = 0
    tables = set(_tables(db))
    if "apikey" in tables:
        total += db.execute("SELECT COUNT(*) FROM apikey WHERE key_hash IS NOT NULL AND key_hash != ''").fetchone()[0]
    if "device" in tables and "agent_key_hash" in _text_columns(db, "device"):
        total += db.execute("SELECT COUNT(*) FROM device WHERE agent_key_hash != ''").fetchone()[0]
    return total


# -- Redis --------------------------------------------------------------------------

def rotate_redis(url: str, ring: KeyRing, mode: str, prefixes: tuple[str, ...] = TELEMETRY_PREFIXES,
                 client: Any = None) -> Report:
    report = Report(store="telemetry", mode=mode)
    if client is None:
        import redis

        client = redis.Redis.from_url(url)
    for prefix in prefixes:
        place = f"{prefix}*"
        for key in client.scan_iter(match=f"{prefix}*", count=500):
            value = client.get(key)
            if value is None or not looks_like_token(value):
                continue
            counts = report.count(place)
            counts.tokens += 1
            token = _to_bytes(value)
            kind = _classify(ring, token)
            setattr(counts, kind, getattr(counts, kind) + 1)
            name = key.decode() if isinstance(key, bytes) else str(key)
            if kind == "undecryptable":
                report.undecryptable.append(f"redis key {name}")
            elif kind == "previous" and mode == "apply":
                # KEEPTTL: a concern image still expires when it was going to.
                client.set(key, ring.rotate(token), keepttl=True)
                counts.rotated += 1
    return report


# -- entry point --------------------------------------------------------------------

def store_sqlite_path(store: str, env: dict[str, str]) -> str:
    keys = {"identity": "IDENTITY_DATABASE_URL", "workspace": "WORKSPACE_DATABASE_URL", "homehub": "HOMEHUB_DB"}
    if store not in keys:
        raise SystemExit(f"{store} is not a SQLite store")
    return sqlite_path(env.get(keys[store], ""))


def backup_sqlite(store: str, dest: str, env: dict[str, str] | None = None) -> int:
    """A consistent copy of the store's database (SQLite's online backup), mode 600. Returns bytes."""
    env = dict(os.environ if env is None else env)
    src_path = store_sqlite_path(store, env)
    if not os.path.exists(src_path):
        raise SystemExit(f"{store}: database not found at {src_path}")
    old_umask = os.umask(0o077)
    try:
        src = sqlite3.connect(src_path, timeout=30.0)
        out = sqlite3.connect(dest)
        try:
            src.backup(out)
        finally:
            out.close()
            src.close()
    finally:
        os.umask(old_umask)
    os.chmod(dest, 0o600)
    return os.path.getsize(dest)


def run(store: str, mode: str, ring: KeyRing | None = None, env: dict[str, str] | None = None) -> Report:
    env = dict(os.environ if env is None else env)
    ring = ring or KeyRing.from_values(env.get("FERNET_KEY"), env.get("FERNET_KEY_PREVIOUS"))
    if store == "identity":
        return rotate_sqlite(sqlite_path(env.get("IDENTITY_DATABASE_URL", "")), ring, mode, store)
    if store == "workspace":
        return rotate_sqlite(sqlite_path(env.get("WORKSPACE_DATABASE_URL", "")), ring, mode, store)
    if store == "homehub":
        return rotate_sqlite(sqlite_path(env.get("HOMEHUB_DB", "")), ring, mode, store,
                             tables=("config_entries", "refresh_tokens", "blobs"))
    if store == "telemetry":
        url = env.get("REDIS_URL", "")
        if not url:
            raise SystemExit("telemetry: REDIS_URL is not set")
        return rotate_redis(url, ring, mode)
    raise SystemExit(f"unknown store {store!r}; one of {', '.join(STORES)}")


def verdict(report: Report, allow_undecryptable: bool = False) -> tuple[int, str]:
    """(exit status, one line) for a report."""
    t = report.totals
    if report.undecryptable and not allow_undecryptable:
        return 3, (f"{report.store}: {len(report.undecryptable)} value(s) open with no key; nothing can be "
                   "dropped until someone has looked at them")
    if report.mode == "verify" and (t.previous or (report.rehashed and report.store == "identity")):
        return 4, (f"{report.store}: {t.previous} value(s) still need the previous key"
                   f"{' and ' + str(report.rehashed) + ' key hash(es) are stale' if report.rehashed else ''}; "
                   "run --mode apply")
    if report.mode == "plan":
        return 0, (f"{report.store}: {t.tokens} sealed value(s): {t.primary} under the current key, "
                   f"{t.previous} under a previous key, {t.undecryptable} unreadable")
    if report.mode == "apply":
        return 0, f"{report.store}: re-encrypted {t.rotated} value(s), re-hashed {report.rehashed} key(s)"
    return 0, f"{report.store}: all {t.tokens} sealed value(s) open with the current key alone"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--store", required=True, choices=STORES)
    parser.add_argument("--mode", choices=("plan", "apply", "verify"))
    parser.add_argument("--backup-to", metavar="PATH",
                        help="write a consistent copy of the store's SQLite database to PATH (mode 600) and exit")
    parser.add_argument("--allow-undecryptable", action="store_true",
                        help="accept values no key opens (listed in the output) after checking them")
    parser.add_argument("--json", action="store_true", help="print the full report as JSON (still no values)")
    parser.add_argument("--fingerprint", action="store_true",
                        help="print only the one-way fingerprints of the keys this process holds, then exit")
    args = parser.parse_args(argv)
    if args.backup_to:
        size = backup_sqlite(args.store, args.backup_to)
        print(f"{args.store}: backed up {size} bytes")
        return 0
    ring = KeyRing.from_values(os.getenv("FERNET_KEY"), os.getenv("FERNET_KEY_PREVIOUS"))
    if args.fingerprint:
        print(json.dumps(ring.fingerprints()))
        return 0
    if not args.mode:
        parser.error("--mode is required")
    report = run(args.store, args.mode, ring)
    status, line = verdict(report, args.allow_undecryptable)
    if args.json:
        print(json.dumps(report.as_dict(), indent=2))
    for place in report.undecryptable[:50]:
        print(f"  unreadable: {place}", file=sys.stderr)
    for note in report.notes:
        print(f"  note: {note}", file=sys.stderr)
    print(line)
    return status


__all__ = ["Report", "backup_sqlite", "hmac_digest", "main", "rotate_redis", "rotate_sqlite", "run", "verdict"]

if __name__ == "__main__":
    raise SystemExit(main())

# services/execution/device_registry.py
"""
Persistent device IP/MAC/hostname registry for TV/media devices.

Uses aiosqlite (async SQLite) for non-blocking I/O with WAL mode for
fast concurrent reads and crash-safe persistence.

Storage: /data/device_registry.db (volume-mounted as execution_data).

Schema:
  devices (
    entity_id TEXT PRIMARY KEY,
    ip TEXT, mac TEXT, hostname TEXT,
    integration TEXT, friendly_name TEXT, device_class TEXT,
    discovery_method TEXT, metadata TEXT (JSON),
    last_seen REAL, last_verified REAL, last_updated REAL,
    ip_stale INT, ip_stale_reason TEXT, ip_stale_at REAL
  )
  Indexes: idx_ip, idx_mac, idx_integration, idx_friendly_name
"""
import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

import aiosqlite

log = logging.getLogger("execution.device_registry")

DB_PATH_ENV = "DEVICE_REGISTRY_PATH"
DB_PATH_DEFAULT = "/data/device_registry.db"
_db: Optional[aiosqlite.Connection] = None


async def _get_conn() -> aiosqlite.Connection:
    global _db
    if _db is None:
        db_path = os.environ.get(DB_PATH_ENV, DB_PATH_DEFAULT)
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        _db = await aiosqlite.connect(str(path))
        await _db.execute("PRAGMA journal_mode=WAL")
        await _db.execute("PRAGMA synchronous=NORMAL")
        await _db.execute("PRAGMA cache_size=-64000")
        await _db.execute("""
            CREATE TABLE IF NOT EXISTS devices (
                entity_id TEXT PRIMARY KEY,
                ip TEXT, mac TEXT, hostname TEXT,
                integration TEXT, friendly_name TEXT, device_class TEXT,
                discovery_method TEXT, metadata TEXT,
                last_seen REAL, last_verified REAL, last_updated REAL,
                ip_stale INTEGER DEFAULT 0,
                ip_stale_reason TEXT, ip_stale_at REAL
            )
        """)
        await _db.execute("CREATE INDEX IF NOT EXISTS idx_ip ON devices(ip)")
        await _db.execute("CREATE INDEX IF NOT EXISTS idx_mac ON devices(mac)")
        await _db.execute("CREATE INDEX IF NOT EXISTS idx_integration ON devices(integration)")
        await _db.execute("CREATE INDEX IF NOT EXISTS idx_friendly ON devices(friendly_name)")
        await _db.commit()
    return _db


def _row_to_dict(row: tuple, keys: list) -> Optional[dict]:
    if row is None:
        return None
    d = dict(zip(keys, row))
    if d.get("metadata"):
        try:
            d["metadata"] = json.loads(d["metadata"])
        except Exception:
            d["metadata"] = {}
    return d


COLUMNS = [
    "entity_id", "ip", "mac", "hostname", "integration", "friendly_name",
    "device_class", "discovery_method", "metadata",
    "last_seen", "last_verified", "last_updated",
    "ip_stale", "ip_stale_reason", "ip_stale_at",
]


async def get_device(entity_id: str) -> Optional[dict]:
    """Get stored device info for an entity (fast indexed lookup)."""
    db = await _get_conn()
    async with db.execute(
        "SELECT * FROM devices WHERE entity_id = ?", (entity_id,)
    ) as cursor:
        row = await cursor.fetchone()
    return _row_to_dict(row, COLUMNS)


async def set_device(
    entity_id: str,
    ip: Optional[str] = None,
    mac: Optional[str] = None,
    hostname: Optional[str] = None,
    integration: Optional[str] = None,
    friendly_name: Optional[str] = None,
    device_class: Optional[str] = None,
    metadata: Optional[dict] = None,
    discovery_method: Optional[str] = None,
) -> dict:
    """Store or update device info. Returns the updated device record."""
    db = await _get_conn()
    existing = await get_device(entity_id)
    now = time.time()

    if existing:
        d = existing
        if ip:
            d["ip"] = ip
            d["last_seen"] = now
            d["last_verified"] = now
        if mac:
            d["mac"] = mac
        if hostname:
            d["hostname"] = hostname
        if integration:
            d["integration"] = integration
        if friendly_name:
            d["friendly_name"] = friendly_name
        if device_class:
            d["device_class"] = device_class
        if metadata:
            old_meta = d.get("metadata") or {}
            old_meta.update(metadata)
            d["metadata"] = old_meta
        if discovery_method:
            d["discovery_method"] = discovery_method
        d["last_updated"] = now

        meta_json = json.dumps(d.get("metadata")) if d.get("metadata") else None
        await db.execute("""
            UPDATE devices SET
                ip=?, mac=?, hostname=?, integration=?, friendly_name=?,
                device_class=?, metadata=?, discovery_method=?,
                last_seen=?, last_verified=?, last_updated=?
            WHERE entity_id=?
        """, (
            d.get("ip"), d.get("mac"), d.get("hostname"),
            d.get("integration"), d.get("friendly_name"),
            d.get("device_class"), meta_json,
            d.get("discovery_method"),
            d.get("last_seen"), d.get("last_verified"),
            d.get("last_updated"), entity_id,
        ))
    else:
        meta_json = json.dumps(metadata) if metadata else None
        await db.execute("""
            INSERT INTO devices (
                entity_id, ip, mac, hostname, integration, friendly_name,
                device_class, metadata, discovery_method,
                last_seen, last_verified, last_updated
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            entity_id, ip, mac, hostname, integration, friendly_name,
            device_class, meta_json, discovery_method,
            now if ip else None, now if ip else None, now,
        ))

    await db.commit()
    return await get_device(entity_id)


async def invalidate_device(entity_id: str, reason: str = "connection_failed") -> None:
    """Mark cached IP as stale (call on connection errors)."""
    db = await _get_conn()
    await db.execute("""
        UPDATE devices SET ip_stale=1, ip_stale_reason=?, ip_stale_at=?
        WHERE entity_id=?
    """, (reason, time.time(), entity_id))
    await db.commit()


async def clear_stale(entity_id: str) -> None:
    """Remove stale flag after successful re-discovery."""
    db = await _get_conn()
    await db.execute("""
        UPDATE devices SET ip_stale=0, ip_stale_reason=NULL, ip_stale_at=NULL
        WHERE entity_id=?
    """, (entity_id,))
    await db.commit()


async def list_devices() -> dict:
    """Return all registered devices as {entity_id: {info}}."""
    db = await _get_conn()
    async with db.execute("SELECT * FROM devices ORDER BY last_updated DESC") as cursor:
        rows = await cursor.fetchall()
    return {row[0]: _row_to_dict(row, COLUMNS) for row in rows}


async def find_by_ip(ip: str) -> Optional[str]:
    """Find entity_id by IP address (indexed)."""
    db = await _get_conn()
    async with db.execute(
        "SELECT entity_id FROM devices WHERE ip = ?", (ip,)
    ) as cursor:
        row = await cursor.fetchone()
    return row[0] if row else None


async def find_by_mac(mac: str) -> Optional[str]:
    """Find entity_id by MAC address (indexed)."""
    db = await _get_conn()
    async with db.execute(
        "SELECT entity_id FROM devices WHERE LOWER(mac) = LOWER(?)", (mac,)
    ) as cursor:
        row = await cursor.fetchone()
    return row[0] if row else None


async def remove_device(entity_id: str) -> bool:
    """Remove a device from the registry."""
    db = await _get_conn()
    cursor = await db.execute("DELETE FROM devices WHERE entity_id = ?", (entity_id,))
    await db.commit()
    return cursor.rowcount > 0


async def needs_rediscovery(entity_id: str, max_age_seconds: int = 86400) -> bool:
    """Check if a device needs re-discovery (stale or never discovered)."""
    device = await get_device(entity_id)
    if not device:
        return True
    if device.get("ip_stale"):
        return True
    last_verified = device.get("last_verified") or 0
    return (time.time() - last_verified) > max_age_seconds


async def search_devices(query: str) -> list[dict]:
    """Search devices by friendly_name, entity_id, or integration."""
    db = await _get_conn()
    pattern = f"%{query.lower()}%"
    async with db.execute("""
        SELECT * FROM devices
        WHERE LOWER(friendly_name) LIKE ?
           OR LOWER(entity_id) LIKE ?
           OR LOWER(integration) LIKE ?
           OR LOWER(hostname) LIKE ?
        ORDER BY last_updated DESC
    """, (pattern, pattern, pattern, pattern)) as cursor:
        rows = await cursor.fetchall()
    return [_row_to_dict(r, COLUMNS) for r in rows]

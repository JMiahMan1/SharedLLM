# services/execution/media_playback_registry.py
"""
Persistent storage for user media states, queues, and active playback targets.
Uses aiosqlite for async database access to /data/device_registry.db.
"""
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import aiosqlite
import pytz

log = logging.getLogger("execution.media_playback_registry")

DB_PATH_ENV = "DEVICE_REGISTRY_PATH"
DB_PATH_DEFAULT = "/data/device_registry.db"
_db: aiosqlite.Connection | None = None
AZ_TZ = pytz.timezone("America/Phoenix")

async def _get_conn() -> aiosqlite.Connection:
    global _db
    if _db is None:
        db_path = os.environ.get(DB_PATH_ENV, DB_PATH_DEFAULT)
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        _db = await aiosqlite.connect(str(path))
        await _db.execute("PRAGMA journal_mode=WAL")
        await _db.execute("PRAGMA synchronous=NORMAL")

        # Initialize tables
        await _db.execute("""
            CREATE TABLE IF NOT EXISTS media_playback_states (
                username TEXT PRIMARY KEY,
                entity_id TEXT,
                state TEXT,
                media_type TEXT,
                query TEXT,
                media_content_id TEXT,
                position REAL DEFAULT 0.0,
                duration REAL DEFAULT 0.0,
                volume_level REAL,
                is_volume_muted INTEGER DEFAULT 0,
                media_title TEXT,
                media_artist TEXT,
                media_album TEXT,
                queue TEXT,
                updated_at TEXT
            )
        """)
        await _db.commit()
    return _db

def get_az_timestamp_str() -> str:
    """Get ISO format timestamp in America/Phoenix timezone."""
    return datetime.now(AZ_TZ).isoformat()

async def get_playback_state(username: str) -> dict[str, Any] | None:
    """Retrieve the persisted media playback state for a user."""
    db = await _get_conn()
    try:
        async with db.execute(
            "SELECT * FROM media_playback_states WHERE username = ?", (username,)
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None

            # Map columns to dictionary
            cols = [
                "username", "entity_id", "state", "media_type", "query",
                "media_content_id", "position", "duration", "volume_level",
                "is_volume_muted", "media_title", "media_artist", "media_album",
                "queue", "updated_at"
            ]
            data = dict(zip(cols, row))
            if data.get("queue"):
                try:
                    data["queue"] = json.loads(data["queue"])
                except Exception:
                    data["queue"] = []
            else:
                data["queue"] = []

            data["is_volume_muted"] = bool(data["is_volume_muted"])
            return data
    except Exception as e:
        log.error(f"[media_playback_registry] Failed to get playback state: {e}", exc_info=True)
        return None

async def save_playback_state(username: str, data: dict[str, Any]) -> bool:
    """Save/update the media playback state for a user."""
    db = await _get_conn()
    try:
        now_str = get_az_timestamp_str()
        queue_json = json.dumps(data.get("queue", [])) if "queue" in data else None
        is_muted_val = 1 if data.get("is_volume_muted") else 0

        # Check if row exists
        existing = await get_playback_state(username)
        if existing:
            # Update fields dynamically
            fields_to_update = []
            params = []
            updatable = [
                "entity_id", "state", "media_type", "query", "media_content_id",
                "position", "duration", "volume_level", "is_volume_muted",
                "media_title", "media_artist", "media_album", "queue"
            ]
            for key in updatable:
                if key in data:
                    fields_to_update.append(f"{key}=?")
                    if key == "queue":
                        params.append(queue_json)
                    elif key == "is_volume_muted":
                        params.append(is_muted_val)
                    else:
                        params.append(data[key])

            fields_to_update.append("updated_at=?")
            params.append(now_str)
            params.append(username)

            query = f"UPDATE media_playback_states SET {', '.join(fields_to_update)} WHERE username=?"
            await db.execute(query, tuple(params))
        else:
            # Insert new row
            await db.execute("""
                INSERT INTO media_playback_states (
                    username, entity_id, state, media_type, query, media_content_id,
                    position, duration, volume_level, is_volume_muted,
                    media_title, media_artist, media_album, queue, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                username,
                data.get("entity_id", "local"),
                data.get("state", "idle"),
                data.get("media_type"),
                data.get("query"),
                data.get("media_content_id"),
                data.get("position", 0.0),
                data.get("duration", 0.0),
                data.get("volume_level", 0.5),
                is_muted_val,
                data.get("media_title"),
                data.get("media_artist"),
                data.get("media_album"),
                queue_json or "[]",
                now_str
            ))

        await db.commit()
        log.info(f"[media_playback_registry] Saved state for {username} (America/Phoenix timestamp: {now_str})")
        return True
    except Exception as e:
        log.error(f"[media_playback_registry] Failed to save playback state: {e}", exc_info=True)
        return False

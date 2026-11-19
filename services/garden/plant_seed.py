"""Read-only access to the Jarvis Garden botanical seed database.

The seed is built by ``scripts/build_plants_seed.py`` from curated JSON
catalog files under ``data/plants_seed/`` and lives beside the garden
database (``PLANT_SEED_PATH`` overrides). It holds per-species care and
safety profiles plus an FTS5 full-text index over names, scientific names,
descriptions, and culinary aliases.

A missing or unreadable seed is a normal state, not an error: the service
logs one line and answers searches without it. Values not reported stay
``None`` all the way to the UI, never zero.
"""
from __future__ import annotations

import logging
import re
import sqlite3
from pathlib import Path

log = logging.getLogger("garden.plant_seed")

_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")
_MAX_TOKENS = 8


class PlantSeed:
    """Lazily opened, read-only view of the botanical seed."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._conn: sqlite3.Connection | None = None
        self._absent = False

    @property
    def present(self) -> bool:
        return self._connect() is not None

    def _connect(self) -> sqlite3.Connection | None:
        if self._conn is not None:
            return self._conn
        if self._absent:
            return None
        try:
            conn = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("SELECT key FROM plants LIMIT 1")
        except sqlite3.Error as exc:
            # One line at first use; every later search just skips the seed.
            log.info(
                "[Garden] Plant seed not available at %s (%s); searches run without it",
                self.path,
                exc,
            )
            self._absent = True
            return None
        self._conn = conn
        return conn

    def _where_with_filters(
        self, base_where: str, params: list, category: str | None, usda_zone: int | None
    ) -> tuple[str, list]:
        """Append optional category / usda_zone filters to a WHERE clause."""
        where = base_where
        if category is not None:
            where += " AND p.category = ?"
            params.append(category)
        if usda_zone is not None:
            # Zone is suitable if usda_zone_min <= zone <= usda_zone_max.
            where += " AND p.usda_zone_min IS NOT NULL AND p.usda_zone_max IS NOT NULL "
            where += "AND p.usda_zone_min <= ? AND p.usda_zone_max >= ?"
            params.extend([usda_zone, usda_zone])
        return where, params

    def search(
        self,
        query: str,
        limit: int,
        category: str | None = None,
        usda_zone: int | None = None,
    ) -> list[dict]:
        """Full-text search over plant names and descriptions, best match first.

        Optional ``category`` and ``usda_zone`` filters narrow results.
        """
        conn = self._connect()
        if conn is None or limit <= 0:
            return []
        tokens = _TOKEN_RE.findall(query)[:_MAX_TOKENS]
        if not tokens:
            return []
        match = " AND ".join(f"{token}*" for token in tokens)
        params: list = [match, limit]
        where = "WHERE plant_fts MATCH ?"
        where, params = self._where_with_filters(where, params[:-1], category, usda_zone)
        params.append(limit)
        rows = conn.execute(
            "SELECT p.* FROM plant_fts "
            "JOIN plants p ON p.key = plant_fts.key "
            f"{where} ORDER BY bm25(plant_fts) LIMIT ?",
            tuple(params),
        ).fetchall()
        return [dict(row) for row in rows]

    def get(self, key: str) -> dict | None:
        """Fetch one species by its catalog key."""
        conn = self._connect()
        if conn is None:
            return None
        row = conn.execute(
            "SELECT p.* FROM plants p WHERE p.key = ?",
            (key,),
        ).fetchone()
        return dict(row) if row is not None else None

    def list_by_category(
        self,
        category: str,
        limit: int = 100,
        usda_zone: int | None = None,
    ) -> list[dict]:
        """List species in a category, ordered by common name.

        Optional ``usda_zone`` filters to zones suitable for that zone.
        """
        conn = self._connect()
        if conn is None:
            return []
        params: list = [category]
        where = "WHERE category = ?"
        if usda_zone is not None:
            where += (
                " AND usda_zone_min IS NOT NULL AND usda_zone_max IS NOT NULL "
                "AND usda_zone_min <= ? AND usda_zone_max >= ?"
            )
            params.extend([usda_zone, usda_zone])
        params.append(limit)
        rows = conn.execute(
            "SELECT p.* FROM plants p " + where +
            " ORDER BY p.common_name LIMIT ?",
            tuple(params),
        ).fetchall()
        return [dict(row) for row in rows]

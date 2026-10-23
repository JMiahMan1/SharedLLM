"""Read-only access to the USDA FoodData Central seed database.

The seed is built by ``scripts/build_fdc_seed.py`` from USDA's bulk downloads
(Foundation Foods, SR Legacy, FNDDS) and lives outside the image, beside the
health database (``FDC_SEED_PATH`` overrides). It holds per-100 g nutrients and
FNDDS household portions ("1 cup, shredded" = 105 g), which is what turns a
search hit into a loggable serving.

A missing or unreadable seed is a normal state, not an error: the service logs
one line and answers searches from OpenFoodFacts alone. Values USDA does not
report stay ``None`` all the way to the UI, never zero.
"""
from __future__ import annotations

import logging
import re
import sqlite3
from pathlib import Path

log = logging.getLogger("health.food_seed")

_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")
_MAX_TOKENS = 8

# seed column -> API key in the ``per_100g`` block
_COLUMNS = {
    "calories": "calories_kcal",
    "protein_g": "protein_g",
    "carbs_g": "carbs_g",
    "fat_g": "fat_g",
    "fiber_g": "fiber_g",
    "sugar_g": "sugar_g",
    "sodium_mg": "sodium_mg",
}


class FoodSeed:
    """Lazily opened, read-only view of the USDA seed."""

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
            conn.execute("SELECT key FROM foods LIMIT 1")
        except sqlite3.Error as exc:
            # One line at first use; every later search just skips the seed.
            log.info(
                "[Health] USDA food seed not available at %s (%s); searches use OpenFoodFacts only", self.path, exc
            )
            self._absent = True
            return None
        self._conn = conn
        return conn

    def search(self, query: str, limit: int) -> list[dict]:
        """Full-text search over food descriptions, best match first."""
        conn = self._connect()
        if conn is None or limit <= 0:
            return []
        tokens = _TOKEN_RE.findall(query)[:_MAX_TOKENS]
        if not tokens:
            return []
        match = " AND ".join(f"{token}*" for token in tokens)
        columns = ", ".join(f"f.{column}" for column in _COLUMNS.values())
        rows = conn.execute(
            f"SELECT f.key, f.description, {columns} FROM food_fts "
            "JOIN foods f ON f.key = food_fts.key "
            "WHERE food_fts MATCH ? ORDER BY bm25(food_fts) LIMIT ?",
            (match, limit),
        ).fetchall()
        portions = self._portions(conn, [row["key"] for row in rows])
        return [
            {
                "code": row["key"],
                "name": row["description"],
                "brand": "",
                "image": None,
                "serving_size": None,
                "serving_grams": None,
                "per_100g": {api: row[column] for api, column in _COLUMNS.items()},
                "portions": portions.get(row["key"], []),
                "source": "usda_fdc",
            }
            for row in rows
        ]

    @staticmethod
    def _portions(conn: sqlite3.Connection, keys: list[str]) -> dict[str, list[dict]]:
        if not keys:
            return {}
        marks = ", ".join("?" for _ in keys)
        out: dict[str, list[dict]] = {}
        for row in conn.execute(
            f"SELECT food_key, label, grams FROM portions WHERE food_key IN ({marks}) ORDER BY food_key, seq",
            keys,
        ):
            out.setdefault(row["food_key"], []).append({"label": row["label"], "grams": row["grams"]})
        return out

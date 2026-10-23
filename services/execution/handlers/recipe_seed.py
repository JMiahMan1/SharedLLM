"""The offline recipe index: a read-only SQLite over the whole CC0 Food.com set.

The Recipes page searches this index first — instant and fully offline — and a
pick saves without touching the network. The file lives in the server data dir
(``data/recipes/recipe-seed.db``) and is built by ``scripts/build_recipe_seed.py``.

Optional by design: a missing file only ever loses the offline results (one
log line, search falls back to TheMealDB, every other shelf feature keeps
working). Opened lazily and read-only.
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3

log = logging.getLogger("execution.recipe_seed")

DEFAULT_SEED_PATH = "/data/recipes/recipe-seed.db"
_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")
_SEED_CACHE: dict[str, "RecipeSeed"] = {}


def seed_path() -> str:
    """Where the seed lives: ``RECIPE_SEED_PATH`` or the server data dir."""
    return os.environ.get("RECIPE_SEED_PATH") or DEFAULT_SEED_PATH


def get_seed() -> "RecipeSeed":
    """The shared reader for the configured seed path."""
    path = seed_path()
    seed = _SEED_CACHE.get(path)
    if seed is None:
        seed = RecipeSeed(path)
        _SEED_CACHE[path] = seed
    return seed


class RecipeSeed:
    """Lazy read-only view of the seed; every call is safe when it is absent."""

    def __init__(self, path: str):
        self.path = path
        self._conn: sqlite3.Connection | None = None
        self._absent = False

    @property
    def present(self) -> bool:
        if self._absent:
            return False
        if self._conn is not None:
            return True
        if not os.path.exists(self.path):
            self._mark_absent("file not found")
            return False
        try:
            self._conn = sqlite3.connect(
                f"file:{self.path}?mode=ro", uri=True, check_same_thread=False
            )
            self._conn.execute("SELECT id FROM recipes LIMIT 1").fetchone()
            return True
        except sqlite3.Error as exc:
            self._mark_absent(str(exc))
            return False

    def _mark_absent(self, detail: str) -> None:
        self._absent = True
        self._conn = None
        log.info(
            f"[recipe-seed] offline recipe index not available at {self.path} "
            f"({detail}); search falls back to TheMealDB"
        )

    def search(self, query: str, limit: int = 12) -> list[dict]:
        """Result cards for a query, best match first; ``[]`` when absent."""
        if not query or not self.present:
            return []
        tokens = _TOKEN_RE.findall(query.lower())[:8]
        if not tokens:
            return []
        match = " AND ".join(f"{token}*" for token in tokens)
        try:
            rows = self._conn.execute(
                """
                SELECT r.id, r.name, r.category, r.total_minutes, r.servings,
                       r.rating, r.image, r.source_url
                FROM recipe_fts f
                JOIN recipes r ON r.id = f.rowid
                WHERE recipe_fts MATCH ?
                ORDER BY bm25(recipe_fts)
                LIMIT ?
                """,
                (match, limit),
            ).fetchall()
        except sqlite3.Error as exc:
            log.warning(f"[recipe-seed] search failed: {exc}")
            return []
        return [
            {
                "id": row[0],
                "title": row[1],
                "category": row[2] or "",
                "total_minutes": row[3],
                "servings": row[4],
                "rating": row[5],
                "image": row[6] or None,
                "source_url": row[7],
                "site": "food.com",
                "source": "foodcom",
            }
            for row in rows
        ]

    def fetch(self, recipe_id: int) -> dict | None:
        """The full recipe in the shape the app saves, or ``None``."""
        if not self.present:
            return None
        try:
            row = self._conn.execute(
                "SELECT id, name, category, ingredients, instructions, "
                "prep_minutes, cook_minutes, total_minutes, servings, image, "
                "source_url FROM recipes WHERE id = ?",
                (recipe_id,),
            ).fetchone()
        except sqlite3.Error as exc:
            log.warning(f"[recipe-seed] fetch failed: {exc}")
            return None
        if not row:
            return None
        return {
            "title": row[1],
            "description": row[2] or "",
            "ingredients": (row[3] or "").split("\n"),
            "instructions": (row[4] or "").split("\n"),
            "prep_minutes": row[5],
            "cook_minutes": row[6],
            "total_minutes": row[7],
            "yield": f"{row[8]} servings" if row[8] else "",
            "image": row[9] or None,
            "source_url": row[10],
            "site": "food.com",
        }

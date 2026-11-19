"""Tests for scripts/build_plants_seed.py.

The build script reads curated JSON from ``data/plants_seed/*.json`` (no
network, no downloads) and asserts schema, enum, safety, and per-category
count invariants before atomically replacing the output SQLite file.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data.plants_seed import fields  # noqa: E402


def _run_build(out: Path, extra: list[str] | None = None) -> subprocess.CompletedProcess:
    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "build_plants_seed.py"),
        "--work-dir",
        str(tempfile.gettempdir()),
        "--out",
        str(out),
        "--refresh",
    ]
    if extra:
        cmd.extend(extra)
    return subprocess.run(cmd, capture_output=True, text=True)


@pytest.fixture()
def built_db(tmp_path):
    out = tmp_path / "plants-seed.db"
    result = _run_build(out)
    assert result.returncode == 0, result.stderr
    return out


def test_build_produces_database(built_db):
    assert built_db.exists()
    assert built_db.stat().st_size > 0


def test_build_meets_min_counts(built_db):
    import sqlite3

    conn = sqlite3.connect(f"file:{built_db}?mode=ro", uri=True)
    try:
        for category, minimum in fields.MIN_COUNTS_BY_CATEGORY.items():
            count = conn.execute(
                "SELECT COUNT(*) FROM plants WHERE category = ?", (category,)
            ).fetchone()[0]
            assert count >= minimum, f"{category}: {count} < {minimum}"
    finally:
        conn.close()


def test_known_toxic_never_non_toxic(built_db):
    import sqlite3

    conn = sqlite3.connect(f"file:{built_db}?mode=ro", uri=True)
    try:
        for key in fields.KNOWN_TOXIC_KEYS:
            level = conn.execute(
                "SELECT toxicity_level FROM plants WHERE key = ?", (key,)
            ).fetchone()[0]
            assert level != "non_toxic", f"{key} must not be non_toxic (got {level})"
    finally:
        conn.close()


def test_build_parity_writes_matching_catalog(tmp_path):
    """The catalog_dir recorded in meta matches the source JSON directory."""
    import sqlite3

    out = tmp_path / "plants-seed.db"
    result = _run_build(out)
    assert result.returncode == 0, result.stderr

    conn = sqlite3.connect(f"file:{out}?mode=ro", uri=True)
    try:
        row = conn.execute(
            "SELECT value FROM meta WHERE key = 'catalog_dir'"
        ).fetchone()
        assert row is not None
        assert Path(row[0]).name == "plants_seed"
    finally:
        conn.close()


def test_rejects_non_toxic_known_toxic(tmp_path):
    """A catalog that marks a known-toxic species non_toxic must fail the build."""
    bad = tmp_path / "bad"
    bad.mkdir()
    # Copy real catalog, then corrupt one known-toxic key.
    for name in ("houseplant.json", "herb.json", "vegetable.json", "fruit.json"):
        src = ROOT / "data" / "plants_seed" / name
        (bad / name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    data = json.loads((bad / "houseplant.json").read_text(encoding="utf-8"))
    for rec in data:
        if rec["key"] == "monstera_deliciosa":
            rec["toxicity_level"] = "non_toxic"
    (bad / "houseplant.json").write_text(json.dumps(data, indent=2), encoding="utf-8")

    out = tmp_path / "plants-seed.db"
    result = _run_build(out, extra=["--catalog-dir", str(bad)])
    assert result.returncode != 0
    assert "non_toxic" in result.stderr or "toxic" in result.stderr.lower()


def test_rejects_missing_required(tmp_path):
    """A catalog missing a universal required column must fail the build."""
    bad = tmp_path / "bad2"
    bad.mkdir()
    for name in ("houseplant.json", "herb.json", "vegetable.json", "fruit.json"):
        src = ROOT / "data" / "plants_seed" / name
        (bad / name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    data = json.loads((bad / "herb.json").read_text(encoding="utf-8"))
    for rec in data:
        if rec["key"] == "basil_sweet":
            del rec["common_name"]
    (bad / "herb.json").write_text(json.dumps(data, indent=2), encoding="utf-8")

    out = tmp_path / "plants-seed.db"
    result = _run_build(out, extra=["--catalog-dir", str(bad)])
    assert result.returncode != 0


def test_low_acid_requires_pressure_canning(tmp_path):
    """Low-acid vegetables must list pressure_canning, never bare water_bath_canning."""
    bad = tmp_path / "bad3"
    bad.mkdir()
    for name in ("houseplant.json", "herb.json", "vegetable.json", "fruit.json"):
        src = ROOT / "data" / "plants_seed" / name
        (bad / name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    data = json.loads((bad / "vegetable.json").read_text(encoding="utf-8"))
    for rec in data:
        if rec["key"] == "carrot":
            rec["preservation_methods"] = "water_bath_canning, freezing"
    (bad / "vegetable.json").write_text(json.dumps(data, indent=2), encoding="utf-8")

    out = tmp_path / "plants-seed.db"
    result = _run_build(out, extra=["--catalog-dir", str(bad)])
    assert result.returncode != 0
    assert "pressure_canning" in result.stderr or "canning" in result.stderr.lower()


def test_tender_crops_no_positive_outdoor_offsets(tmp_path):
    """Tender crops must never have positive direct_sow/transplant offsets (offset <= 0 = after last frost)."""
    bad = tmp_path / "bad4"
    bad.mkdir()
    for name in ("houseplant.json", "herb.json", "vegetable.json", "fruit.json"):
        src = ROOT / "data" / "plants_seed" / name
        (bad / name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    data = json.loads((bad / "vegetable.json").read_text(encoding="utf-8"))
    for rec in data:
        if rec["key"] == "green_bean_bush":
            rec["direct_sow_weeks_before_last_frost"] = 2  # tender, must be <= 0
    (bad / "vegetable.json").write_text(json.dumps(data, indent=2), encoding="utf-8")

    out = tmp_path / "plants-seed.db"
    result = _run_build(out, extra=["--catalog-dir", str(bad)])
    assert result.returncode != 0
    assert "tender" in result.stderr.lower() or "direct_sow" in result.stderr.lower()


def test_winter_multiplier_must_be_ge_1(tmp_path):
    """winter_multiplier is an interval multiplier and must be >= 1.0."""
    bad = tmp_path / "bad5"
    bad.mkdir()
    for name in ("houseplant.json", "herb.json", "vegetable.json", "fruit.json"):
        src = ROOT / "data" / "plants_seed" / name
        (bad / name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    data = json.loads((bad / "houseplant.json").read_text(encoding="utf-8"))
    for rec in data:
        if rec["key"] == "monstera_deliciosa":
            rec["winter_multiplier"] = 0.5  # invalid: must be >= 1.0
    (bad / "houseplant.json").write_text(json.dumps(data, indent=2), encoding="utf-8")

    out = tmp_path / "plants-seed.db"
    result = _run_build(out, extra=["--catalog-dir", str(bad)])
    assert result.returncode != 0
    assert "winter_multiplier" in result.stderr.lower()

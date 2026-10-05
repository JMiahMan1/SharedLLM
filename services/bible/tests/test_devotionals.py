"""Devotional sources.

The registry's contract is the interesting part: an unconfigured or broken
source is reported by name with the setting to fix, never dropped silently. A
reader seeing an empty devotional card with no explanation is the failure this
file exists to prevent.
"""
from datetime import date

import pytest

import services.config as cfg
from services.bible.devotionals import day_of_year, registry
from services.bible.devotionals.blb import BLB_WORKS, BlbDevotionalSource
from services.bible.devotionals.local import LocalDevotionalSource


def use(monkeypatch, **settings) -> None:
    monkeypatch.setattr(cfg, "BLB_BASE_URL", settings.get("blb", ""))
    monkeypatch.setattr(cfg, "BIBLE_DEVOTIONAL_DIR", settings.get("local", ""))


# ── day addressing ──────────────────────────────────────────────────────────


def test_day_of_year_matches_the_doy_urls():
    assert day_of_year(date(2026, 1, 1)) == 1
    assert day_of_year(date(2026, 12, 31)) == 365
    assert day_of_year(date(2026, 3, 1)) == 60


def test_leap_day_falls_on_february_twenty_eighth():
    assert day_of_year(date(2024, 2, 29)) == day_of_year(date(2001, 2, 28))


# ── blb ─────────────────────────────────────────────────────────────────────


def test_blb_is_unconfigured_without_a_base_url():
    source = BlbDevotionalSource("")
    assert source.configured() is False
    assert "blb_base_url" in source.unconfigured_reason()


def test_blb_builds_a_deep_link_per_work():
    source = BlbDevotionalSource("https://bible.test")
    entry = source.entry_for("dbdbg", 42)
    assert entry is not None
    assert entry.kind == "link"
    assert entry.url == "https://bible.test/devotionals/dbdbg/view.cfm?doy=42"
    assert entry.source == "blb"


def test_blb_morning_and_evening_ignore_the_day():
    source = BlbDevotionalSource("https://bible.test")
    assert source.entry_for("me-am", 1).url.endswith("?Time=am")
    assert source.entry_for("me-pm", 365).url.endswith("?Time=pm")


def test_blb_ships_the_documented_works():
    assert {slug for slug, _t, _u in BLB_WORKS} == set(BlbDevotionalSource("x").works)


def test_blb_ignores_an_unknown_work():
    assert BlbDevotionalSource("https://bible.test").entry_for("nope", 1) is None


def test_blb_url_for_an_unknown_work_is_an_error():
    with pytest.raises(ValueError):
        BlbDevotionalSource("https://bible.test").url_for("nope", 1)


# ── local ───────────────────────────────────────────────────────────────────


def write_day(folder, doy: int, body: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{doy:03d}.md").write_text(body, encoding="utf-8")


def test_local_is_unconfigured_without_a_directory():
    source = LocalDevotionalSource("")
    assert source.configured() is False
    assert "bible_devotional_dir" in source.unconfigured_reason()


def test_local_names_the_missing_directory(tmp_path):
    source = LocalDevotionalSource(str(tmp_path / "absent"))
    assert source.configured() is False
    assert "does not exist" in source.unconfigured_reason()


def test_local_reads_a_day_file(tmp_path):
    write_day(tmp_path, 15, "# Be Still\n> Reading: Psalm 46:10\n\nBe still.\n")
    entry = LocalDevotionalSource(str(tmp_path)).entry_for("default", 15)
    assert entry is not None
    assert entry.kind == "text"
    assert entry.title == "Be Still"
    assert entry.reference == "Psalm 46:10"
    assert entry.text == "Be still."


def test_local_returns_none_for_a_day_with_no_file(tmp_path):
    write_day(tmp_path, 15, "# Be Still\n")
    assert LocalDevotionalSource(str(tmp_path)).entry_for("default", 16) is None


def test_local_work_subdirectories(tmp_path):
    write_day(tmp_path / "family", 15, "# Ours\n\nOur own words.\n")
    source = LocalDevotionalSource(str(tmp_path))
    assert source.available_works() == ("family",)
    assert source.entry_for("family", 15).title == "Ours"
    assert source.entry_for("default", 15) is None


def test_local_falls_back_to_a_plain_day_title(tmp_path):
    write_day(tmp_path, 3, "Just words, no heading.\n")
    assert LocalDevotionalSource(str(tmp_path)).entry_for("default", 3).title == "Day 3"


# ── registry ────────────────────────────────────────────────────────────────


def test_blb_wins_when_both_sources_are_configured(monkeypatch, tmp_path):
    write_day(tmp_path, day_of_year(date(2026, 9, 1)), "# Local\n")
    use(monkeypatch, blb="https://bible.test", local=str(tmp_path))
    result = registry.daily(day=date(2026, 9, 1))
    assert result["source"] == "blb"
    assert result["entry"]["kind"] == "link"


def test_local_is_used_when_blb_is_unconfigured(monkeypatch, tmp_path):
    write_day(tmp_path, day_of_year(date(2026, 9, 1)), "# Local\n\nWords.\n")
    use(monkeypatch, blb="", local=str(tmp_path))
    result = registry.daily(day=date(2026, 9, 1))
    assert result["source"] == "local"
    assert result["entry"]["title"] == "Local"
    assert any("blb_base_url" in item["reason"] for item in result["skipped"])


def test_a_named_work_is_honoured(monkeypatch, tmp_path):
    write_day(tmp_path / "family", day_of_year(date(2026, 9, 1)), "# Ours\n\nWords.\n")
    use(monkeypatch, blb="https://bible.test", local=str(tmp_path))
    result = registry.daily(day=date(2026, 9, 1), work="family")
    assert result["entry"]["source"] == "local"


def test_an_empty_registry_explains_itself(monkeypatch):
    use(monkeypatch, blb="", local="")
    result = registry.daily(day=date(2026, 9, 1))
    assert result["entry"] is None
    assert result["source"] is None
    assert "No devotional is available" in result["reason"]
    assert {item["source"] for item in result["skipped"]} == {"blb", "local"}


def test_a_failing_source_is_reported_not_swallowed(monkeypatch, tmp_path):
    class Exploding(BlbDevotionalSource):
        code = "blb"
        priority = 10

        def entry_for(self, work, day_of_year):
            raise RuntimeError("blb is down")

    monkeypatch.setattr(registry, "_SOURCES", ("blb", "local"))
    monkeypatch.setattr(registry, "BlbDevotionalSource", Exploding)
    use(monkeypatch, blb="https://bible.test", local="")
    result = registry.daily(day=date(2026, 9, 1))
    assert result["entry"] is None
    assert any("blb is down" in item["reason"] for item in result["skipped"])


def test_sources_are_described_for_the_settings_screen(monkeypatch, tmp_path):
    use(monkeypatch, blb="https://bible.test", local=str(tmp_path))
    described = {s["code"]: s for s in registry.describe_sources()}
    assert described["blb"]["configured"] is True
    assert described["blb"]["reason"] == ""
    assert described["local"]["configured"] is True
    assert described["blb"]["priority"] < described["local"]["priority"]
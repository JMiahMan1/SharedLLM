"""Alpaca's own benchmark results are evidence the trainer can read offline.

The files are written by Alpaca, not by SharedLLM, so the parser is tested
against both shapes it really uses: the general suite's ``category_<group>``
blocks with a 0-100 score, and the SharedLLM suite's per-task list. A machine
with no results yet must read as "nothing measured", not as an error.
"""
from __future__ import annotations

import json
import pathlib

from services.gateway.trainer import alpaca


def _write(path: pathlib.Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _tree(tmp_path: pathlib.Path, monkeypatch) -> dict[str, pathlib.Path]:
    general = tmp_path / "llm" / "models"
    shared = tmp_path / "shared" / "models"
    monkeypatch.setenv("ALPACA_BENCHMARK_RESULTS_DIR", str(general))
    monkeypatch.setenv("ALPACA_SHARED_BENCHMARK_RESULTS_DIR", str(shared))
    return {"general": general, "shared": shared}


def test_the_general_suite_is_read_per_category(tmp_path, monkeypatch):
    tree = _tree(tmp_path, monkeypatch)
    _write(
        tree["general"] / "general_qwen3_8b.json",
        {
            "model": "qwen3:8b",
            "generated_at": "2026-11-17T19:45:43",
            "results": [
                {
                    "model": "qwen3:8b",
                    "category_coding": {"tests_run": 6, "tests_passed": 2, "score": 33, "letter": "F"},
                    "category_reasoning": {"tests_run": 4, "tests_passed": 4, "score": 88, "letter": "B"},
                }
            ],
        },
    )

    summary = alpaca.benchmark_summary()

    assert summary["total"] == 1
    row = summary["results"][0]
    assert row["model"] == "qwen3:8b" and row["suite"] == "general"
    assert row["categories"]["coding"]["score"] == 33
    assert row["average_score"] == 60.5


def test_the_sharedllm_suite_is_averaged_from_its_tasks(tmp_path, monkeypatch):
    tree = _tree(tmp_path, monkeypatch)
    _write(
        tree["shared"] / "shared_qwen3_8b.json",
        {
            "model": "qwen3:8b",
            "generated_at": "2026-11-18T10:00:00",
            "results": [
                {
                    "model": "qwen3:8b",
                    "tasks": [
                        {"category": "fast_path", "score": 100},
                        {"category": "fast_path", "success": False},
                        {"category": "librarian", "score": 80},
                    ],
                }
            ],
        },
    )

    summary = alpaca.benchmark_summary()

    categories = summary["results"][0]["categories"]
    assert categories["fast_path"] == {
        "score": 50.0,
        "tests_run": 2,
        "tests_passed": 1,
        "letter": None,
    }
    assert categories["librarian"]["score"] == 80.0


def test_a_machine_with_no_results_reads_as_nothing_measured(tmp_path, monkeypatch):
    tree = _tree(tmp_path, monkeypatch)
    assert not tree["general"].exists()

    summary = alpaca.benchmark_summary()

    assert summary["total"] == 0
    assert summary["results"] == []
    assert summary["directories"]["general"].endswith("llm/models")


def test_the_weakest_categories_come_from_the_worst_run_of_the_general_suite(tmp_path, monkeypatch):
    tree = _tree(tmp_path, monkeypatch)
    _write(
        tree["general"] / "general_qwen3_8b.json",
        {
            "model": "qwen3:8b",
            "generated_at": "2026-11-17T19:45:43",
            "results": [
                {
                    "model": "qwen3:8b",
                    "category_coding": {"tests_run": 6, "tests_passed": 1, "score": 20, "letter": "F"},
                    "category_knowledge": {"tests_run": 8, "tests_passed": 7, "score": 90, "letter": "A"},
                }
            ],
        },
    )
    summary = alpaca.benchmark_summary()

    weak = alpaca.weakest_categories(summary, "qwen3:8b")

    assert [item["category"] for item in weak] == ["coding"]
    assert weak[0]["score"] == 20
    assert weak[0]["tests_run"] == 6


def test_a_shared_result_alone_does_not_drive_curriculum_proposals(tmp_path, monkeypatch):
    tree = _tree(tmp_path, monkeypatch)
    _write(
        tree["shared"] / "shared_qwen3_8b.json",
        {
            "model": "qwen3:8b",
            "results": [{"model": "qwen3:8b", "tasks": [{"category": "fast_path", "score": 10}]}],
        },
    )

    assert alpaca.weakest_categories(alpaca.benchmark_summary(), "qwen3:8b") == []


def test_another_models_results_are_not_mixed_in(tmp_path, monkeypatch):
    tree = _tree(tmp_path, monkeypatch)
    _write(
        tree["general"] / "general_other.json",
        {
            "model": "other:70b",
            "results": [{"model": "other:70b", "category_coding": {"tests_run": 6, "score": 5}}],
        },
    )

    summary = alpaca.benchmark_summary()

    assert alpaca.weakest_categories(summary, "qwen3:8b") == []
    assert len(alpaca.weakest_categories(summary, "other:70b")) == 1

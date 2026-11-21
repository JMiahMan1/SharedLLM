"""The cycle is the trainer's product: one pass that leaves the system better.

These tests run a whole cycle against fakes (no RAG, no model, no workspace) and
pin what the household gets out of it: a bounded evaluation, a merged backlog,
low-confidence lessons written back, an optional proven repair, and a cycle that
still finishes when RAG is down. The UI-gap rule is checked against a small
catalogue so the intent (capabilities with no screen) is explicit.
"""
from __future__ import annotations

import json
import pathlib
from pathlib import Path

import pytest

from services.gateway.trainer import facilitator, runner


@pytest.fixture
def data_dir(tmp_path: pathlib.Path, monkeypatch):
    """Point every trainer write at a temp directory, never the repo's ``data/``."""
    monkeypatch.setattr(runner, "DATA_DIR", tmp_path)
    monkeypatch.setattr(facilitator.runner, "DATA_DIR", tmp_path)
    return tmp_path


def _dream_ok() -> dict:
    return {
        "status": "SUCCESS",
        "reviewed": 12,
        "kept": 12,
        "compacted": [],
        "merged": {},
        "pruned": [],
        "degraded": [],
    }


def test_a_capability_with_no_screen_becomes_a_ui_proposal():
    features = [
        {"kind": "route", "tags": ["media"], "service": "execution"},
        *[{"kind": "route", "tags": ["garden"], "service": "garden"} for _ in range(11)],
        *[{"kind": "route", "tags": ["bible"], "service": "bible"} for _ in range(12)],
        {"kind": "page", "tags": ["bible"], "service": "ui"},
    ]

    proposals = facilitator.ui_proposals({"features": features})
    titles = [proposal["title"] for proposal in proposals]

    assert any('"garden"' in title for title in titles), "garden has no screen: it should be proposed"
    assert not any('"bible"' in title for title in titles), "bible already has a page"
    assert not any('"media"' in title for title in titles), "two routes is below the threshold"


def test_internal_tags_are_not_proposed_as_screens():
    features = [{"kind": "route", "tags": ["tools"], "service": "execution"} for _ in range(20)]

    assert facilitator.ui_proposals({"features": features}) == []


async def test_a_cycle_evaluates_proposes_and_remembers(data_dir, monkeypatch):
    async def fake_dream(user_id, **kwargs):
        return _dream_ok()

    ingested: list[str] = []

    async def fake_ingest(proposals, *, user_id="default", limit=3):
        ingested.extend(proposal["id"] for proposal in proposals[:limit])
        return ingested

    monkeypatch.setattr(facilitator.dream_mod, "dream", fake_dream)
    monkeypatch.setattr(facilitator.dream_mod, "ingest_proposals", fake_ingest)

    async def always_wrong(case):
        return "I am not sure."

    summary = await facilitator.cycle(limit=4, complete_fn=always_wrong)

    assert summary["status"] == "SUCCESS"
    assert summary["curriculum"]["total"] == 4
    assert summary["eval"]["totals"]["cases"] == 4
    assert summary["proposals"], "a failing run must produce proposals"
    assert ingested and summary["lessons"] == ingested
    assert Path(summary["backlog_path"]).is_file()


async def test_a_cycle_survives_rag_being_down(data_dir, monkeypatch):
    async def broken_dream(user_id, **kwargs):
        raise ConnectionError("rag is down")

    monkeypatch.setattr(facilitator.dream_mod, "dream", broken_dream)

    async def right_answer(case):
        return "WorkspaceFileWriteRequest path content Apply: [lesson-abc123]"

    summary = await facilitator.cycle(limit=2, apply_lessons=False, complete_fn=right_answer)

    assert summary["status"] == "SUCCESS"
    assert summary["dream"]["status"] is None
    assert any(error.startswith("dream:") for error in summary["errors"])
    assert summary["eval"]["totals"]["cases"] == 2


async def test_a_cycle_can_include_a_proven_repair(data_dir, monkeypatch):
    async def fake_dream(user_id, **kwargs):
        return _dream_ok()

    calls: list[dict] = []

    async def fake_repair(workspace_id, **kwargs):
        calls.append({"workspace_id": workspace_id, **kwargs})

        class _Result:
            def to_dict(self):
                return {"status": "repaired", "workspace_id": workspace_id, "path": kwargs["path"]}

        return _Result()

    monkeypatch.setattr(facilitator.dream_mod, "dream", fake_dream)
    monkeypatch.setattr(facilitator.selffix, "repair_workspace", fake_repair)

    async def answer(case):
        return "anything"

    summary = await facilitator.cycle(
        limit=1,
        apply_lessons=False,
        complete_fn=answer,
        repair={"workspace_id": "sermons", "path": "math_utils.py", "error_text": "boom"},
    )

    assert calls[0]["workspace_id"] == "sermons"
    assert summary["repair"]["status"] == "repaired"


async def test_the_backlog_is_readable_after_a_cycle(data_dir, monkeypatch):
    async def fake_dream(user_id, **kwargs):
        return _dream_ok()

    monkeypatch.setattr(facilitator.dream_mod, "dream", fake_dream)

    async def answer(case):
        return "no"

    await facilitator.cycle(limit=1, apply_lessons=False, complete_fn=answer)
    backlog = facilitator.read_backlog()

    assert backlog["eval_report_id"]
    assert backlog["proposals"]
    assert json.loads((data_dir / "backlog.json").read_text(encoding="utf-8"))["eval_report_id"] == (
        backlog["eval_report_id"]
    )


def test_reading_a_missing_backlog_returns_an_empty_one(data_dir):
    backbone = facilitator.read_backlog()

    assert backbone == {"generated_at": None, "eval_report_id": None, "proposals": [], "lessons": []}

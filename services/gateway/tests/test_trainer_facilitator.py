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


#: A host that could not be probed (unset URL, down). Unknown is not ready: the
#: evaluation is skipped with an error, and it never becomes a "hardware" proposal.
OFFLINE = {"reachable": False, "ready": False, "reasons": ["llm_local_url is not set"]}

#: The household is using the GPU right now.
BUSY = {
    "reachable": True,
    "ready": False,
    "reasons": ["only 400 MB of GPU memory is free"],
    "free_vram_mb": 400,
    "models": {"chosen": "qwen3:8b"},
    "lora": {"total": 0},
}

IDLE = {
    "reachable": True,
    "ready": True,
    "reasons": [],
    "free_vram_mb": 9000,
    "models": {"chosen": "qwen3:8b"},
    "lora": {"total": 1},
}


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
        return {"written": list(ingested), "failed": []}

    monkeypatch.setattr(facilitator.dream_mod, "dream", fake_dream)
    monkeypatch.setattr(facilitator.dream_mod, "ingest_proposals", fake_ingest)

    async def always_wrong(case):
        return "I am not sure."

    summary = await facilitator.cycle(limit=4, complete_fn=always_wrong, hardware=IDLE)

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

    summary = await facilitator.cycle(
        limit=2, apply_lessons=False, complete_fn=right_answer, hardware=IDLE
    )

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
        hardware=OFFLINE,
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

    await facilitator.cycle(limit=1, apply_lessons=False, complete_fn=answer, hardware=IDLE)
    backlog = facilitator.read_backlog()

    assert backlog["eval_report_id"]
    assert backlog["proposals"]
    assert json.loads((data_dir / "backlog.json").read_text(encoding="utf-8"))["eval_report_id"] == (
        backlog["eval_report_id"]
    )


def test_reading_a_missing_backlog_returns_an_empty_one(data_dir):
    backbone = facilitator.read_backlog()

    assert backbone == {"generated_at": None, "eval_report_id": None, "proposals": [], "lessons": []}


async def test_a_busy_host_is_respected_and_recorded(data_dir, monkeypatch):
    """The household keeps the GPU: no evaluation, one honest proposal."""
    async def fake_dream(user_id, **kwargs):
        return _dream_ok()

    monkeypatch.setattr(facilitator.dream_mod, "dream", fake_dream)
    calls: list[int] = []

    async def must_not_run(cases, **kwargs):
        calls.append(len(cases))
        raise AssertionError("the evaluation must not run while the host is busy")

    monkeypatch.setattr(facilitator.runner, "run_curriculum", must_not_run)

    summary = await facilitator.cycle(limit=4, apply_lessons=False, hardware=BUSY)

    assert calls == []
    assert summary["eval"]["skipped"] == "hardware"
    assert summary["hardware"]["ready"] is False
    assert any(proposal["kind"] == "hardware_busy" for proposal in summary["proposals"])
    backlog = facilitator.read_backlog()
    assert backlog["eval_report_id"] is None
    assert backlog["hardware_ready"] is False


async def test_an_unreachable_host_is_not_evaluated_and_files_no_proposal(data_dir, monkeypatch):
    """Unknown is not ready: no evaluation, a recorded error, and no lesson about it."""
    async def fake_dream(user_id, **kwargs):
        return _dream_ok()

    async def must_not_run(cases, **kwargs):
        raise AssertionError("the evaluation must not run against a host nobody could probe")

    monkeypatch.setattr(facilitator.dream_mod, "dream", fake_dream)
    monkeypatch.setattr(facilitator.runner, "run_curriculum", must_not_run)

    summary = await facilitator.cycle(limit=2, apply_lessons=False, hardware=OFFLINE)

    assert summary["eval"]["skipped"] == "unreachable"
    assert any(error.startswith("eval:") for error in summary["errors"])
    assert not any(proposal["kind"] == "hardware_busy" for proposal in summary["proposals"])


def test_a_weak_benchmark_category_becomes_a_proposal():
    summary = {
        "results": [
            {
                "suite": "general",
                "model": "qwen3:8b",
                "categories": {
                    "coding": {"score": 20, "tests_run": 6, "tests_passed": 1, "letter": "F"},
                    "knowledge": {"score": 95, "tests_run": 8, "tests_passed": 8, "letter": "A"},
                },
            }
        ]
    }

    proposals = facilitator.benchmark_proposals("qwen3:8b", summary)

    assert len(proposals) == 1
    assert proposals[0]["kind"] == "benchmark_gap"
    assert "coding" in proposals[0]["title"]
    assert proposals[0]["priority"] == 7


def test_one_unlucky_test_is_not_a_benchmark_proposal():
    summary = {
        "results": [
            {
                "suite": "general",
                "model": "qwen3:8b",
                "categories": {"coding": {"score": 0, "tests_run": 1, "tests_passed": 0}},
            }
        ]
    }

    assert facilitator.benchmark_proposals("qwen3:8b", summary) == []


async def test_infrastructure_failures_never_become_lessons(data_dir, monkeypatch):
    """A busy host, or a model that stopped answering, is shown but never remembered."""
    from services.gateway.trainer import dream as dream_mod
    from services.gateway.trainer import llm

    async def fake_dream(user_id, **kwargs):
        return _dream_ok()

    sent: list[dict] = []

    async def fake_request(method, path, *, params=None, body=None, timeout=60.0):
        sent.append(body)
        return {"status": "SUCCESS"}

    monkeypatch.setattr(dream_mod, "dream", fake_dream)
    monkeypatch.setattr(dream_mod, "request", fake_request)

    async def host_busy(case):
        raise llm.LocalModelBusy("no free slot after 45s")

    summary = await facilitator.cycle(limit=4, complete_fn=host_busy, hardware=IDLE)

    assert summary["eval"]["stopped_reason"] == "host_busy"
    kinds = {proposal["kind"] for proposal in summary["proposals"]}
    assert "host_busy" in kinds
    assert not any(kind.startswith("curriculum_") for kind in kinds), "unanswered cases are not failures"
    lesson_topics = {json.loads(body["content"])["topic"] for body in sent}
    assert not any("host_busy" in topic or "model_unavailable" in topic for topic in lesson_topics)


async def test_a_busy_host_proposal_is_never_ingested(data_dir, monkeypatch):
    from services.gateway.trainer import dream as dream_mod

    async def fake_dream(user_id, **kwargs):
        return _dream_ok()

    sent: list[dict] = []

    async def fake_request(method, path, *, params=None, body=None, timeout=60.0):
        sent.append(body)
        return {"status": "SUCCESS"}

    monkeypatch.setattr(dream_mod, "dream", fake_dream)
    monkeypatch.setattr(dream_mod, "request", fake_request)

    summary = await facilitator.cycle(limit=2, hardware=BUSY)

    assert any(proposal["kind"] == "hardware_busy" for proposal in summary["proposals"])
    assert all("hardware_busy" not in json.loads(body["content"])["topic"] for body in sent)


async def test_a_second_cycle_while_one_runs_is_refused(data_dir, monkeypatch):
    import asyncio

    async def fake_dream(user_id, **kwargs):
        return _dream_ok()

    monkeypatch.setattr(facilitator.dream_mod, "dream", fake_dream)
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow(case):
        started.set()
        await release.wait()
        return "no"

    first = asyncio.create_task(
        facilitator.cycle(limit=1, apply_lessons=False, complete_fn=slow, hardware=IDLE)
    )
    await started.wait()
    with pytest.raises(facilitator.CycleBusy):
        await facilitator.cycle(limit=1, apply_lessons=False, hardware=IDLE)
    release.set()
    await first
    assert not facilitator.cycle_running()


@pytest.mark.parametrize(
    "repair",
    [
        {"workspace_id": "w", "path": "a.py", "attempts": 0},
        {"workspace_id": "w", "path": "a.py", "attempts": 50},
        {"workspace_id": "w", "path": "a.py", "attempts": "2"},
        {"workspace_id": "", "path": "a.py"},
    ],
)
async def test_a_malformed_repair_is_refused_before_any_work(data_dir, repair):
    with pytest.raises(ValueError):
        await facilitator.cycle(limit=1, apply_lessons=False, hardware=IDLE, repair=repair)


async def test_a_cycle_refuses_to_run_without_a_writable_data_directory(tmp_path, monkeypatch):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("a file where the directory should be", encoding="utf-8")
    monkeypatch.setattr(runner, "DATA_DIR", blocker / "trainer")

    with pytest.raises(runner.TrainerStorageError, match="data/trainer"):
        await facilitator.cycle(limit=1, apply_lessons=False, hardware=IDLE)


def test_a_corrupt_backlog_is_an_error_not_an_empty_list(data_dir):
    (data_dir / "backlog.json").write_text("{not json", encoding="utf-8")

    with pytest.raises(runner.TrainerStorageError):
        facilitator.read_backlog()

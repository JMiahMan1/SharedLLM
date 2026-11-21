"""A run must be scored exactly, bounded in time, and leave usable evidence.

The scoring is the trainer's measuring stick: if it is lenient, the model looks
better than it is and the backlog fills with the wrong work. These tests pin the
check semantics (include/absent/regex/length), the two ways a run stops early
(budget and a model that keeps failing), and that a report can be re-read and
exported as fine-tuning rows without walking outside its directory.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from services.gateway.trainer import curriculum, runner
from services.gateway.trainer.llm import LocalModelUnavailable


def _case(**checks) -> curriculum.Case:
    return curriculum.Case(
        id="case-x", bucket="repair", title="A case", prompt="Do the thing", checks=checks
    )


def test_a_clean_answer_passes():
    passed, score, failed = runner.score_answer(_case(must_include=["def mean"]), "def mean(v):\n  return 1")

    assert passed and score == 1.0 and failed == []


def test_a_missing_phrase_fails_and_is_named():
    passed, score, failed = runner.score_answer(_case(must_include=["def mean", "return"]), "def mean(v)")

    assert not passed
    assert score == 0.5
    assert failed == ["missing: return"]


def test_forbidden_text_and_regexes_are_checked():
    passed, _, failed = runner.score_answer(
        _case(must_not_include=["TODO"], must_not_match=[r"len\(values\)\s*\+\s*1"]),
        "def mean(values):\n    return sum(values) / len(values) + 1  # TODO",
    )

    assert not passed
    assert "unexpected: TODO" in failed
    assert any(item.startswith("matched:") for item in failed)


def test_a_regex_must_match():
    passed, _, failed = runner.score_answer(_case(must_match=[r"(?i)clienttimeout"]), "use ClientTimeout(total=30)")

    assert passed, failed


def test_the_length_limit_is_a_check_of_its_own():
    passed, score, failed = runner.score_answer(_case(must_include=["ok"], max_chars=5), "ok and more words")

    assert not passed
    assert score == 0.5
    assert any(item.startswith("too long:") for item in failed)


async def test_a_run_scores_every_case_and_saves_a_report(tmp_path: pathlib.Path):
    cases = [
        _case(must_include=["yes"]),
        curriculum.Case(id="case-y", bucket="writing", title="B", prompt="p", checks={"must_include": ["no"]}),
    ]

    async def fake(case):
        return "yes"

    report = await runner.run_curriculum(cases, complete_fn=fake, report_dir=tmp_path)

    assert report["totals"]["cases"] == 2
    assert report["totals"]["passed"] == 1
    assert report["by_bucket"]["repair"]["pass_rate"] == 1.0
    assert report["by_bucket"]["writing"]["pass_rate"] == 0.0
    assert (tmp_path / f"{report['id']}.json").is_file()


async def test_a_run_stops_when_the_model_keeps_failing(tmp_path: pathlib.Path):
    cases = [curriculum.Case(id=f"case-{i}", bucket="tools", title="t", prompt="p", checks={}) for i in range(10)]

    async def broken(case):
        raise LocalModelUnavailable("model not loaded")

    report = await runner.run_curriculum(cases, complete_fn=broken, report_dir=tmp_path)

    assert report["stopped_reason"] == "model_unavailable"
    assert report["totals"]["cases"] == runner.MAX_CONSECUTIVE_ERRORS
    assert all(case["error"] for case in report["cases"])


async def test_a_run_stops_when_its_budget_is_spent(tmp_path: pathlib.Path):
    cases = [curriculum.Case(id=f"case-{i}", bucket="tools", title="t", prompt="p", checks={}) for i in range(50)]

    async def slow(case):
        return "x"

    report = await runner.run_curriculum(cases, complete_fn=slow, budget_s=0.0, report_dir=tmp_path)

    assert report["stopped_reason"] == "budget"
    assert report["cases"] == []


def test_reports_are_listed_newest_first(tmp_path: pathlib.Path):
    for report_id, started in (("eval-a", "2026-01-01T00:00:00Z"), ("eval-b", "2026-02-01T00:00:00Z")):
        runner.save_report(
            {"id": report_id, "started_at": started, "totals": {}, "model": "m"}, report_dir=tmp_path
        )

    listed = runner.list_reports(report_dir=tmp_path)

    assert [item["id"] for item in listed] == ["eval-b", "eval-a"]


def test_a_report_id_cannot_walk_out_of_its_directory(tmp_path: pathlib.Path):
    assert runner.load_report("../../etc/passwd", report_dir=tmp_path) is None
    assert runner.load_report("eval-missing", report_dir=tmp_path) is None


def test_the_dataset_keeps_only_accepted_rows_by_default(tmp_path: pathlib.Path):
    report = {
        "id": "eval-x",
        "model": "coder",
        "cases": [
            {"id": "a", "bucket": "repair", "prompt": "p1", "answer": "good", "passed": True,
             "score": 1.0, "failed_checks": []},
            {"id": "b", "bucket": "repair", "prompt": "p2", "answer": "bad", "passed": False,
             "score": 0.0, "failed_checks": ["missing: x"]},
            {"id": "c", "bucket": "repair", "prompt": "p3", "answer": "", "passed": True, "score": 1.0},
        ],
    }
    target = tmp_path / "out.jsonl"

    accepted = runner.export_dataset(report, path=target)
    everything = runner.export_dataset(report, path=tmp_path / "all.jsonl", accepted_only=False)

    assert accepted["rows"] == 1 and everything["rows"] == 2
    rows = [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines()]
    # No system prompt on the case: no (empty) system message in the row.
    assert rows[0]["messages"][0] == {"role": "user", "content": "p1"}
    assert rows[0]["messages"][1] == {"role": "assistant", "content": "good"}
    assert rows[0]["meta"]["accepted"] is True


async def test_exported_rows_carry_the_system_prompt_the_model_saw(tmp_path: pathlib.Path):
    case = curriculum.Case(
        id="sys-1", bucket="writing", title="t", prompt="Say hi", system="You are Jarvis.",
        checks={"must_include": ["hi"]},
    )

    async def fake(case):
        return "hi there"

    report = await runner.run_curriculum([case], complete_fn=fake, report_dir=tmp_path)
    rows = runner.training_rows(report)

    assert rows[0]["messages"][0] == {"role": "system", "content": "You are Jarvis."}
    assert all(message["content"] for message in rows[0]["messages"]), "no empty message is exported"


async def test_unanswered_cases_are_errors_not_curriculum_failures(tmp_path: pathlib.Path):
    from services.gateway.trainer import llm

    cases = [_case(must_include=["x"]) for _ in range(3)]

    async def busy(case):
        raise llm.LocalModelBusy("no free slot")

    report = await runner.run_curriculum(cases, complete_fn=busy, report_dir=tmp_path)

    assert report["stopped_reason"] == "host_busy"
    assert report["totals"]["infrastructure_errors"] == 3
    bucket = next(iter(report["by_bucket"].values()))
    assert bucket["failed_ids"] == [] and len(bucket["error_ids"]) == 3
    assert runner.training_rows(report, accepted_only=False) == []


async def test_the_run_asks_one_resolved_model_for_every_case(tmp_path: pathlib.Path, monkeypatch):
    """The eval request's model reaches every completion; nothing is resolved per case."""
    from services.gateway import orchestrator
    from services.gateway.trainer import llm

    resolved: list[str | None] = []
    asked: list[str | None] = []

    async def fake_settings():
        return {"llm_local_url": "http://llm-host:11434"}

    async def fake_resolve(settings, requested=None):
        resolved.append(requested)
        return "coder:14b"

    async def fake_complete(prompt, *, system=None, model=None, max_tokens=320, **kwargs):
        asked.append(model)
        return llm.Completion(text="x", model=model or "", elapsed_s=0.0, prompt_chars=len(prompt))

    monkeypatch.setattr(orchestrator, "get_llm_settings", fake_settings)
    monkeypatch.setattr(llm, "resolve_model", fake_resolve)
    monkeypatch.setattr(llm, "complete", fake_complete)

    report = await runner.run_curriculum(
        [_case(must_include=["x"]) for _ in range(3)], model="coder", report_dir=tmp_path
    )

    assert resolved == ["coder"]
    assert asked == ["coder:14b"] * 3
    assert report["model"] == "coder:14b"


async def test_a_model_that_cannot_be_resolved_stops_the_run_before_any_case(tmp_path, monkeypatch):
    from services.gateway import orchestrator
    from services.gateway.trainer import llm

    async def fake_settings():
        return {"llm_local_url": "http://llm-host:11434", "trainer_model": "flux-image:q8"}

    monkeypatch.setattr(orchestrator, "get_llm_settings", fake_settings)

    with pytest.raises(llm.LocalModelUnavailable, match="picture model"):
        await runner.run_curriculum([_case(must_include=["x"])], report_dir=tmp_path)
    assert list(tmp_path.glob("*.json")) == []


async def test_an_unwritable_report_directory_refuses_the_run(tmp_path: pathlib.Path):
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    called: list[str] = []

    async def fake(case):
        called.append(case.id)
        return "x"

    with pytest.raises(runner.TrainerStorageError):
        await runner.run_curriculum([_case(must_include=["x"])], complete_fn=fake, report_dir=blocker / "r")
    assert called == [], "no model time is spent on a run that cannot be kept"


def test_a_corrupt_report_is_an_error_not_a_missing_one(tmp_path: pathlib.Path):
    (tmp_path / "eval-bad.json").write_text("{nope", encoding="utf-8")

    with pytest.raises(runner.TrainerStorageError):
        runner.load_report("eval-bad", report_dir=tmp_path)
    listed = runner.list_reports(report_dir=tmp_path)
    assert listed[0]["id"] == "eval-bad" and "unreadable" in listed[0]["error"]

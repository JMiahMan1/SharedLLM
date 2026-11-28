"""The proposer/selector half of SIM: full history in, outcomes back out."""
from __future__ import annotations

from services.gateway.trainer import dream


def _history(*problems):
    return {"count": len(problems), "problems": list(problems)}


def test_proposer_sees_retired_fixes_and_will_not_repropose_them():
    history = _history(
        {
            "problem_id": "sim-dead",
            "status": "retired",
            "edit_or_rule": "Always retry the same failing shell command.",
            "observed_outcome": "the mission looped twice",
            "attempts": [{"attempt_id": "a1"}, {"attempt_id": "a2"}],
        }
    )
    proposals = dream.proposals_from_sim(history)

    retired = next(p for p in proposals if p["kind"] == "sim_retired")
    assert "sim-dead" in retired["title"]
    assert "looped twice" in retired["detail"]
    assert dream.is_lesson_worthy(retired) is False


def test_a_candidate_with_repeated_attempts_gets_a_verification_proposal():
    history = _history(
        {
            "problem_id": "sim-recurring",
            "status": "candidate",
            "edit_or_rule": "Use the alias named by the error.",
            "attempts": [{"attempt_id": "a1"}, {"attempt_id": "a2"}],
        }
    )
    proposals = dream.proposals_from_sim(history)
    assert any(p["kind"] == "sim_validate" and "sim-recurring" in p["title"] for p in proposals)


async def test_selector_records_each_case_outcome_and_consolidates(monkeypatch):
    recorded: list[dict] = []
    consolidated: list[bool] = []

    async def fake_sim_record(**kwargs):
        recorded.append(kwargs)
        return {"status": "SUCCESS"}

    async def fake_consolidate():
        consolidated.append(True)
        return {"promoted": [], "retired": [], "model_specific": []}

    monkeypatch.setattr(dream, "sim_record", fake_sim_record)
    monkeypatch.setattr(dream, "consolidate_sim", fake_consolidate)

    report = {
        "model": "kwaipilot-kat-coder-v2-5-dev-iq4-nl",
        "cases": [
            {"id": "case-pass", "title": "Write a test", "passed": True},
            {"id": "case-fail", "title": "Fix a bug", "passed": False, "error": "assertion failed"},
            {"id": "case-infra", "title": "GPU", "passed": False, "infrastructure_error": True},
        ],
    }
    written = await dream.record_eval_outcomes(report, user_id="alice")

    assert written == 2
    assert [r["outcome_status"] for r in recorded] == ["improved", "regressed"]
    assert all(r["model"] == report["model"] for r in recorded)
    assert all(r["scope"] == "system" for r in recorded)
    assert consolidated == [True]

"""The proposer/selector half of SIM: full history in, outcomes back out."""
from __future__ import annotations

import pytest
from fastapi import HTTPException

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


async def test_context_for_turn_preserves_retrieval_relevance_order(monkeypatch):
    from services.gateway.trainer import sim as sim_mod

    verified = {
        "problem_id": "sim-v",
        "scope": "user",
        "user_id": "alice",
        "harness": "workspace",
        "status": "verified",
        "verified_models": ["qwen"],
        "edit_or_rule": "VERIFIED-MATCHING-LESSON",
        "rationale": "",
    }
    portable = {
        "problem_id": "sim-p",
        "scope": "system",
        "user_id": "default",
        "harness": "workspace",
        "status": "portable",
        "verified_models": ["qwen", "claude"],
        "edit_or_rule": "PORTABLE-UNRELATED-LESSON",
        "rationale": "",
    }

    async def fake_query(**kwargs):
        return [verified, portable]

    monkeypatch.setattr(sim_mod, "_retrieve", fake_query)

    block, items = await sim_mod.context_for_turn(
        user_id="alice",
        harness="workspace",
        query="verified matching lesson",
        model="qwen3-6-35b",
    )

    assert [item["problem_id"] for item in items] == ["sim-v", "sim-p"]
    assert block.index("VERIFIED-MATCHING-LESSON") < block.index("PORTABLE-UNRELATED-LESSON")


async def test_admin_records_system_sim_outcome_but_non_admin_still_gets_404(monkeypatch):
    from services.gateway.trainer import routes

    posted: list[dict] = []

    async def fake_problems(*, scope, user_id, is_admin, **kwargs):
        if scope == "system":
            return [{"problem_id": "sim-system"}]
        return [{"problem_id": "sim-alice"}]

    async def fake_request(method, path, body=None, **kwargs):
        posted.append(body or {})
        return {"status": "SUCCESS"}

    monkeypatch.setattr(routes, "_sim_problems", fake_problems)
    monkeypatch.setattr(routes.dream_mod, "request", fake_request)

    body = routes.SimOutcomeBody(
        problem_id="sim-system", outcome_status="improved", model="qwen3-6-35b"
    )

    async def admin_viewer(request):
        return "root", True

    monkeypatch.setattr(routes, "_viewer", admin_viewer)
    result = await routes.sim_outcome(body, None)
    assert result["status"] == "SUCCESS"
    assert posted[-1]["user_id"] == "default"

    async def non_admin_viewer(request):
        return "alice", False

    monkeypatch.setattr(routes, "_viewer", non_admin_viewer)
    with pytest.raises(HTTPException) as exc:
        await routes.sim_outcome(body, None)
    assert exc.value.status_code == 404
    assert len(posted) == 1, "a non-admin must never reach the recording call"


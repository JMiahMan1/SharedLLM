"""Mission discipline: Raven's objectives checklist and its runtime-checked debrief."""
from __future__ import annotations

import inspect

from services.gateway import agent_loop, tool_registry
from services.gateway.mission_discipline import MAX_REFUSALS, Discipline


def _objs(*items):
    return {"objectives": [dict(zip(("id", "text", "status", "note"), it, strict=False)) for it in items]}


# ---------------------------------------------------------------- objectives

def test_objectives_replace_the_list_and_show_as_one_compact_line():
    d = Discipline()
    r = d.set_objectives(_objs(("o1", "read config", "done"), ("o2", "add route", "in_progress"), ("o3", "tests", "pending")))
    assert r["status"] == "SUCCESS" and "1/3" in r["message"]
    assert d.line() == "OBJECTIVES 1/3 done: [x] o1 read config, [>] o2 add route, [ ] o3 tests"
    d.set_objectives(_objs(("o1", "only this", "done")))
    assert d.line() == "OBJECTIVES 1/1 done: [x] o1 only this"


def test_the_line_is_bounded():
    d = Discipline()
    d.set_objectives({"objectives": [{"id": f"o{i}", "text": "x" * 200, "status": "pending"} for i in range(12)]})
    assert len(d.line()) <= 600 and d.line().endswith("...")


def test_bad_checklists_are_refused_and_leave_the_old_one():
    d = Discipline()
    d.set_objectives(_objs(("o1", "keep me", "pending")))
    assert d.set_objectives({"objectives": []})["status"] == "FAILURE"
    assert d.set_objectives({})["status"] == "FAILURE"
    assert d.set_objectives({"objectives": [{"id": "x", "text": "t"}] * 13})["status"] == "FAILURE"
    assert d.set_objectives(_objs(("o1", "t", "finished")))["status"] == "FAILURE"
    dropped = d.set_objectives(_objs(("o1", "t", "dropped")))
    assert dropped["status"] == "FAILURE" and "note" in dropped["message"]
    assert d.objectives[0]["text"] == "keep me"


def test_a_dropped_objective_with_a_note_counts_as_closed():
    d = Discipline()
    d.set_objectives(_objs(("o1", "a", "done"), ("o2", "b", "dropped", "not needed: covered by o1")))
    assert d.open_objectives() == []
    assert "(dropped: not needed: covered by o1)" in d.recap()


# ---------------------------------------------------------------- debrief

def test_a_debrief_with_nothing_open_ends_the_mission_with_its_summary():
    d = Discipline()
    d.set_objectives(_objs(("o1", "build the CLI", "done")))
    result, answer = d.review_debrief({"summary": "Built the CLI.", "verification": "pytest: 4 passed"}, [])
    assert result["status"] == "SUCCESS"
    assert answer.startswith("Built the CLI.")
    assert "Verification: pytest: 4 passed" in answer and "[x] o1: build the CLI" in answer


def test_a_debrief_is_refused_with_exactly_what_is_missing():
    d = Discipline()
    d.set_objectives(_objs(("o1", "a", "done"), ("o2", "b", "in_progress")))
    result, answer = d.review_debrief({"summary": "done"}, ["app.py", "cli.py"])
    assert answer is None and result["status"] == "FAILURE"
    assert "o2" in result["message"] and "app.py, cli.py" in result["message"]


def test_the_model_cannot_declare_its_own_files_verified():
    d = Discipline()
    result, answer = d.review_debrief({"summary": "ok", "verification": ["app.py"]}, ["app.py"])
    assert answer is None and "app.py" in result["message"]


def test_a_debrief_needs_a_summary():
    result, answer = Discipline().review_debrief({"verification": "ran it"}, [])
    assert answer is None and "summary" in result["message"]


def test_after_repeated_refusals_it_is_accepted_but_marked_unverified():
    d = Discipline()
    d.set_objectives(_objs(("o1", "a", "pending")))
    for _ in range(MAX_REFUSALS - 1):
        assert d.review_debrief({"summary": "s"}, [])[1] is None
    result, answer = d.review_debrief({"summary": "s"}, [])
    assert result["status"] == "SUCCESS"
    assert answer.startswith("Debrief not verified: objectives not done or dropped: o1")


def test_no_objectives_means_only_the_verification_gate_applies():
    assert Discipline().review_debrief({"summary": "s"}, [])[1] == "s"


def test_state_survives_a_checkpoint():
    d = Discipline()
    d.set_objectives(_objs(("o1", "a", "pending")))
    d.review_debrief({"summary": "s"}, [])
    again = Discipline()
    again.restore(d.to_checkpoint())
    assert again.objectives == d.objectives and again.refusals == 1
    again.restore(None)
    assert again.refusals == 1


# ---------------------------------------------------------------- wiring

def test_both_tools_are_offered_to_missions_but_not_to_single_turn_chat():
    names = {row[0] for row in tool_registry._RAVEN_TOOL_TABLE}
    assert {"MissionObjectivesRequest", "MissionCompleteRequest"} <= names
    assert "MissionCompleteRequest" in tool_registry.SINGLE_TURN_EXCLUDED
    assert "missioncompleterequest" not in tool_registry.single_turn_tools()
    assert {"missionobjectivesrequest", "missioncompleterequest"} <= agent_loop.ALLOWED_TOOLS


def test_the_loop_checks_the_debrief_against_its_own_tracking_and_ends_on_it():
    src = inspect.getsource(agent_loop.AgentLoop)
    assert "_discipline.review_debrief(\n                        payload, pending_verification(_written_files, _verified_files)" in src
    assert "if _debrief_answer is not None:" in src and "ans = _debrief_answer" in src
    # Words alone, or a quiet stall, do not end a mission whose objectives are open.
    assert "if any(ind in _ans_l for ind in COMPLETION_INDICATORS) and _still_open:" in src
    assert "if _gate_ok and _discipline.open_objectives():" in src
    # The objectives line reaches the model every turn and the state is checkpointed.
    assert "_objectives_line = _discipline.line()" in src
    assert "**_discipline.to_checkpoint()" in src and "_discipline.restore(cp)" in src

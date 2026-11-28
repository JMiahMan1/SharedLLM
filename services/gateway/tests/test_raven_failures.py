"""Learning from Raven's own failed missions.

Infrastructure failures are never lessons; model mistakes become "avoid"
lessons whose confidence grows when they recur, ``raven_failures`` cases the
model is re-tested on, and contrastive rows against a teacher's solution. Mission
text is scrubbed (household names from Identity) before any of it is kept, and
the agent loop stops a looping mission early with the cause named.
"""
from __future__ import annotations

import asyncio
import json
import pathlib

import pytest
import teacher_samples as samples

from services.gateway import agent_loop
from services.gateway.trainer import curriculum, raven_failures, runner, teacher
from services.gateway.trainer import dream as dream_mod
from services.gateway.trainer import teacher_scrub as scrub
from services.gateway.trainer import teacher_traces as traces

NAME = "Placeholderperson"
ROSTER = [{"username": "pp", "display_name": NAME, "nextcloud_user": None}, {"username": "default"}]


def _event(tool: str, payload: dict, ok: bool, result: str) -> dict:
    kind = "result_success" if ok else "result_error"
    return {"type": kind, "raw_type": kind, "data": "x", "raw_data": result, "tool": tool, "payload": json.dumps(payload)}


def _mission(mission_id: int, goal: str, result: str, events: list[dict], status: str = "failed") -> dict:
    return {"id": mission_id, "status": status, "proposed_mission": goal, "result": result,
            "output_log": json.dumps(events), "completed_at": "2026-11-21T10:00:00Z"}


def _looping() -> dict:
    """The shape of the trivial mission that ran to iteration 38: the same failing read, again and again."""
    events = []
    for _ in range(6):
        events.append(_event("workspacefilereadrequest", {"file_path": "test.txt"}, False, "No such file or directory: test.txt"))
        events.append(_event("workspaceshellrequest", {"command": "ls"}, True, "README.md"))
    return _mission(1, f"Create test.txt for {NAME}", "Aborted by user", events)


def _system(text: str) -> dict:
    return {"type": "system", "data": text, "timestamp": "2026-11-21T10:00:00Z"}


def _mission_nine() -> dict:
    """Killed while looping on a trivial file task, iteration 38 of 60, varied calls.

    Nothing here repeats the same call or error often enough for the detector, and
    there is no path error: only the iteration count says this was a loop.
    """
    events = []
    for iteration in range(1, 39):
        events.append(_system(f"Agent loop iteration {iteration}/60 started."))
        if iteration == 12:
            events.append(_event("workspacefilereadrequest", {"file_path": "notes.txt"}, False, "No such file or directory"))
        elif iteration == 30:
            events.append(_event("workspaceshellrequest", {"command": "pytest -q"}, False, "1 failed, 2 passed"))
        else:
            events.append(_event("workspaceshellrequest", {"command": f"ls step{iteration}"}, True, "ok"))
    return _mission(9, "Tidy the notes", "MISSION TERMINATED: User requested cancellation via control plane.", events, status="killed")


def _mission_ten() -> dict:
    """The same invented script path, run three times, then killed."""
    events = [_system(f"Agent loop iteration {iteration}/60 started.") for iteration in range(1, 13)]
    for _ in range(3):
        events.append(_event(
            "workspaceshellrequest", {"command": "python3 test_lessons.py"}, False,
            "python3: can't open file '/workspace/test_lessons.py': [Errno 2] No such file or directory",
        ))
    events.append(_event("workspaceshellrequest", {"command": "ls"}, True, "README.md"))
    return _mission(10, "Run the lessons tests", "MISSION TERMINATED: User requested cancellation via control plane.",
                    events, status="killed")


def _capped(iteration: int = 60) -> dict:
    events = [_system(f"Agent loop iteration {n}/60 started.") for n in range(iteration - 2, iteration + 1)]
    events.append(_event("workspaceshellrequest", {"command": "ls"}, True, "ok"))
    return _mission(11, "Keep working", "Tool failed", events)


MISSIONS = {
    1: _looping(),
    2: _mission(2, "Summarise the logs", "LocalModelUnavailable: the model host is busy", []),
    3: _mission(3, "Fix the pager", "Tool failed", [
        _event("workspacefilepatchrequest", {"file_path": "src/pagerr.py"}, False, "FileNotFoundError: src/pagerr.py does not exist"),
    ]),
    4: _mission(4, "Fix the pager again", "Tool failed", [
        _event("workspacefilepatchrequest", {"file_path": "src/pagerx.py"}, False, "FileNotFoundError: src/pagerx.py does not exist"),
    ]),
    5: _mission(5, "Done already", "", [], status="completed"),
    6: _mission(7, "Restart sync", "Cancelled: orphaned mission with no backing job", []),
}


def _fetch(missions: dict):
    async def fetch(path: str):
        if path.startswith("/api/internal/name-roster"):
            return ROSTER
        if path.startswith("/api/raven/missions?"):
            return [{k: v for k, v in m.items() if k != "output_log"} for m in missions.values()]
        return next(m for m in missions.values() if str(m["id"]) == path.rsplit("/", 1)[-1])

    return fetch


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "DATA_DIR", tmp_path)
    return tmp_path


class _Written(list):
    """System-learnings writes; SIM ledger writes are kept on ``.sim``."""

    def __init__(self) -> None:
        super().__init__()
        self.sim: list[dict] = []


@pytest.fixture
def rag(monkeypatch):
    written = _Written()

    async def fake_request(method, path, **kwargs):
        if path == "/rag/sim/history":
            return {"problems": [], "count": 0}
        if path == "/rag/sim/record":
            written.sim.append(kwargs["body"])
            return {}
        written.append(kwargs["body"])
        return {}

    monkeypatch.setattr(dream_mod, "request", fake_request)
    return written


def test_causes_are_classified_infrastructure_first():
    def kind(mission):
        return raven_failures.classify(mission, raven_failures.mission_events(mission))["kind"]

    assert kind(MISSIONS[1]) == "loop"
    assert kind(MISSIONS[2]) == "infrastructure"
    assert kind(MISSIONS[3]) == "hallucinated_path"
    assert kind(MISSIONS[6]) == "infrastructure"
    assert kind(_mission(9, "x", "Unknown action: MagicRequest", [])) == "wrong_tool"
    assert kind(_mission(9, "x", "ERROR: looping: workspaceshellrequest::'ls' repeated 5 times", [])) == "loop"
    timeouts = [_event("workspaceshellrequest", {"command": "pytest"}, False, "ClientTimeout: timed out")] * 5
    assert kind(_mission(9, "x", "Tool failed", timeouts)) == "infrastructure", "a repeated timeout is not a model loop"


def test_a_kill_while_looping_is_a_loop_not_unexplained():
    finding = raven_failures.classify(_mission_nine(), raven_failures.mission_events(_mission_nine()))

    assert finding["kind"] == "loop", "38 of 60 iterations on a trivial task is a loop"
    assert "iteration 38/60" in finding["evidence"]
    assert "unexplained" not in finding["evidence"]


def test_repeating_an_invented_path_is_a_loop():
    mission = _mission_ten()
    finding = raven_failures.classify(mission, raven_failures.mission_events(mission))

    assert finding["kind"] == "loop"
    assert "test_lessons.py" in finding["evidence"]
    assert finding["tool"] == "workspaceshellrequest"


def test_one_missing_file_is_a_hallucinated_path():
    mission = _mission(12, "Fix the thing", "Tool failed", [
        _event("workspacefilepatchrequest", {"file_path": "src/invented.py"}, False,
               "FileNotFoundError: src/invented.py does not exist"),
    ])
    finding = raven_failures.classify(mission, raven_failures.mission_events(mission))

    assert finding["kind"] == "hallucinated_path"
    assert finding["tool"] == "workspacefilepatchrequest"


def test_repeating_the_same_failing_call_is_a_loop():
    mission = _mission(13, "Deploy", "Tool failed", [
        _event("workspaceshellrequest", {"command": "make deploy"}, False, "permission denied"),
    ] * 3)
    finding = raven_failures.classify(mission, raven_failures.mission_events(mission))

    assert finding["kind"] == "loop"
    assert "failed 3 times" in finding["evidence"]


def test_the_iteration_cap_is_recognised_from_the_log_and_the_verdict():
    from_log = raven_failures.classify(_capped(), raven_failures.mission_events(_capped()))
    from_verdict = raven_failures.classify(
        _mission(14, "Keep going", "Reached the maximum number of iterations (60).",
                 [_event("workspaceshellrequest", {"command": "ls"}, True, "ok")]),
        raven_failures.mission_events(_mission(14, "Keep going", "Reached the maximum number of iterations (60).",
                                               [_event("workspaceshellrequest", {"command": "ls"}, True, "ok")])),
    )

    assert from_log["kind"] == "iteration_cap" and "60/60" in from_log["evidence"]
    assert from_verdict["kind"] == "iteration_cap"


def test_a_kill_before_any_loop_is_not_called_a_loop():
    mission = _mission(15, "Quick look", "MISSION TERMINATED: User requested cancellation via control plane.",
                       [_event("workspaceshellrequest", {"command": "ls"}, True, "ok")])
    finding = raven_failures.classify(mission, raven_failures.mission_events(mission))

    assert finding["kind"] == "aborted_unexplained"


def test_a_double_encoded_or_wrapped_log_is_still_read():
    events = [_event("workspaceshellrequest", {"command": "ls"}, True, "ok")]
    wrapped = {"events": json.dumps(events)}
    double = json.dumps(json.dumps(events))

    assert [c["tool"] for c in raven_failures.mission_events({"output_log": wrapped})] == ["workspaceshellrequest"]
    assert [c["tool"] for c in raven_failures.mission_events({"output_log": double})] == ["workspaceshellrequest"]
    assert raven_failures.iteration_span({"output_log": json.dumps([_system("Agent loop iteration 7/60 started.")])}) == (7, 60)


def test_a_pass_learns_from_mistakes_and_never_from_infrastructure(data_dir, rag):
    done = asyncio.run(raven_failures.run_pass(fetch=_fetch(MISSIONS)))

    assert done["examined"] == 5, "only failed missions"
    assert done["infrastructure"] == 2
    assert done["by_kind"] == {"loop": 1, "infrastructure": 2, "hallucinated_path": 2}
    kinds = [json.loads(body["content"])["topic"] for body in rag]
    assert "raven failure: infrastructure" not in kinds
    assert all(body["metadata"]["type"] == "avoid" for body in rag)
    assert len(rag.sim) == len(rag) == 3, "every avoid-lesson is also recorded in the SIM ledger"
    assert all(body["harness"] == "raven" for body in rag.sim)
    assert all(body["outcome_status"] == "regressed" for body in rag.sim)
    assert NAME not in json.dumps(rag) and NAME not in json.dumps(rag.sim), "names are scrubbed"
    assert NAME not in json.dumps(raven_failures.read_state()), "names are scrubbed"

    again = asyncio.run(raven_failures.run_pass(fetch=_fetch(MISSIONS)))
    assert again["examined"] == 0, "a mission is read once"


def test_a_recurring_mistake_is_one_lesson_with_growing_confidence(data_dir, rag):
    asyncio.run(raven_failures.run_pass(fetch=_fetch(MISSIONS)))

    paths = [b for b in rag if "hallucinated path" in json.loads(b["content"])["rule"]]
    assert len({b["metadata"]["id"] for b in paths}) == 1
    assert [b["metadata"]["confidence"] for b in paths] == [0.4, 0.5]
    assert "list or search the workspace" in paths[-1]["metadata"]["rule"]


def test_failures_become_cases_checked_against_the_failing_call(data_dir, rag):
    asyncio.run(raven_failures.run_pass(fetch=_fetch(MISSIONS)))

    cases = curriculum.derive_curriculum(buckets=["raven_failures"])

    assert {c.bucket for c in cases} == {"raven_failures"}
    path_case = next(c for c in cases if "pagerr" in json.dumps(c.checks))
    assert path_case.checks["must_not_include"] == ["src/pagerr.py"]
    good = "1. WorkspaceSearchRequest for pager\n2. patch src/pager.py\n3. run pytest to verify"
    assert runner.score_answer(path_case, good)[0] is True
    assert runner.score_answer(path_case, "patch src/pagerr.py and finish")[0] is False


def test_a_missing_roster_stops_the_pass(data_dir):
    async def fetch(path):
        return []  # an empty roster, and no missions

    with pytest.raises(RuntimeError, match="roster is empty"):
        asyncio.run(raven_failures.run_pass(fetch=fetch))


def test_a_failed_attempt_pairs_with_a_teacher_solution(data_dir, rag):
    session = {"id": "s", "directory": samples.ROOT_DIR, "model": json.dumps(samples.MODEL)}
    config = scrub.ScrubConfig(names=(NAME,), names_loaded=True)
    teacher.import_traces([scrub.scrub_trace(t, config) for t in traces.opencode_tasks(session, samples.opencode_messages())],
                          uploader="admin")
    similar = {3: _mission(3, "Fix the off-by-one in the pager so the last page shows", "Tool failed", [
        _event("workspacefilepatchrequest", {"file_path": "src/pagerr.py"}, False, "FileNotFoundError: src/pagerr.py does not exist"),
    ])}
    asyncio.run(raven_failures.run_pass(fetch=_fetch(similar)))

    result = teacher.export_finetune("You are Raven.", eval_share=0.0)

    assert result["contrastive_rows"] == 1
    row = json.loads(pathlib.Path(result["paths"]["contrastive"]).read_text().splitlines()[0])
    assert row["prompt"][1]["content"].startswith("Fix the off-by-one")
    assert row["rejected"][0]["tool_calls"][0]["function"]["name"] == "WorkspaceFilePatchRequest"
    assert row["chosen"][-1]["content"].startswith("Fixed")
    assert row["meta"]["failure_kind"] == "hallucinated_path"


def test_the_summary_counts_by_kind_and_tracks_the_bucket(data_dir, rag):
    asyncio.run(raven_failures.run_pass(fetch=_fetch(MISSIONS)))
    runner.save_report({"id": "eval-1", "started_at": "2026-11-21T00:00:00Z", "totals": {},
                        "by_bucket": {"raven_failures": {"cases": 3, "passed": 1, "pass_rate": 0.333}}})

    summary = raven_failures.summary()

    assert summary["learned_from"] == 3 and summary["infrastructure"] == 2
    assert summary["scores"][0]["passed"] == 1
    assert teacher.summary()["raven_failures"]["lessons"] == 2


# --- the loop detector in the agent loop ------------------------------------


def test_the_detector_catches_alternating_dead_ends():
    history = []
    for _ in range(5):
        history.append(("workspacefilereadrequest::'test.txt'", False, "No such file or directory: test.txt"))
        history.append(("workspaceshellrequest::'ls'", True, ""))

    reason = agent_loop.detect_mission_loop(history)

    assert reason is not None and reason.startswith("looping:")
    assert "repeated" in reason


def test_the_detector_catches_one_error_from_different_calls():
    history = [(f"workspaceshellrequest::'cmd {i}'", False, "permission denied") for i in range(4)]

    assert agent_loop.detect_mission_loop(history) == "looping: error 'permission denied' repeated 4 times"


def test_edit_and_test_cycles_are_not_a_loop():
    history = []
    for _ in range(6):
        history.append(("workspacefilewriterequest::'game.py'", True, ""))
        history.append((f"workspaceshellrequest::'pytest -k case{_}'", True, ""))

    assert agent_loop.detect_mission_loop(history) is None


def test_error_digests_fold_numbers():
    assert agent_loop.error_digest({"message": "line 12: bad"}) == agent_loop.error_digest({"message": "line 40: bad"})
    assert agent_loop.error_digest("not a dict") == ""

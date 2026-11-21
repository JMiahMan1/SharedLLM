"""Teacher traces in the trainer: stored safely, turned into lessons, cases and rows.

The store refuses anything that is not a current, clean scrub; lessons and
cases come only from verified successes; the fine-tuning rows use Raven's own
tools and system prompt; and the routes keep the admin policy.
"""
from __future__ import annotations

import json
import pathlib

import pytest
import teacher_fixture
import teacher_samples as samples
from fastapi.testclient import TestClient

from services.gateway.main import app
from services.gateway.trainer import curriculum, runner, teacher
from services.gateway.trainer import dream as dream_mod
from services.gateway.trainer import teacher_scrub as scrub
from services.gateway.trainer import teacher_traces as traces


@pytest.fixture
def data_dir(tmp_path, monkeypatch) -> pathlib.Path:
    monkeypatch.setattr(runner, "DATA_DIR", tmp_path)
    return tmp_path


def _scrubbed() -> list[dict]:
    session = {"id": "ses_synthetic01", "directory": samples.ROOT_DIR, "model": json.dumps(samples.MODEL)}
    config = scrub.ScrubConfig(names=("Zebulonia",), names_loaded=True)
    return [scrub.scrub_trace(t, config) for t in traces.opencode_tasks(session, samples.opencode_messages())]


def test_scrubbed_traces_are_stored_and_listed(data_dir):
    result = teacher.import_traces(_scrubbed(), uploader="admin")

    assert len(result["accepted"]) == 3 and not result["refused"]
    rows = teacher.list_traces()
    assert {row["outcome"] for row in rows} == {"success", "failure", "unverified"}
    assert all("steps" not in row for row in rows), "the list must not carry full steps"
    again = teacher.import_traces(_scrubbed(), uploader="admin")
    assert len(again["updated"]) == 3 and not again["accepted"]


def test_a_trace_with_a_secret_left_in_it_is_refused(data_dir):
    trace = _scrubbed()[0]
    secret = teacher_fixture.load()["secrets"]["openrouter_key"]["value"]
    trace["steps"][0]["output"] = f"key {secret}"

    result = teacher.import_traces([trace], uploader="admin")

    assert result["accepted"] == []
    assert "still contains secrets (api_key" in result["refused"][0]["reason"]
    assert secret not in json.dumps(result)
    assert not list((data_dir / "teacher" / "traces").glob("*.json"))


def test_a_trace_scrubbed_by_other_rules_is_refused(data_dir):
    trace = _scrubbed()[0]
    trace["scrub"]["version"] = "0"

    result = teacher.import_traces([trace], uploader="admin")

    assert "rules version" in result["refused"][0]["reason"]


def test_a_dropped_trace_may_not_carry_content(data_dir):
    stub = {**_scrubbed()[0], "scrub": {"version": scrub.SCRUB_VERSION, "status": "dropped"}}

    result = teacher.import_traces([stub], uploader="admin")

    assert "must not carry content" in result["refused"][0]["reason"]


def test_a_dropped_stub_is_listed_with_its_reason(data_dir):
    config = scrub.ScrubConfig()
    raw = traces.opencode_tasks(
        {"id": "s", "directory": samples.ROOT_DIR, "model": json.dumps(samples.MODEL)}, samples.opencode_messages()
    )[0]
    raw["goal"] = "Decrypt the stored keys"
    stub = scrub.scrub_trace(raw, config)

    teacher.import_traces([stub], uploader="admin")

    (row,) = teacher.list_traces()
    assert row["scrub_status"] == "dropped" and row["drop_reason"] == "sensitive_goal"
    assert row["case_id"] is None


def test_only_verified_successes_become_lessons(data_dir, monkeypatch):
    teacher.import_traces(_scrubbed(), uploader="admin")
    sent: list[dict] = []

    async def fake_request(method, path, **kwargs):
        sent.append({"path": path, **kwargs})
        return {"status": "SUCCESS"}

    monkeypatch.setattr(dream_mod, "request", fake_request)

    import asyncio

    result = asyncio.run(teacher.write_lessons())

    assert len(result["written"]) == 1
    reasons = {item["reason"] for item in result["skipped"]}
    assert any("not a verified success" in r for r in reasons)
    payload = sent[0]["body"]
    assert sent[0]["path"] == "/rag/ingest"
    assert payload["collection_name"] == "system_learnings"
    meta = payload["metadata"]
    assert meta["type"] == "worked_example" and meta["outcome"] == "success"
    assert meta["rule"].startswith('For a task like "Fix the off-by-one')
    assert "checked with `pytest services/gateway/tests/test_pager.py -q`" in meta["rule"]
    assert len(meta["rule"]) <= teacher.MAX_LESSON_RULE_CHARS
    assert {"teacher", "worked_example", "services/gateway"} <= set(meta["tags"])

    second = asyncio.run(teacher.write_lessons())
    assert second["written"] == [], "a lesson is written once"


def test_a_near_duplicate_task_does_not_get_a_second_lesson(data_dir, monkeypatch):
    first = _scrubbed()[0]
    twin = json.loads(json.dumps(first))
    twin["id"] = "tt-" + "b" * 12
    twin["goal"] = first["goal"] + " please"
    teacher.import_traces([first, twin], uploader="admin")

    async def fake_request(method, path, **kwargs):
        return {}

    monkeypatch.setattr(dream_mod, "request", fake_request)
    import asyncio

    result = asyncio.run(teacher.write_lessons())

    assert len(result["written"]) == 1
    assert any("already exists" in item["reason"] for item in result["skipped"])


def test_a_refused_lesson_write_is_reported_not_counted(data_dir, monkeypatch):
    teacher.import_traces(_scrubbed(), uploader="admin")

    async def refuse(method, path, **kwargs):
        raise dream_mod.RagRejected("RAG /rag/ingest answered 422: bad", status=422)

    monkeypatch.setattr(dream_mod, "request", refuse)
    import asyncio

    result = asyncio.run(teacher.write_lessons())

    assert result["written"] == [] and result["failed"]
    assert not (data_dir / "teacher" / "lessons.json").exists()


def test_a_verified_trace_becomes_a_checkable_case(data_dir):
    teacher.import_traces(_scrubbed(), uploader="admin")

    (case,) = teacher.teacher_cases()

    assert case.bucket == "teacher"
    assert case.checks["must_include"] == ["pager.py"]
    assert case.checks["must_match"] == [r"(?i)\bpytest\b"]
    good = "1. read services/gateway/pager.py\n2. fix range\n3. run pytest services/gateway/tests/test_pager.py"
    assert runner.score_answer(case, good)[0] is True
    assert runner.score_answer(case, "I would look around.")[0] is False


def test_the_curriculum_carries_the_teacher_bucket(data_dir):
    teacher.import_traces(_scrubbed(), uploader="admin")

    only = curriculum.derive_curriculum(buckets=["teacher"])

    assert "teacher" in curriculum.BUCKETS
    assert [case.bucket for case in only] == ["teacher"]
    assert curriculum.derive_curriculum(buckets=["writing"], include_teacher=True)[0].bucket == "writing"


def test_teacher_failures_become_a_teacher_gap_proposal():
    report = {"cases": [], "by_bucket": {"teacher": {"cases": 2, "passed": 0, "failed_ids": ["teacher-a", "teacher-b"]}}}

    kinds = {p["kind"] for p in dream_mod.proposals_from_failures(report)}

    assert "teacher_gap" in kinds


def test_steps_map_onto_ravens_own_tools():
    first = _scrubbed()[0]
    calls = [teacher.raven_call(step) for step in first["steps"]]

    assert [c[0] for c in calls] == [
        "WorkspaceFileReadRequest", "WorkspaceSearchRequest", "WorkspaceFilePatchRequest",
        "WorkspaceShellRequest", "GitOperationRequest",
    ]
    assert calls[2][1]["chunks"] == [{"old_text": "range(n - 1)", "new_text": "range(n)"}]
    assert calls[4][1] == {"action": "commit", "commit_message": "fix(pager): show the last page"}


def test_openai_rows_carry_tool_calls_results_and_the_system_prompt():
    row = teacher.finetune_row(_scrubbed()[0], "You are Raven.", fmt="openai")

    messages = row["messages"]
    assert messages[0] == {"role": "system", "content": "You are Raven."}
    assert messages[1]["role"] == "user"
    call = messages[2]["tool_calls"][0]
    assert call["function"]["name"] == "WorkspaceFileReadRequest"
    assert json.loads(call["function"]["arguments"])["file_path"] == "services/gateway/pager.py"
    assert messages[3] == {"role": "tool", "tool_call_id": call["id"], "name": "WorkspaceFileReadRequest",
                           "content": "def pages(n): ..."}
    assert messages[-1]["role"] == "assistant" and messages[-1]["content"].startswith("Fixed")
    names = {tool["function"]["name"] for tool in row["tools"]}
    assert "WorkspaceShellRequest" in names
    shell = next(t for t in row["tools"] if t["function"]["name"] == "WorkspaceShellRequest")
    assert "command" in shell["function"]["parameters"]["properties"]


def test_sharegpt_rows_use_hermes_tool_tags():
    row = teacher.finetune_row(_scrubbed()[0], "You are Raven.", fmt="sharegpt")

    turns = row["conversations"]
    assert turns[0]["from"] == "system" and "<tools>" in turns[0]["value"]
    assert turns[2]["from"] == "gpt" and "<tool_call>" in turns[2]["value"]
    assert turns[3]["from"] == "tool" and turns[3]["value"].startswith("<tool_response>")
    assert turns[-1]["from"] == "gpt"


def test_the_export_writes_train_and_eval_files(data_dir):
    teacher.import_traces(_scrubbed(), uploader="admin")

    result = teacher.export_finetune("You are Raven.", eval_share=0.0)

    assert result["train_rows"] == 1 and result["eval_rows"] == 0
    assert result["skipped"] == {"outcome_failure": 1, "outcome_unverified": 1}
    lines = pathlib.Path(result["paths"]["train"]).read_text().splitlines()
    assert json.loads(lines[0])["meta"]["outcome"] == "success"
    assert teacher.summary()["exports"][0]["train_rows"] == 1


def test_an_empty_system_prompt_stops_the_export(data_dir):
    with pytest.raises(teacher.TeacherError, match="system prompt is empty"):
        teacher.export_finetune("  ")


def test_the_summary_tracks_teacher_scores_over_time(data_dir):
    teacher.import_traces(_scrubbed(), uploader="admin")
    for index, (passed, stamp) in enumerate(((0, "2026-11-01T00:00:00Z"), (1, "2026-11-02T00:00:00Z"))):
        runner.save_report(
            {"id": f"eval-{index}", "started_at": stamp, "model": "local-moe", "totals": {},
             "by_bucket": {"teacher": {"cases": 1, "passed": passed, "pass_rate": float(passed)}}},
        )

    summary = teacher.summary()

    assert summary["traces"]["total"] == 3
    assert summary["traces"]["by_outcome"]["success"] == 1
    assert summary["eval_cases"] == 1
    assert [row["pass_rate"] for row in summary["scores"]] == [0.0, 1.0]
    assert summary["lessons"]["eligible"] == 1


# --- routes ------------------------------------------------------------------


def test_teacher_routes_are_admin_only(data_dir):
    anonymous = TestClient(app)

    assert anonymous.get("/api/trainer/teacher").status_code in (401, 403)
    assert anonymous.post("/api/trainer/teacher/traces", json={"traces": []}).status_code in (401, 403)


def test_an_admin_uploads_lists_and_reads_traces(client, data_dir):
    resp = client.post("/api/trainer/teacher/traces", json={"traces": _scrubbed()})
    assert resp.status_code == 200, resp.text
    assert len(resp.json()["accepted"]) == 3

    listed = client.get("/api/trainer/teacher/traces", params={"outcome": "success"}).json()["traces"]
    assert len(listed) == 1
    detail = client.get(f"/api/trainer/teacher/traces/{listed[0]['id']}").json()
    assert detail["steps"]
    assert client.get("/api/trainer/teacher/traces/tt-000000000000").status_code == 404
    assert client.delete(f"/api/trainer/teacher/traces/{listed[0]['id']}").status_code == 200


def test_the_dataset_route_uses_ravens_prompt_and_fails_without_it(client, data_dir, monkeypatch):
    client.post("/api/trainer/teacher/traces", json={"traces": _scrubbed()})

    async def missing():
        raise teacher.TeacherError("raven_autonomous_protocol is empty in Identity; seed the prompts first")

    monkeypatch.setattr(teacher, "raven_system_prompt", missing)
    resp = client.post("/api/trainer/teacher/dataset", json={})
    assert resp.status_code == 503 and "raven_autonomous_protocol" in resp.text

    async def present():
        return "You are Raven."

    monkeypatch.setattr(teacher, "raven_system_prompt", present)
    resp = client.post("/api/trainer/teacher/dataset", json={"format": "sharegpt", "eval_share": 0})
    assert resp.status_code == 200, resp.text
    assert resp.json()["train_rows"] == 1


def test_the_lessons_route_reports_what_it_wrote(client, data_dir, monkeypatch):
    client.post("/api/trainer/teacher/traces", json={"traces": _scrubbed()})

    async def fake_request(method, path, **kwargs):
        return {}

    monkeypatch.setattr(dream_mod, "request", fake_request)
    resp = client.post("/api/trainer/teacher/lessons", json={"dry_run": True})

    assert resp.status_code == 200, resp.text
    assert resp.json()["dry_run"] is True and len(resp.json()["written"]) == 1
    assert client.get("/api/trainer/teacher").json()["lessons"]["written"] == 0

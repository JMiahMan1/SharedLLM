"""``/api/trainer`` must be wired, authenticated, and honest about failures.

The catalogue and the curriculum are readable by any signed-in user; anything
that spends the model's time or writes a workspace is admin-only; drafting a
sermon is the caller's own workspace business. The heavy work is monkeypatched
here on purpose -- these tests are about the surface and the policy, not about
the model.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from services.gateway.main import app
from services.gateway.trainer import curriculum, facilitator, routes, runner, selffix
from services.gateway.trainer import sermon as sermon_mod


def test_the_router_is_part_of_the_application():
    assert len(routes.router.routes) == 13
    registered = client_paths()
    assert "/api/trainer/catalog" in registered


def client_paths() -> set[str]:
    """Every path the app serves, flattening FastAPI's deferred includes."""
    from services.gateway.trainer.catalog import _iter_routes

    return {getattr(route, "path", "") for route in _iter_routes(app.routes)}


def test_the_catalogue_is_readable_by_a_signed_in_user(client):
    resp = client.get("/api/trainer/catalog")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["counts"]["total"] > 100
    assert "/api/trainer/catalog" in {feature["path"] for feature in body["features"]}


def test_the_curriculum_lists_buckets_and_cases(client):
    resp = client.get("/api/trainer/curriculum", params={"buckets": "writing", "limit": 5})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["summary"]["total"] <= 5
    assert {case["bucket"] for case in body["cases"]} == {"writing"}
    assert "repair" in body["buckets"]


def test_running_an_evaluation_is_admin_only(monkeypatch):
    """No identity header: the strict resolver finds nothing, so the gate refuses."""
    seen: list[int] = []

    async def fake_run(cases, **kwargs):
        seen.append(len(cases))
        return {"id": "eval-1", "totals": {"cases": len(cases)}}

    monkeypatch.setattr(runner, "run_curriculum", fake_run)

    anonymous = TestClient(app)
    resp = anonymous.post("/api/trainer/eval", json={"limit": 2})

    assert resp.status_code in (401, 403)
    assert seen == []


def test_an_admin_can_run_a_bounded_evaluation(client, monkeypatch):
    async def fake_run(cases, **kwargs):
        return {"id": "eval-1", "totals": {"cases": len(cases)}, "by_bucket": {}, "model": "m",
                "stopped_reason": None}

    monkeypatch.setattr(runner, "run_curriculum", fake_run)

    resp = client.post("/api/trainer/eval", json={"buckets": ["repair"], "limit": 2})

    assert resp.status_code == 200, resp.text
    assert resp.json()["totals"]["cases"] == 2


def test_a_repair_returns_the_result_the_workspace_proved(client, monkeypatch):
    async def fake_repair(workspace_id, **kwargs):
        return selffix.RepairResult(
            status="repaired", workspace_id=workspace_id, path=kwargs["path"],
            attempts=1, tests_passed=True,
        )

    monkeypatch.setattr(selffix, "repair_workspace", fake_repair)

    resp = client.post(
        "/api/trainer/repair",
        json={"workspace_id": "sermons", "path": "math_utils.py", "error_text": "boom"},
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "repaired"


def test_a_sermon_is_drafted_for_the_calling_user(client, monkeypatch):
    seen: dict = {}

    async def fake_compose(workspace_id, **kwargs):
        seen.update({"workspace_id": workspace_id, **kwargs})
        return sermon_mod.Sermon(
            title="Mercy", passage=kwargs["passage"], translation="kjv",
            path="sermons/mercy.md", words=120,
        )

    monkeypatch.setattr(sermon_mod, "compose_sermon", fake_compose)

    resp = client.post(
        "/api/trainer/sermon", json={"workspace_id": "sermons", "passage": "Luke 15:11-32"}
    )

    assert resp.status_code == 200, resp.text
    assert seen["workspace_id"] == "sermons"
    assert seen["passage"] == "Luke 15:11-32"
    assert resp.json()["path"] == "sermons/mercy.md"


def test_an_empty_passage_is_a_400_not_a_crash(client):
    resp = client.post("/api/trainer/sermon", json={"workspace_id": "sermons", "passage": ""})

    assert resp.status_code == 400


def test_an_illustration_returns_the_editors_answer(client, monkeypatch):
    async def fake_illustrate(workspace_id, **kwargs):
        return {"status": "SUCCESS", "output_path": "art/out.png", "message": "", "model": "m"}

    monkeypatch.setattr(sermon_mod, "illustrate", fake_illustrate)

    resp = client.post(
        "/api/trainer/illustrate",
        json={"workspace_id": "sermons", "image_path": "art/in.png", "prompt": "watercolour"},
    )

    assert resp.status_code == 200
    assert resp.json()["output_path"] == "art/out.png"


def test_the_backlog_is_admin_only_and_returns_proposals(client, monkeypatch):
    monkeypatch.setattr(
        facilitator,
        "read_backlog",
        lambda: {"generated_at": "2026-01-01T00:00:00Z", "eval_report_id": "eval-1",
                 "proposals": [{"id": "p1"}], "lessons": []},
    )

    resp = client.get("/api/trainer/backlog")

    assert resp.status_code == 200
    assert resp.json()["proposals"] == [{"id": "p1"}]

    anonymous = TestClient(app)
    assert anonymous.get("/api/trainer/backlog").status_code in (401, 403)


def test_a_cycle_can_be_started_with_a_budget(client, monkeypatch):
    seen: dict = {}

    async def fake_cycle(**kwargs):
        seen.update(kwargs)
        return {"status": "SUCCESS", "proposals": [], "lessons": []}

    monkeypatch.setattr(facilitator, "cycle", fake_cycle)

    resp = client.post("/api/trainer/cycle", json={"limit": 3, "budget_s": 60, "apply_lessons": False})

    assert resp.status_code == 200, resp.text
    assert seen["limit"] == 3
    assert seen["budget_s"] == 60.0
    assert seen["apply_lessons"] is False


def test_reports_are_listed_and_a_missing_one_404s(client, monkeypatch):
    monkeypatch.setattr(runner, "list_reports", lambda limit=20: [{"id": "eval-1"}])
    monkeypatch.setattr(runner, "load_report", lambda report_id: None)

    assert client.get("/api/trainer/reports").json()["reports"] == [{"id": "eval-1"}]
    assert client.get("/api/trainer/reports/eval-nope").status_code == 404


def test_the_curriculum_endpoint_never_returns_a_case_it_cannot_score(client):
    body = client.get("/api/trainer/curriculum").json()

    for case in body["cases"]:
        assert case["checks"]
        assert case["bucket"] in curriculum.BUCKETS

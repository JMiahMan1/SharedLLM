"""The studio's routes must be wired, authenticated, and thin.

Each handler is a small adapter over a module that is tested elsewhere, so these
tests are about the surface: the paths exist on the app, an anonymous caller is
refused, a studio refusal becomes a 400 with the reason (not a 500), and the
request body reaches the module with the caller's own identity attached.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from services.gateway import workspace_api as ws
from services.gateway.main import app
from services.gateway.studio import media, profiles, routes, runtime, works


def _paths() -> set[str]:
    from services.gateway.trainer.catalog import _iter_routes

    return {getattr(route, "path", "") for route in _iter_routes(app.routes)}


def test_every_studio_route_is_registered():
    declared = {route.path for route in routes.router.routes}
    missing = sorted(declared - _paths())

    assert "/api/studio/profile" in declared
    assert not missing, f"declared but not included on the app: {missing}"


def test_the_studio_needs_a_signed_in_user():
    anonymous = TestClient(app)

    assert anonymous.get("/api/studio/profile", params={"workspace_id": "w"}).status_code in (401, 403)
    assert anonymous.post("/api/studio/run", json={"workspace_id": "w"}).status_code in (401, 403)
    assert anonymous.post("/api/studio/deck", json={"workspace_id": "w", "markdown": "# x"}).status_code in (401, 403)
    # the newer surfaces are behind the same door
    assert anonymous.post("/api/studio/diagnose", json={"workspace_id": "w"}).status_code in (401, 403)
    assert anonymous.post("/api/studio/verify", json={"workspace_id": "w"}).status_code in (401, 403)
    assert anonymous.post("/api/studio/stl/inspect", json={"workspace_id": "w", "path": "a.stl"}).status_code in (401, 403)


def test_a_broken_shell_is_reported_not_raised(client, monkeypatch):
    async def fake_diagnose(workspace_id, *, user, profile=None):
        return {"workspace_id": workspace_id, "ok": False, "failed": ["home"],
                "probes": [], "hint": "HOME is /home/sharedllm (NOT writable)"}

    monkeypatch.setattr(routes.runtime, "diagnose", fake_diagnose)

    resp = client.post("/api/studio/diagnose", json={"workspace_id": "w1"})

    assert resp.status_code == 200, resp.text
    assert resp.json()["failed"] == ["home"]


def test_verify_passes_the_callers_choices_through(client, monkeypatch):
    seen: dict = {}

    async def fake_verify(workspace_id, *, user, run_tests, timeout):
        seen.update({"workspace_id": workspace_id, "user": user, "run_tests": run_tests})
        return {"ok": True, "shell": {"ok": True}, "program": {"ok": True}, "tests": None}

    monkeypatch.setattr(routes.runtime, "verify", fake_verify)

    body = client.post(
        "/api/studio/verify", json={"workspace_id": "w1", "run_tests": False}
    ).json()

    assert body["ok"] is True
    assert seen["run_tests"] is False


def test_an_unreadable_model_is_a_400_with_the_reason(client, monkeypatch):
    async def refuse(workspace_id, path, *, user):
        raise routes.stl.StlError("the header declares 900 triangles but the file is 120 bytes")

    monkeypatch.setattr(routes.stl, "inspect", refuse)

    resp = client.post("/api/studio/stl/inspect", json={"workspace_id": "w1", "path": "m.stl"})

    assert resp.status_code == 400
    assert "declares 900 triangles" in resp.json()["detail"]


def test_a_rotate_that_is_not_three_numbers_is_a_400(client, monkeypatch):
    async def must_not_run(*args, **kwargs):  # pragma: no cover - the guard runs first
        raise AssertionError("the route validated the vector only after calling the module")

    monkeypatch.setattr(routes.stl, "transform", must_not_run)

    resp = client.post(
        "/api/studio/stl/transform",
        json={"workspace_id": "w1", "path": "m.stl", "scale": 2.0, "rotate": [90, 0]},
    )

    assert resp.status_code == 400
    assert "three numbers" in resp.json()["detail"]


def test_a_model_transform_returns_both_measurements(client, monkeypatch):
    async def fake_transform(workspace_id, path, **kwargs):
        assert kwargs["rotate"] == (90.0, 0.0, 0.0)
        return {
            "source": path,
            "path": "m_edited.stl",
            "format": "binary",
            "applied": {"rotate_deg": [90.0, 0.0, 0.0]},
            "before": {"longest": 10.0},
            "after": {"longest": 10.0},
            "warnings": [],
        }

    monkeypatch.setattr(routes.stl, "transform", fake_transform)

    body = client.post(
        "/api/studio/stl/transform",
        json={"workspace_id": "w1", "path": "m.stl", "rotate": [90, 0, 0]},
    ).json()

    assert body["path"] == "m_edited.stl"
    assert body["before"]["longest"] == body["after"]["longest"] == 10.0


def test_the_profile_reports_the_stack_and_every_stack_present(client, monkeypatch):
    async def fake_list(workspace_id, path=".", **kwargs):
        return [{"name": "package.json"}, {"name": "vite.config.ts"}, {"name": "go.mod"}]

    monkeypatch.setattr(routes.ws, "list_files", fake_list)

    body = client.get("/api/studio/profile", params={"workspace_id": "w1"}).json()

    assert body["stack"] == "node-vite"
    assert body["all_stacks"] == ["node-vite", "go"]
    assert body["steps"]["test"]["command"] == "npm test"


def test_a_missing_workspace_is_a_404(client, monkeypatch):
    async def missing(workspace_id, path=".", **kwargs):
        raise ws.WorkspaceError("Workspace not found")

    monkeypatch.setattr(routes.ws, "list_files", missing)

    resp = client.get("/api/studio/profile", params={"workspace_id": "nope"})

    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"]


def test_running_a_step_returns_the_result(client, monkeypatch):
    async def fake_run(workspace_id, action, *, user, timeout):
        return runtime.RunResult(
            action=action, stack="python", command="python -m pytest -q",
            ok=True, returncode=0, duration_s=1.5, stdout="4 passed",
        )

    monkeypatch.setattr(routes.runtime, "run_action", fake_run)

    resp = client.post("/api/studio/run", json={"workspace_id": "w1", "action": "test"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True and body["stdout"] == "4 passed"


def test_asking_for_a_step_the_stack_lacks_is_a_400(client, monkeypatch):
    async def refuse(workspace_id, action, *, user, timeout):
        raise runtime.StudioError("Static site has no 'test' step. Available: serve")

    monkeypatch.setattr(routes.runtime, "run_action", refuse)

    resp = client.post("/api/studio/run", json={"workspace_id": "w1", "action": "test"})

    assert resp.status_code == 400
    assert "Available: serve" in resp.json()["detail"]


def test_a_preview_returns_its_url(client, monkeypatch):
    async def fake_preview(workspace_id, *, user, port=None):
        return {"ok": True, "url": "http://host:14173", "container_port": 5173, "host_port": 14173}

    monkeypatch.setattr(routes.runtime, "preview", fake_preview)

    body = client.post("/api/studio/preview", json={"workspace_id": "w1"}).json()

    assert body["url"] == "http://host:14173"


def test_stopping_a_preview_reports_what_closed(client, monkeypatch):
    async def fake_stop(workspace_id, *, user):
        return {"stopped_process": True, "closed_ports": [14173]}

    monkeypatch.setattr(routes.runtime, "stop", fake_stop)

    body = client.post("/api/studio/preview/stop", json={"workspace_id": "w1"}).json()

    assert body["closed_ports"] == [14173]



def test_an_image_request_reaches_the_media_module_with_the_caller(client, monkeypatch):
    seen: dict = {}

    async def fake_generate(workspace_id, prompt, **kwargs):
        seen.update({"workspace_id": workspace_id, "prompt": prompt, **kwargs})
        return media.Asset(kind="image", path="assets/hero.png", prompt=prompt)

    monkeypatch.setattr(routes.media, "generate_image", fake_generate)

    body = client.post(
        "/api/studio/assets/image", json={"workspace_id": "w1", "prompt": "a hero", "name": "hero"}
    ).json()

    assert seen["workspace_id"] == "w1" and seen["name"] == "hero"
    assert body["path"] == "assets/hero.png"
    assert seen["user"], "the caller's identity is what the workspace capability check sees"


def test_a_generation_backend_failure_is_a_502_with_its_words(client, monkeypatch):
    async def refuse(*args, **kwargs):
        raise media.MediaError("CUDA out of memory")

    monkeypatch.setattr(routes.media, "generate_image", refuse)

    resp = client.post("/api/studio/assets/image", json={"workspace_id": "w1", "prompt": "x"})

    assert resp.status_code == 502
    assert "CUDA out of memory" in resp.json()["detail"]


def test_a_deck_is_built_and_reported(client, monkeypatch):
    async def fake_build(workspace_id, markdown, *, title, footer, user):
        return works.Built(kind="deck", path="decks/my-deck/index.html", title=title, slides=2)

    monkeypatch.setattr(routes.works, "build_deck", fake_build)

    body = client.post(
        "/api/studio/deck", json={"workspace_id": "w1", "markdown": "# a", "title": "My Deck"}
    ).json()

    assert body["path"] == "decks/my-deck/index.html"
    assert body["slides"] == 2


def test_a_game_is_scaffolded_with_its_sprites(client, monkeypatch):
    seen: dict = {}

    async def fake_scaffold(workspace_id, title, *, sprites, user):
        seen.update({"title": title, "sprites": sprites})
        return works.Built(kind="game", path=f"games/{profiles.slug(title)}/index.html", title=title)

    monkeypatch.setattr(routes.works, "scaffold_game", fake_scaffold)

    body = client.post(
        "/api/studio/game",
        json={"workspace_id": "w1", "title": "Star Catcher", "sprites": {"player": "assets/hero.png"}},
    ).json()

    assert seen["sprites"] == {"player": "assets/hero.png"}
    assert body["path"] == "games/star-catcher/index.html"


def test_the_asset_manifest_is_readable(client, monkeypatch):
    async def fake_manifest(workspace_id, *, user="default"):
        return {"assets": [{"path": "assets/hero.png", "prompt": "a hero"}], "updated_at": "now"}

    monkeypatch.setattr(routes.media, "manifest", fake_manifest)

    body = client.get("/api/studio/assets", params={"workspace_id": "w1"}).json()

    assert body["assets"][0]["prompt"] == "a hero"

"""The Workspace Studio end to end, through the real routes and the real services.

Everything between the HTTP request and the operating system is the production
code: the gateway's ``/api/studio/*`` routes and their shared actions, the
gateway's Workspace API client, the Workspace Runtime's FastAPI app (its real
file, port and path-confinement code on a temporary workspace root), and the
execution service's real ``workspace_shell`` handler, which runs each command
through ``workspace_sandbox.run_workspace_cmd``. A workspace outside the sandbox
mount root runs on the host, so the tests run real ``pytest``, a real
``http.server`` and real file I/O.

Three things are replaced, each at a boundary this machine does not have:

* the network between services: an in-process transport hands each request to
  the service app it was addressed to (``_Transport``);
* Docker's port forward (``expose/unexpose/list_workspace_ports``): a preview's
  host process already listens on the host, so the "forward" is its own port;
* the image model's HTTP API, which answers with a fixed PNG.
"""
from __future__ import annotations

import base64
import contextlib
import json
import os
import socket
import struct
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, StaticPool, create_engine

import services.gateway.config as gw_config
import services.workspace_runtime.database as runtime_db
import services.workspace_runtime.main as runtime_main
import services.workspace_sandbox as sandbox
from services.execution.handlers import workspace as exec_workspace
from services.execution.schemas import WorkspaceShellRequest
from services.gateway import main as gateway_main
from services.gateway.studio import actions, media, stl, tools

OWNER = "testuser"  # the user the gateway's test client signs in as
SOMEONE_ELSE = "another-member"

PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32).decode("ascii")

TETRA = [
    ((0.0, 0.0, 0.0), (0.0, 10.0, 0.0), (10.0, 0.0, 0.0)),
    ((0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (0.0, 0.0, 10.0)),
    ((10.0, 0.0, 0.0), (0.0, 10.0, 0.0), (0.0, 0.0, 10.0)),
    ((0.0, 10.0, 0.0), (0.0, 0.0, 0.0), (0.0, 0.0, 10.0)),
]


def _binary_stl(triangles) -> bytes:
    out = bytearray(b"part".ljust(80, b"\x00"))
    out += struct.pack("<I", len(triangles))
    for a, b, c in triangles:
        out += struct.pack("<12fH", 0.0, 0.0, 0.0, *a, *b, *c, 0)
    return bytes(out)


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


# --- the in-process network -------------------------------------------------

class _Body:
    def __init__(self, data: bytes) -> None:
        self._data = data

    async def iter_chunked(self, size: int):
        for start in range(0, len(self._data), size):
            yield self._data[start:start + size]


class _Response:
    """The slice of ``aiohttp.ClientResponse`` the gateway's clients use."""

    def __init__(self, status: int, data: bytes) -> None:
        self.status = status
        self._data = data
        self.content_length = len(data)
        self.content = _Body(data)

    async def json(self, content_type: str | None = None) -> Any:
        return json.loads(self._data or b"null")

    async def text(self) -> str:
        return self._data.decode("utf-8", errors="replace")

    async def read(self) -> bytes:
        return self._data


class _Transport:
    """Hands each request to the service app the URL names, like the network would."""

    def __init__(self, runtime_client: TestClient) -> None:
        self.runtime = runtime_client
        self.shell_commands: list[tuple[str, str]] = []

    async def dispatch(self, method: str, url: str, payload: Any, headers: dict[str, str]) -> _Response:
        if url.startswith(gw_config.WORKSPACE_RUNTIME_SVC):
            path = url[len(gw_config.WORKSPACE_RUNTIME_SVC):]
            resp = self.runtime.request(method, path, json=payload, headers=headers)
            return _Response(resp.status_code, resp.content)
        if url == f"{gw_config.EXECUTION_SVC}/execute/workspace_shell":
            # The execution route is a one-line adapter over this handler.
            request = WorkspaceShellRequest(**payload)
            self.shell_commands.append((request.user_context.user, request.command or ""))
            result = await exec_workspace.handle_workspace_shell(request)
            return _Response(200, result.model_dump_json().encode())
        if url == "http://image-model.test/v1/images/generations":
            assert "X-Internal-Secret" not in headers, "the image host must never see the internal secret"
            return _Response(200, json.dumps({"data": [{"b64_json": PNG}], "model": "test-sd"}).encode())
        raise AssertionError(f"no service answers {method} {url} in this test")

    # aiohttp.ClientSession's surface, as the gateway uses it
    def request(self, method: str, url: str, *, json: Any = None, headers=None, timeout=None):
        return self._call(method, url, json, headers or {})

    def post(self, url: str, *, json: Any = None, headers=None, timeout=None):
        return self._call("POST", url, json, headers or {})

    @asynccontextmanager
    async def _call(self, method: str, url: str, payload: Any, headers: dict[str, str]):
        yield await self.dispatch(method.upper(), url, payload, headers)


@pytest.fixture
def studio(tmp_path: Path, monkeypatch, client):
    """Two users' workspaces on a real Workspace Runtime, reachable from the gateway."""
    root = (tmp_path / "workspaces").resolve()
    root.mkdir()
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(runtime_main, "engine", engine)
    monkeypatch.setattr(runtime_db, "engine", engine)
    monkeypatch.setattr(runtime_main, "WORKSPACE_ROOT", root)
    monkeypatch.setattr(runtime_main, "get_workspace_root", lambda: root)
    runtime_client = TestClient(runtime_main.app)
    transport = _Transport(runtime_client)

    @asynccontextmanager
    async def _shared():
        yield transport

    monkeypatch.setattr(gateway_main, "shared_http_client", _shared)

    async def _resolve(workspace_id, user_context=None):
        # The execution service's HTTP call to the runtime, over the same transport.
        ctx = user_context.model_dump() if hasattr(user_context, "model_dump") else dict(user_context or {})
        resp = runtime_client.post(
            "/workspace/resolve",
            json={"workspace_id": workspace_id, "user_context": ctx},
            headers={"X-Internal-Secret": runtime_main.INTERNAL_SECRET},
        )
        if resp.status_code != 200:
            from fastapi import HTTPException

            raise HTTPException(status_code=resp.status_code, detail=resp.json().get("detail"))
        workspace = resp.json()["workspace"]
        return workspace["resolved_path"], workspace

    monkeypatch.setattr(exec_workspace, "_resolve_workspace_info", _resolve)

    # Docker's port forward. A host-exec preview already listens on the host.
    forwards: dict[tuple[str, int], int] = {}

    def _expose(workspace_id, container_port, host_port=None):
        forwards[(workspace_id, container_port)] = container_port
        return {"status": "active", "workspace_id": workspace_id, "container_port": container_port,
                "host_port": container_port, "url": f"http://127.0.0.1:{container_port}"}

    def _unexpose(workspace_id, host_port):
        return forwards.pop((workspace_id, host_port), None) is not None

    def _list(workspace_id):
        return [{"workspace_id": ws_id, "container_port": port, "host_port": port,
                 "url": f"http://127.0.0.1:{port}"}
                for (ws_id, port) in forwards if ws_id == workspace_id]

    monkeypatch.setattr(sandbox, "expose_workspace_port", _expose)
    monkeypatch.setattr(sandbox, "unexpose_workspace_port", _unexpose)
    monkeypatch.setattr(sandbox, "list_workspace_ports", _list)

    async def _image_host():
        return "http://image-model.test"

    import services.gateway.agent_loop as agent_loop

    monkeypatch.setattr(agent_loop, "sd_proxy_base_url", _image_host)
    # The sandboxed commands find this interpreter (and its pytest) first.
    monkeypatch.setenv("PATH", f"{Path(sys.executable).parent}{os.pathsep}{os.environ.get('PATH', '')}")
    # The host stands in for the container, which has its own writable HOME
    # (the sandbox user's); give the host process one the same way.
    home = tmp_path / "sandbox-home"
    home.mkdir()
    real_host_exec = sandbox._host_exec

    async def _host_exec_with_home(cmd, *, cwd, env, shell, timeout):
        return await real_host_exec(cmd, cwd=cwd, env={**(env or {}), "HOME": str(home)}, shell=shell, timeout=timeout)

    monkeypatch.setattr(sandbox, "_host_exec", _host_exec_with_home)

    secret = {"X-Internal-Secret": runtime_main.INTERNAL_SECRET}
    for ws_id, owner in (("app", OWNER), ("site", OWNER), ("private", SOMEONE_ELSE)):
        resp = runtime_client.post(
            "/workspaces",
            json={"id": ws_id, "display_name": ws_id, "local_path": f"users/{owner}/{ws_id}",
                  "scope": "user", "owner_user": owner},
            headers=secret,
        )
        assert resp.status_code == 200, resp.text
        (root / "users" / owner / ws_id).mkdir(parents=True, exist_ok=True)

    app_dir = root / "users" / OWNER / "app"
    (app_dir / "pyproject.toml").write_text('[project]\nname = "demo"\nversion = "0.1.0"\n')
    (app_dir / "demo.py").write_text("def add(a, b):\n    return a + b\n")
    (app_dir / "test_demo.py").write_text("from demo import add\n\n\ndef test_add():\n    assert add(2, 2) == 4\n")
    (app_dir / "parts").mkdir()
    (app_dir / "parts" / "bracket.stl").write_bytes(_binary_stl(TETRA))
    site_dir = root / "users" / OWNER / "site"
    (site_dir / "index.html").write_text("<!doctype html><title>site</title><p>hello</p>")
    private_dir = root / "users" / SOMEONE_ELSE / "private"
    (private_dir / "secret.stl").write_bytes(_binary_stl(TETRA))

    state = {"root": root, "app": app_dir, "site": site_dir, "private": private_dir,
             "client": client, "transport": transport, "runtime": runtime_client}
    yield state
    # Never leave a preview server behind, whatever the test did.
    pid_file = site_dir / ".studio" / "preview.pid"
    if pid_file.exists():
        with contextlib.suppress(OSError, ValueError):  # already gone is fine here
            os.kill(int(pid_file.read_text().strip()), 15)


# --- the journey ----------------------------------------------------------------

def test_a_python_workspace_is_profiled_built_tested_and_verified(studio):
    client = studio["client"]

    profile = client.get("/api/studio/profile", params={"workspace_id": "app"})
    assert profile.status_code == 200, profile.text
    assert profile.json()["stack"] == "python"
    assert profile.json()["steps"]["test"]["command"] == "python -m pytest -q"

    build = client.post("/api/studio/run", json={"workspace_id": "app", "action": "build"})
    assert build.status_code == 200, build.text
    assert build.json()["ok"] is True and build.json()["returncode"] == 0

    tests = client.post("/api/studio/run", json={"workspace_id": "app", "action": "test"})
    assert tests.status_code == 200, tests.text
    body = tests.json()
    assert body["ok"] is True, body
    assert body["extra"]["tests_ran"] == 1
    assert "1 passed" in body["stdout"]

    verdict = client.post("/api/studio/verify", json={"workspace_id": "app"}).json()
    assert verdict["ok"] is True, verdict
    assert verdict["program"]["ok"] is True and "studio-ok from python" in verdict["program"]["output"]
    assert not (studio["app"] / "_studio_probe.py").exists(), "the probe cleans up after itself"
    # every command ran as the signed-in user, never as "default"
    assert {user for user, _cmd in studio["transport"].shell_commands} == {OWNER}


def test_a_failing_test_fails_and_a_suite_with_no_tests_is_not_a_pass(studio):
    client = studio["client"]
    (studio["app"] / "test_demo.py").write_text("def test_add():\n    assert 1 == 2\n")
    failing = client.post("/api/studio/run", json={"workspace_id": "app", "action": "test"}).json()
    assert failing["ok"] is False and failing["returncode"] == 1

    (studio["app"] / "test_demo.py").unlink()
    empty = client.post("/api/studio/run", json={"workspace_id": "app", "action": "test"}).json()
    assert empty["ok"] is False
    assert empty["extra"]["tests_ran"] == 0


def test_a_static_site_previews_and_a_second_preview_replaces_the_first(studio):
    client = studio["client"]
    first_port, second_port = _free_port(), _free_port()

    first = client.post("/api/studio/preview", json={"workspace_id": "site", "port": first_port})
    assert first.status_code == 200, first.text
    assert first.json()["ok"] is True, first.json()
    assert first.json()["command"] == f"python3 -m http.server {first_port} --bind 0.0.0.0"

    second = client.post("/api/studio/preview", json={"workspace_id": "site", "port": second_port}).json()
    assert second["ok"] is True, second
    assert second["replaced"]["process"] == "stopped"
    assert second["replaced"]["closed_ports"] == [first_port]
    with socket.socket() as probe:
        assert probe.connect_ex(("127.0.0.1", first_port)) != 0, "the first server is gone"

    stopped = client.post("/api/studio/preview/stop", json={"workspace_id": "site"}).json()
    assert stopped == {"stopped_process": True, "process": "stopped", "closed_ports": [second_port]}
    again = client.post("/api/studio/preview/stop", json={"workspace_id": "site"}).json()
    assert again == {"stopped_process": False, "process": "none", "closed_ports": []}


def test_a_model_is_inspected_and_edited_into_new_files_never_over_the_source(studio):
    client = studio["client"]
    source = (studio["app"] / "parts" / "bracket.stl").read_bytes()

    described = client.post("/api/studio/stl/inspect", json={"workspace_id": "app", "path": "parts/bracket.stl"})
    assert described.status_code == 200, described.text
    assert described.json()["triangles"] == 4 and described.json()["watertight"] is True

    first = client.post(
        "/api/studio/stl/transform",
        json={"workspace_id": "app", "path": "parts/bracket.stl", "fit": 120, "center": True},
    )
    assert first.status_code == 200, first.text
    assert first.json()["path"] == "parts/bracket_edited.stl"
    assert first.json()["after"]["longest"] == pytest.approx(120.0)
    second = client.post(
        "/api/studio/stl/transform",
        json={"workspace_id": "app", "path": "parts/bracket.stl", "scale": [1, 2, 1]},
    ).json()
    assert second["path"] == "parts/bracket_edited_2.stl", "a second edit must not overwrite the first"
    assert (studio["app"] / "parts" / "bracket.stl").read_bytes() == source

    onto_source = client.post(
        "/api/studio/stl/transform",
        json={"workspace_id": "app", "path": "parts/bracket.stl", "scale": 2, "out_path": "parts/bracket.stl"},
    )
    assert onto_source.status_code == 400
    exists = client.post(
        "/api/studio/stl/transform",
        json={"workspace_id": "app", "path": "parts/bracket.stl", "scale": 2,
              "out_path": "parts/bracket_edited.stl"},
    )
    assert exists.status_code == 409
    replaced = client.post(
        "/api/studio/stl/transform",
        json={"workspace_id": "app", "path": "parts/bracket.stl", "scale": 2,
              "out_path": "parts/bracket_edited.stl", "replace": True},
    )
    assert replaced.status_code == 200, replaced.text


@pytest.mark.parametrize(
    "body, words",
    [
        ({"scale": [1, 2]}, "three numbers"),
        ({"rotate": [90, 0]}, "three numbers"),
        ({"scale": float("nan")}, "finite"),
        ({"fit": float("inf")}, "finite"),
        ({"scale": 2, "out_path": "parts/out.obj"}, ".stl"),
    ],
)
def test_a_bad_transform_is_a_400_that_says_why(studio, body, words):
    resp = studio["client"].post(
        "/api/studio/stl/transform",
        content=json.dumps({"workspace_id": "app", "path": "parts/bracket.stl", **body}),
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 400, resp.text
    assert words in resp.json()["detail"]


def test_another_users_files_cannot_be_reached_by_an_absolute_path(studio):
    client = studio["client"]
    secret = str(studio["private"] / "secret.stl")

    read = client.post("/api/studio/stl/inspect", json={"workspace_id": "app", "path": secret})
    assert read.status_code == 400
    write = client.post(
        "/api/studio/stl/transform",
        json={"workspace_id": "app", "path": "parts/bracket.stl", "scale": 2, "out_path": secret},
    )
    assert write.status_code == 400
    # and the runtime itself refuses it, whoever asks the studio to
    raw = studio["runtime"].post(
        "/files/raw",
        json={"workspace_id": "app", "relative_path": secret, "user_context": {"user": OWNER}},
        headers={"X-Internal-Secret": runtime_main.INTERNAL_SECRET},
    )
    assert raw.status_code == 403
    assert (studio["private"] / "secret.stl").read_bytes() == _binary_stl(TETRA)
    # nor is another user's workspace reachable by its id
    theirs = client.post("/api/studio/stl/inspect", json={"workspace_id": "private", "path": "secret.stl"})
    assert theirs.status_code == 404


def test_a_deck_and_a_game_are_written_and_never_silently_overwritten(studio):
    client = studio["client"]
    deck = client.post("/api/studio/deck", json={"workspace_id": "app", "markdown": "# Hello\n- one\n---\n# Two"})
    assert deck.status_code == 200, deck.text
    assert (studio["app"] / "decks" / "hello" / "index.html").exists()
    again = client.post("/api/studio/deck", json={"workspace_id": "app", "markdown": "# Hello\n- changed"})
    assert again.status_code == 409 and "replace" in again.json()["detail"]
    replaced = client.post(
        "/api/studio/deck", json={"workspace_id": "app", "markdown": "# Hello\n- changed", "replace": True}
    )
    assert replaced.status_code == 200
    assert "changed" in (studio["app"] / "decks" / "hello" / "source.md").read_text()

    game = client.post(
        "/api/studio/game",
        json={"workspace_id": "app", "title": "Star <b>Catcher</b>",
              "sprites": {"player": "assets/x.png</script><script>alert(1)</script>"}},
    )
    assert game.status_code == 200, game.text
    page = (studio["root"] / "users" / OWNER / "app" / game.json()["path"]).read_text()
    assert "<b>Catcher</b>" not in page and "&lt;b&gt;Catcher&lt;/b&gt;" in page
    assert "</script><script>alert(1)" not in page
    assert client.post("/api/studio/game", json={"workspace_id": "app", "title": "Star <b>Catcher</b>"}).status_code == 409


def test_generated_art_lands_in_the_workspace_and_the_manifest_keeps_every_entry(studio):
    client = studio["client"]
    for name in ("hero", "star"):
        made = client.post("/api/studio/assets/image", json={"workspace_id": "app", "prompt": f"a {name}", "name": name})
        assert made.status_code == 200, made.text
    listed = client.get("/api/studio/assets", params={"workspace_id": "app"}).json()
    assert [item["path"] for item in listed["assets"]] == ["assets/hero.png", "assets/star.png"]

    (studio["app"] / "assets" / "manifest.json").write_text("{not json")
    broken = client.get("/api/studio/assets", params={"workspace_id": "app"})
    assert broken.status_code == 502 and "not valid JSON" in broken.json()["detail"]
    refused = client.post("/api/studio/assets/image", json={"workspace_id": "app", "prompt": "a moon", "name": "moon"})
    assert refused.status_code == 502
    assert (studio["app"] / "assets" / "manifest.json").read_text() == "{not json", "a corrupt manifest is not overwritten"


async def test_the_models_tools_run_the_same_actions_as_the_routes(studio):
    result = await tools.call("StudioRunRequest", {"action": "test"}, user=OWNER, workspace_id="app")
    assert result["ok"] is True and result["extra"]["tests_ran"] == 1
    inspected = await tools.call("stl_inspect_request", {"path": "parts/bracket.stl"}, user=OWNER, workspace_id="app")
    assert inspected["triangles"] == 4
    with pytest.raises(actions.StudioActionError) as refused:
        await tools.call("StlInspectRequest", {"path": "parts/bracket.stl"}, user=SOMEONE_ELSE, workspace_id="app")
    assert refused.value.status == 404, "a tool runs with the turn's user's own access"
    with pytest.raises(actions.StudioActionError) as bad:
        await tools.call("StlTransformRequest", {"path": "parts/bracket.stl", "rotate": [1]}, user=OWNER, workspace_id="app")
    assert bad.value.status == 400


async def test_a_studio_module_has_no_default_user():
    """Every studio entry point needs the caller's user: a forgotten one must fail loudly."""
    import inspect

    from services.gateway.studio import runtime, works

    for module in (runtime, stl, media, works):
        for name, func in inspect.getmembers(module, inspect.iscoroutinefunction):
            if func.__module__ != module.__name__:
                continue
            param = inspect.signature(func).parameters.get("user")
            if param is not None:
                assert param.default is inspect.Parameter.empty, f"{module.__name__}.{name} defaults user"


async def test_the_chat_executor_runs_a_studio_tool_as_the_turns_user(studio, monkeypatch):
    from services.gateway import orchestrator
    from services.gateway.schemas import ResolvedCredentials

    async def no_settings():
        return {}

    monkeypatch.setattr(orchestrator, "get_all_settings", no_settings)
    (studio["app"] / "test_demo.py").write_text("def test_add():\n    assert 1 == 2\n")

    answer = await orchestrator._execute_single_tool(
        "StudioRunRequest", {"payload": {"action": "test"}}, "run the tests",
        ResolvedCredentials(user=OWNER), workspace_id="app",
    )

    assert answer.startswith('StudioRunRequest result: {"ok": false'), answer
    assert '"returncode": 1' in answer, "a failing run reaches the model as a failure"
    refused = await orchestrator._execute_single_tool(
        "StudioRunRequest", {"payload": {"action": "test"}}, "run the tests",
        ResolvedCredentials(user=SOMEONE_ELSE), workspace_id="app",
    )
    assert "HTTP 404" in refused

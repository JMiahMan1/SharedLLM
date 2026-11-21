"""Workspace fixes found while investigating production (Nov 2026).

- A failed recovery or bootstrap clone left the workspace empty with the
  user's files parked in a ``-backup-`` directory (seen on the server).
- Bootstrap moved those files aside before refusing a call with no repo_url.
- A command for a sandboxed workspace silently ran in the service container
  when Docker failed, with the service's full environment (every secret).
- A sandbox found by name was reused even when it mounted another path.
- Missions matched workspaces by substring and "created" theirs through a
  route that always refused without a repository.
"""

from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from fastapi import HTTPException

import services.workspace_runtime.main as runtime
import services.workspace_sandbox as sandbox
from services.gateway import agent_loop

# --- Recovery and bootstrap keep the user's files ------------------------------


def _failing_clone(*args, **kwargs):
    return {"args": args, "returncode": 128, "stdout": "", "stderr": "fatal: Authentication failed"}


def test_a_failed_recovery_clone_puts_the_files_back(tmp_path, monkeypatch):
    ws_dir = tmp_path / "proj"
    ws_dir.mkdir()
    (ws_dir / "notes.md").write_text("mine")
    monkeypatch.setattr(runtime, "_run_git_with_optional_askpass", _failing_clone)

    with pytest.raises(HTTPException) as err:
        runtime._ensure_workspace_recovered(
            {"id": "proj", "repo_url": "https://example.invalid/r.git", "resolved_path": str(ws_dir)}
        )

    assert err.value.status_code == 502
    assert "Authentication failed" in err.value.detail
    assert (ws_dir / "notes.md").read_text() == "mine"
    assert not any(p.name.startswith("proj-backup-") for p in tmp_path.iterdir())


def test_a_successful_recovery_clone_still_backs_up_the_old_files(tmp_path, monkeypatch):
    ws_dir = tmp_path / "proj"
    ws_dir.mkdir()
    (ws_dir / "notes.md").write_text("mine")

    def cloned(*args, **kwargs):
        (ws_dir / ".git").mkdir()
        return {"returncode": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr(runtime, "_run_git_with_optional_askpass", cloned)
    runtime._ensure_workspace_recovered(
        {"id": "proj", "repo_url": "https://example.invalid/r.git", "resolved_path": str(ws_dir)}
    )
    backups = [p for p in tmp_path.iterdir() if p.name.startswith("proj-backup-")]
    assert len(backups) == 1 and (backups[0] / "notes.md").read_text() == "mine"


def _bootstrap_workspace(tmp_path: Path, repo_url: str | None):
    ws_dir = tmp_path / "proj"
    ws_dir.mkdir()
    (ws_dir / "notes.md").write_text("mine")
    return ws_dir, {
        "id": "proj",
        "local_path": "proj",
        "resolved_path": str(ws_dir),
        "repo_url": repo_url,
        "capabilities": ["git_write"],
    }


def test_bootstrap_without_a_repo_leaves_the_files_alone(tmp_path, monkeypatch):
    ws_dir, workspace = _bootstrap_workspace(tmp_path, None)
    monkeypatch.setattr(runtime, "_resolve_workspace_for_bootstrap", lambda req: dict(workspace))
    monkeypatch.setattr(runtime, "_require_internal_secret", lambda secret: None)
    req = runtime.WorkspaceBootstrapRequest(workspace_id="proj")

    with pytest.raises(HTTPException) as err:
        runtime.bootstrap_workspace(req, x_internal_secret="x")

    assert err.value.status_code == 400
    assert (ws_dir / "notes.md").read_text() == "mine"
    assert not any(p.name.startswith("proj-backup-") for p in tmp_path.iterdir())


def test_a_failed_bootstrap_clone_puts_the_files_back(tmp_path, monkeypatch):
    ws_dir, workspace = _bootstrap_workspace(tmp_path, "https://example.invalid/r.git")
    monkeypatch.setattr(runtime, "_resolve_workspace_for_bootstrap", lambda req: dict(workspace))
    monkeypatch.setattr(runtime, "_require_internal_secret", lambda secret: None)
    monkeypatch.setattr(runtime, "_run_git_with_optional_askpass", _failing_clone)
    req = runtime.WorkspaceBootstrapRequest(workspace_id="proj")

    with pytest.raises(HTTPException):
        runtime.bootstrap_workspace(req, x_internal_secret="x")

    assert (ws_dir / "notes.md").read_text() == "mine"
    assert not any(p.name.startswith("proj-backup-") for p in tmp_path.iterdir())


# --- The sandbox never leaks the service environment ----------------------------


async def test_a_host_exec_does_not_receive_the_service_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("INTERNAL_SECRET", "service-only-value")
    monkeypatch.setenv("FERNET_KEY", "service-only-key")
    result = await sandbox._host_exec(
        ["/usr/bin/env"], cwd=str(tmp_path), env={"MINE": "1"}, shell=False, timeout=10
    )
    assert result["returncode"] == 0
    assert "MINE=1" in result["stdout"]
    assert "service-only" not in result["stdout"]


async def test_a_sandboxed_workspace_never_falls_back_to_the_host(monkeypatch):
    monkeypatch.setattr(sandbox, "_under_mount_root", lambda path: True)

    def docker_down(*args, **kwargs):
        raise RuntimeError("docker-unavailable")

    ran_on_host = []

    async def host(*args, **kwargs):
        ran_on_host.append(args)
        return {"returncode": 0}

    monkeypatch.setattr(sandbox, "_exec_blocking", docker_down)
    monkeypatch.setattr(sandbox, "_host_exec", host)
    result = await sandbox.run_workspace_cmd("proj", "/workspaces/proj", ["ls"])
    assert result["returncode"] == 125
    assert "did not run" in result["stderr"]
    assert ran_on_host == []


class _Container:
    status = "running"

    def __init__(self, destination):
        self.attrs = {"Mounts": [{"Destination": destination}]}
        self.removed = False
        self.image = type("I", (), {"id": "img"})()

    def remove(self, force=False):
        self.removed = True


class _Client:
    def __init__(self, container):
        self.containers = self
        self.images = self
        self._c = container

    def get(self, name):
        return self._c if name.startswith("wsbox") else type("I", (), {"id": "img"})()


def test_a_sandbox_mounting_another_path_is_recreated():
    c = _Container("/workspaces/old-path")
    assert sandbox._get_or_recreate(_Client(c), "wsbox-proj", "img", "/workspaces/new-path") is None
    assert c.removed


def test_a_sandbox_mounting_the_right_path_is_reused():
    c = _Container("/workspaces/proj")
    assert sandbox._get_or_recreate(_Client(c), "wsbox-proj", "img", "/workspaces/proj") is c
    assert not c.removed


# --- Missions find and create their workspace -----------------------------------


@pytest.mark.parametrize(
    ("query", "wid", "expected"),
    [
        ("Summarise this week's sermons", "sermon", False),
        ("Update the notes in the sermon workspace", "sermon", True),
        ("Fix the game in Raven-Space-Shooter", "raven-space-shooter", True),
        ("Use raven-space-shooter-v2", "raven-space-shooter", False),
    ],
)
def test_workspaces_are_matched_by_whole_id(query, wid, expected):
    assert agent_loop._mentions_workspace(query, wid) is expected


class _Resp:
    def __init__(self, status, body):
        self.status = status
        self._body = body

    async def json(self):
        return self._body


class _Http:
    def __init__(self):
        self.posts = []

    async def post(self, url, json=None, headers=None, timeout=None):
        self.posts.append((url, json))
        if url.endswith("/workspace/resolve"):
            return _Resp(404, {})
        if url.endswith("/workspaces"):
            return _Resp(200, {"workspace": {"id": json["id"], "owner_user": json["owner_user"]}})
        return _Resp(400, {"detail": "repo_url is required"})

    async def get(self, url, params=None, headers=None, timeout=None):
        return _Resp(200, {"workspaces": []})


def _patch_http(monkeypatch):
    http = _Http()

    @asynccontextmanager
    async def client():
        yield http

    monkeypatch.setattr(agent_loop, "shared_http_client", client)
    return http


async def test_an_assigned_workspace_that_does_not_exist_is_created_locally(monkeypatch):
    http = _patch_http(monkeypatch)
    ws = await agent_loop.resolve_mission_workspace("alice", "raven-new-game")
    assert ws == {"id": "raven-new-game", "owner_user": "alice"}
    assert not any(url.endswith("/workspaces/bootstrap") for url, _ in http.posts)


async def test_a_raven_word_in_the_query_creates_nothing(monkeypatch):
    http = _patch_http(monkeypatch)
    monkeypatch.setattr(agent_loop, "is_system_maintenance_task", lambda q: False)
    assert await agent_loop.resolve_mission_workspace("alice", None, "Write it in a raven-style voice") is None
    assert not any(url.endswith("/workspaces") for url, _ in http.posts)


async def test_a_workspace_named_in_the_query_is_created(monkeypatch):
    _patch_http(monkeypatch)
    monkeypatch.setattr(agent_loop, "is_system_maintenance_task", lambda q: False)
    ws = await agent_loop.resolve_mission_workspace("alice", None, "Build it in workspace raven-pong")
    assert ws and ws["id"] == "raven-pong"


"""Unit tests for git push verification (lessons from Aider/Hermes).

These run fast and need no model or downstream service — they mock the
sandbox git execution so we can prove the push-confirmation logic is correct:
`git push` exiting 0 is no longer blindly trusted; we confirm the ref actually
advanced and no commits remain unpushed.
"""
import os

# git_ops has a deliberate circular import with main.py (main imports the router
# at the bottom). Importing git_ops directly triggers the cycle, so we must
# import main first (the production entrypoint order). main refuses to start
# without INTERNAL_SECRET, hence the env stub.
os.environ.setdefault("INTERNAL_SECRET", "test-secret")
os.environ.setdefault("WORKSPACE_DATABASE_URL", "sqlite:///:memory:")

from unittest.mock import AsyncMock, MagicMock

import services.workspace_runtime.main  # noqa: F401  (resolves circular import order)
from services.workspace_runtime import git_ops


def _result(returncode: int, stdout: str = "") -> dict:
    return {"returncode": returncode, "stdout": stdout, "stderr": ""}


async def test_count_unpushed_zero_when_synced(monkeypatch):
    async def fake_run_git(ws, path, args, **kw):
        return _result(0, "0")

    monkeypatch.setattr(git_ops, "run_git", fake_run_git)
    assert await git_ops._count_unpushed("ws", "/p", "origin", "main") == 0


async def test_count_unpushed_reports_ahead_commits(monkeypatch):
    async def fake_run_git(ws, path, args, **kw):
        return _result(0, "3")

    monkeypatch.setattr(git_ops, "run_git", fake_run_git)
    assert await git_ops._count_unpushed("ws", "/p", "origin", "main") == 3


async def test_count_unpushed_unknown_on_git_error(monkeypatch):
    async def fake_run_git(ws, path, args, **kw):
        return _result(128, "")

    monkeypatch.setattr(git_ops, "run_git", fake_run_git)
    assert await git_ops._count_unpushed("ws", "/p", "origin", "main") == -1


async def test_count_unpushed_unknown_on_bad_output(monkeypatch):
    async def fake_run_git(ws, path, args, **kw):
        return _result(0, "not-a-number")

    monkeypatch.setattr(git_ops, "run_git", fake_run_git)
    assert await git_ops._count_unpushed("ws", "/p", "origin", "main") == -1


async def test_git_push_reports_verified_when_synced(monkeypatch):
    monkeypatch.setattr(git_ops, "_require_internal_secret", lambda *a, **k: None)
    monkeypatch.setattr(
        git_ops,
        "_resolve_workspace",
        MagicMock(
            return_value={
                "id": "ws",
                "resolved_path": "/p",
                "resolved_identity": {},
                "git_remote": "origin",
            }
        ),
    )
    monkeypatch.setattr(git_ops, "_require_workspace_capability", lambda *a, **k: None)
    monkeypatch.setattr(git_ops, "current_branch_name", AsyncMock(return_value="main"))
    monkeypatch.setattr(git_ops, "git_remote_url", AsyncMock(return_value="https://x"))
    monkeypatch.setattr(git_ops, "_is_protected_branch", lambda *a, **k: False)
    monkeypatch.setattr(
        git_ops, "run_git_with_optional_askpass", AsyncMock(return_value=_result(0, "ok"))
    )

    async def fake_run_git(ws, path, args, **kw):
        if any("rev-list" in a for a in args):
            return _result(0, "0")
        return _result(0, "origin/main")

    monkeypatch.setattr(git_ops, "run_git", fake_run_git)

    class Req:
        workspace_id = "ws"
        remote = None
        branch = None
        set_upstream = False

    out = await git_ops.git_push(Req())
    assert out["status"] == "SUCCESS"
    assert out["verified"] is True
    assert out["unpushed_count"] == 0

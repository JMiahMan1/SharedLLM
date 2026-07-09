"""Unit tests for the `gh` workspace tool (allowlist + execution path)."""
import asyncio

import pytest

from services.execution.handlers import gh as gh_module
from services.execution.handlers.gh import _validate, handle_gh
from services.execution.schemas import GhRequest


def test_validate_allowlist():
    assert _validate(["repo", "create", "my-game", "--public"]) == ("repo", "create")
    assert _validate(["pr", "create"]) == ("pr", "create")
    assert _validate(["issue", "list"]) == ("issue", "list")
    # Blocked subcommands
    assert _validate(["auth", "login"]) is None
    assert _validate(["secret", "list"]) is None
    assert _validate(["api", "repos"]) is None
    # Blocked actions
    assert _validate(["repo", "delete", "x"]) is None
    assert _validate(["repo", "archive", "x"]) is None
    assert _validate(["pr", "close", "123"]) is None
    assert _validate(["release", "delete", "x"]) is None
    # Unknown subcommand
    assert _validate(["nuke", "everything"]) is None
    # Empty
    assert _validate([]) is None


@pytest.fixture
def isolated_workspace(tmp_path, monkeypatch):
    async def fake_resolve(workspace_id, user_context):
        return (str(tmp_path), {})

    monkeypatch.setattr(gh_module, "_resolve_workspace_info", fake_resolve)
    return tmp_path


def test_handle_gh_readonly_version(isolated_workspace):
    req = GhRequest(user_context={"user": "tester", "is_admin": False}, args=["version"])
    res = asyncio.run(handle_gh(req))
    assert res["status"] == "SUCCESS", res
    assert "gh version" in res["detail"]["stdout"]


def test_handle_gh_blocks_destructive(isolated_workspace):
    req = GhRequest(user_context={"user": "tester", "is_admin": True}, args=["auth", "login"])
    res = asyncio.run(handle_gh(req))
    assert res["status"] == "FAILURE"
    assert res["detail"]["error"] == "blocked_subcommand"


def test_handle_gh_blocks_repo_delete(isolated_workspace):
    req = GhRequest(user_context={"user": "tester", "is_admin": True}, args=["repo", "delete", "x", "-y"])
    res = asyncio.run(handle_gh(req))
    assert res["status"] == "FAILURE"
    assert res["detail"]["error"] == "blocked_subcommand"

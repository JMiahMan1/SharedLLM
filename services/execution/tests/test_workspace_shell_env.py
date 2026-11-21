"""The workspace shell must hand the sandbox the user's keys, and nothing else.

Two bugs lived in the same six lines: the shell passed the execution service's
whole environment (INTERNAL_SECRET, FERNET_KEY, cloud keys) to arbitrary repo
code, and it forced HOME=/home/sharedllm -- a directory that exists only in the
gateway image, so npm, `pip install --user`, git's config and bash's startup all
failed with EACCES inside the sandbox. The tests below pin both, and that the
user's own GitHub token still arrives.
"""
from __future__ import annotations

import pytest

from services.execution.handlers import workspace as handler
from services.execution.schemas import UserContext, WorkspaceShellRequest

CAPABILITIES = ["read", "write", "git_status", "git_diff", "git_write", "pytest"]


@pytest.fixture
def captured(monkeypatch):
    """Fake workspace resolution and sandbox run; records what was executed."""
    seen: dict = {}

    async def resolve(workspace_id, user_context=None):
        return "/workspaces/demo", {"id": "demo", "capabilities": CAPABILITIES, "env": {}}

    async def run(workspace_id, host_path, cmd, cwd=None, timeout=30.0, shell=False, env=None):
        seen.update({"cmd": cmd, "env": dict(env or {}), "cwd": cwd, "shell": shell})
        return 0, "ok", ""

    monkeypatch.setattr(handler, "_resolve_workspace_info", resolve)
    monkeypatch.setattr(handler, "_sandbox_run", run)
    return seen


def _request(command: str, **context) -> WorkspaceShellRequest:
    return WorkspaceShellRequest(
        workspace_id="demo",
        command=command,
        user_context=UserContext(user="alice", **context),
    )


async def test_the_services_own_secrets_never_reach_the_sandbox(captured, monkeypatch):
    monkeypatch.setenv("INTERNAL_SECRET", "shhh")
    monkeypatch.setenv("FERNET_KEY", "also-secret")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-secret")

    result = await handler.handle_workspace_shell(_request("npm install"))

    assert result.status == "SUCCESS"
    env = captured["env"]
    assert "INTERNAL_SECRET" not in env
    assert "FERNET_KEY" not in env
    assert "OPENROUTER_API_KEY" not in env


async def test_home_is_left_to_the_sandbox(captured):
    """The sandbox picks HOME from the container's own passwd entry."""
    await handler.handle_workspace_shell(_request("ls"))

    assert "HOME" not in captured["env"], (
        "a HOME chosen by the caller is what pointed npm at /home/sharedllm"
    )


async def test_the_users_github_token_still_arrives(captured):
    await handler.handle_workspace_shell(_request("git status", github_token="gh-token-1"))

    env = captured["env"]
    assert env.get("GITHUB_TOKEN") == "gh-token-1"
    assert env.get("GH_PROMPT_DISABLED") == "1"


async def test_a_git_command_gets_the_inline_credential(captured):
    await handler.handle_workspace_shell(_request("git status", github_token="gh-token-1"))

    env = captured["env"]
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["GIT_CONFIG_COUNT"] == "1"
    assert "x-access-token" in env["GIT_CONFIG_VALUE_0"]


async def test_a_non_git_command_does_not_get_the_git_credential(captured):
    await handler.handle_workspace_shell(_request("npm test", github_token="gh-token-1"))

    assert "GIT_CONFIG_VALUE_0" not in captured["env"]


async def test_a_workspace_override_can_clear_an_inherited_secret(captured, monkeypatch):
    async def resolve(workspace_id, user_context=None):
        return "/workspaces/demo", {
            "id": "demo",
            "capabilities": CAPABILITIES,
            "env": {"GITHUB_TOKEN": None},
        }

    monkeypatch.setattr(handler, "_resolve_workspace_info", resolve)

    await handler.handle_workspace_shell(_request("ls", github_token="gh-token-1"))

    assert "GITHUB_TOKEN" not in captured["env"]

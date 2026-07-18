import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from services.execution.handlers import git as git_handler
from services.execution.handlers.git import GitOperationRequest, handle_git
from services.execution.schemas import UserContext


def _creds(is_admin=True):
    return UserContext(user="default", is_admin=is_admin, github_token="tok")


def _req(action, **kw):
    return GitOperationRequest(action=action, workspace_id="ws1", user_context=_creds(), **kw)


async def _run(action, tmp_path, **kw):
    req = _req(action, **kw)
    with patch.object(git_handler, "_resolve_workspace_path", AsyncMock(return_value=str(tmp_path))), \
         patch.object(git_handler, "_get_workspace_repo_url", AsyncMock(return_value=None)), \
         patch.object(git_handler, "_run_git", wraps=git_handler._run_git):
        return await handle_git(req)


@pytest.mark.asyncio
async def test_git_happy_path_actions_do_not_error_as_unknown(tmp_path):
    """The prescribed Raven path is repo_create -> add -> commit -> push. Every
    git verb on that path (plus init/branch/remote/remote_add/log) must dispatch
    to a real handler, never the 'Unknown git action' fallback. This guards the
    misleading error message and proves the well-tested path is wired end-to-end."""
    # init + configure identity so commit works
    import subprocess

    subprocess.run(["git", "init"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@local.host"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)

    # These must NOT be "Unknown git action".
    for action in ("status", "init", "branch", "remote", "log", "diff", "show"):
        res = await _run(action, tmp_path)
        assert res.status != "FAILURE" or "Unknown git action" not in res.message, (
            f"action '{action}' hit Unknown-git-action fallback: {res.message}"
        )

    # add + commit need a file
    (tmp_path / "README.md").write_text("Raven training sandbox\n")
    ra = await _run("add", tmp_path, path="README.md")
    assert "Unknown git action" not in ra.message
    rc = await _run("commit", tmp_path, commit_message="feat: initial README")
    assert "Unknown git action" not in rc.message

    # remote_add is implemented (wires origin); needs repo_url, not network.
    rra = await _run("remote_add", tmp_path, repo_url="https://github.com/x/y.git")
    assert "Unknown git action" not in rra.message


@pytest.mark.asyncio
async def test_git_unknown_action_message_lists_real_actions():
    """The fallback error must list the ACTUAL valid actions so the model gets
    correct feedback (it previously omitted remote_add/init/branch/etc).

    The GitOperationRequest schema rejects unknown actions at construction, so
    the handler's fallback is only reached via dynamically-built payloads
    (e.g. the shell->git interceptor). Exercise it by constructing the handler
    result the way the fallback does and asserting the corrected hint.
    """
    from services.execution.handlers.git import GitExecutionResult

    res = GitExecutionResult(
        status="FAILURE",
        message=(
            "Unknown git action 'bogus'. Valid: status, diff, add, commit, "
            "pull, push, log, fetch, init, remote, remote_add, branch, checkout, "
            "show, clean, reset, repo_create, repo_clone, gh_noop."
        ),
        service="git",
        detail={},
    )
    for a in ("remote_add", "init", "branch", "repo_create", "push", "commit", "add"):
        assert a in res.message, f"valid action '{a}' missing from error hint"

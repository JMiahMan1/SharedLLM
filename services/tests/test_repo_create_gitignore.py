from unittest.mock import AsyncMock, patch

import pytest

from services.execution.handlers import git as git_handler
from services.execution.handlers.git import GitOperationRequest, handle_git
from services.execution.schemas import UserContext


def _make_req(workspace_path: str):
    creds = UserContext(user="default", is_admin=True, github_token="tok")
    return GitOperationRequest(
        action="repo_create",
        repo_name="my-repo",
        workspace_id="ws1",
        user_context=creds,
    ), workspace_path


async def _run_repo_create(tmp_path, preexisting_gitignore=False):
    ws = tmp_path / "ws"
    ws.mkdir()
    if preexisting_gitignore:
        (ws / ".gitignore").write_text("node_modules/\n", encoding="utf-8")

    req, _ = _make_req(str(ws))

    async def fake_run_git(args, cwd=None, env_override=None):
        # git init / remote remove / remote add all "succeed"
        return {"returncode": 0, "stdout": "", "stderr": ""}

    with patch.object(git_handler, "_create_github_repo", AsyncMock(return_value="https://github.com/JMiahMan1/my-repo.git")), \
         patch.object(git_handler, "_resolve_workspace_path", AsyncMock(return_value=str(ws))), \
         patch.object(git_handler, "_run_git", side_effect=fake_run_git), \
         patch.object(git_handler, "_bind_workspace_repo", AsyncMock()):
        res = await handle_git(req)

    return res, ws


@pytest.mark.asyncio
async def test_repo_create_seeds_default_gitignore(tmp_path):
    res, ws = await _run_repo_create(tmp_path)
    assert res.status == "SUCCESS", res.message
    gi = ws / ".gitignore"
    assert gi.exists(), "default .gitignore was not seeded"
    content = gi.read_text(encoding="utf-8")
    assert "raven_memory.md" in content
    assert "__pycache__/" in content
    assert "node_modules/" in content


@pytest.mark.asyncio
async def test_repo_create_preserves_existing_gitignore(tmp_path):
    res, ws = await _run_repo_create(tmp_path, preexisting_gitignore=True)
    assert res.status == "SUCCESS", res.message
    gi = ws / ".gitignore"
    assert gi.read_text(encoding="utf-8") == "node_modules/\n", "existing .gitignore was clobbered"

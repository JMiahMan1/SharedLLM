import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from services.gateway import agent_loop as al


class _FakeResp:
    def __init__(self, status, payload):
        self._status = status
        self._payload = payload
    async def json(self):
        return self._payload
    @property
    def status(self):
        return self._status


def _make_client(sequence):
    """sequence: list of (_FakeResp) returned per POST call."""
    calls = {"n": 0, "patches": []}
    async def _gen():
        while True:
            yield None
    cli = AsyncMock()
    cli.__aenter__.return_value = cli
    cli.__aexit__.return_value = False
    async def _post(url, *, json=None, headers=None, timeout=None):
        # resolve call returns workspace without repo_url -> proceed
        if "workspace/resolve" in url:
            return _FakeResp(200, {"workspace": {"id": "ws1", "repo_url": None}})
        if url.endswith("/workspaces/ws1"):
            calls["patches"].append(json)
            return _FakeResp(200, {})
        return _FakeResp(200, {})
    cli.post = _post
    cli.patch = _post
    return cli, calls


@pytest.mark.asyncio
async def test_autowire_prefers_exec_data_url_over_gh_view():
    """The URL from the repo_create tool result must win — gh repo view is
    never called, so a flaky `gh repo view` can't leave the workspace unbound
    or bind the WRONG (e.g. SharedLLM) repo."""
    cli, calls = _make_client([])
    gh_calls = []
    fake_creds = type("C", (), {"user": "default", "is_admin": True, "api_key": "k",
                                "github_token": "t", "git_token": None})()

    async def fake_shell_out(ws_id, uc, cmd):
        gh_calls.append(cmd)
        return None  # gh repo view fails -> must NOT be needed

    with patch.object(al, "shared_http_client", return_value=cli), \
         patch.object(al, "_shell_out", side_effect=fake_shell_out):
        await al._autowire_created_repo(
            "ws1", fake_creds, "gh repo create my-repo --private",
            {"status": "SUCCESS", "detail": {"repo_url": "https://github.com/JMiahMan1/my-repo.git"}},
        )

    # gh repo view must NOT have been invoked
    assert not any("gh repo view" in c for c in gh_calls), gh_calls
    # workspace patched with the correct (non-SharedLLM) repo url
    assert calls["patches"], "workspace was never patched"
    assert calls["patches"][0]["repo_url"] == "https://github.com/JMiahMan1/my-repo.git"
    assert calls["patches"][0]["git_remote"] == "origin"
    assert calls["patches"][0]["default_branch"] == "main"

"""Sandbox credentials come only from the calling user, never the service.

The service's own GITHUB_TOKEN / NEXTCLOUD_* / HA_TOKEN belong to the owner. They
used to be copied from ``os.environ`` into every sandbox container (at create
time) and every exec, so a user with no GitHub integration pushed with the
owner's token, and a workspace override of ``{"GITHUB_TOKEN": None}`` could not
clear it. These tests drive the real execution handler through ``_sandbox_run``
and the real sandbox module, faking only the Docker client, and fail if a server
token reaches a user's exec.
"""

import asyncio
import os
from unittest.mock import MagicMock, patch

import pytest

import services.workspace_sandbox as sb
from services.execution.handlers import workspace as ws_handler
from services.execution.schemas import WorkspaceShellRequest

_IMAGE_ID = "sha256:test-image"

_SERVER_SECRETS = {
    "GITHUB_TOKEN": "server-owner-github",
    "GH_TOKEN": "server-owner-gh",
    "GIT_TOKEN": "server-owner-git",
    "GITLAB_TOKEN": "server-owner-gitlab",
    "NEXTCLOUD_PASSWORD": "server-owner-nc",
    "NEXTCLOUD_URL": "https://nextcloud.example.invalid",
    "HA_TOKEN": "server-owner-ha",
    "HA_URL": "http://ha.example.invalid:8123",
}


@pytest.fixture
def server_secrets(monkeypatch):
    for k, v in _SERVER_SECRETS.items():
        monkeypatch.setenv(k, v)


def _container(env_list=None):
    c = MagicMock()
    c.status = "running"
    c.image.id = _IMAGE_ID
    c.id = "c-" + os.urandom(4).hex()
    c.attrs = {"Config": {"Env": list(env_list or ["PATH=/usr/bin"])}, "Mounts": []}
    c.exec_run.return_value = MagicMock(exit_code=0, output=(b"ok\n", b""))
    return c


def _client(container):
    client = MagicMock()
    client.containers.get.return_value = container
    client.containers.run.return_value = container
    client.images.get.return_value = MagicMock(id=_IMAGE_ID)
    client.networks.get.return_value = MagicMock()
    return client


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    root = os.path.realpath(str(tmp_path))
    ws_root = os.path.join(root, "users", "someone", "proj")
    os.makedirs(ws_root)
    monkeypatch.setattr(sb, "SANDBOX_MOUNT_ROOT", root)

    async def _resolve(workspace_id, user_context=None):
        return ws_root, {}

    monkeypatch.setattr(ws_handler, "_resolve_workspace_info", _resolve)
    return ws_root


def _exec_env(container) -> dict:
    """The environment of the user's command (the last exec_run that is not the home probe)."""
    calls = [c for c in container.exec_run.call_args_list if c.kwargs.get("user") != "0:0"]
    assert calls, "the command never reached the sandbox"
    return calls[-1].kwargs["environment"]


def _run_shell(user_context: dict, command: str = "git status") -> dict:
    req = WorkspaceShellRequest(workspace_id="proj", user_context=user_context, command=command)
    return asyncio.run(ws_handler.handle_workspace_shell(req))


def test_a_user_without_integrations_gets_no_server_token(server_secrets, workspace):
    container = _container()
    with patch.object(sb, "_docker_client", return_value=_client(container)):
        _run_shell({"user": "someone"})
    env = _exec_env(container)
    leaked = {k: v for k, v in env.items() if v in _SERVER_SECRETS.values()}
    assert leaked == {}, f"server credentials reached a user's exec: {sorted(leaked)}"


def test_a_user_gets_their_own_token_not_the_servers(server_secrets, workspace):
    container = _container()
    with patch.object(sb, "_docker_client", return_value=_client(container)):
        _run_shell({"user": "someone", "github_token": "users-own-token"})
    env = _exec_env(container)
    assert env["GITHUB_TOKEN"] == "users-own-token"
    assert env["GH_TOKEN"] == "users-own-token"
    assert "GIT_TOKEN" not in env and "NEXTCLOUD_PASSWORD" not in env and "HA_TOKEN" not in env


def test_a_none_override_really_clears_the_token(server_secrets, workspace, monkeypatch):
    async def _resolve(workspace_id, user_context=None):
        return workspace, {"env": {"GITHUB_TOKEN": None, "GH_TOKEN": None}}

    monkeypatch.setattr(ws_handler, "_resolve_workspace_info", _resolve)
    monkeypatch.setattr(ws_handler, "_require_capability", lambda *a, **k: None)
    container = _container()
    with patch.object(sb, "_docker_client", return_value=_client(container)):
        _run_shell({"user": "someone", "github_token": "users-own-token"}, command="ls")
    env = _exec_env(container)
    assert "GITHUB_TOKEN" not in env and "GH_TOKEN" not in env


def test_a_new_container_carries_no_credentials(server_secrets, monkeypatch):
    client = MagicMock()
    client.containers.get.side_effect = sb.NotFound("none")
    client.containers.run.return_value = _container()
    client.containers.run.return_value.decode = None
    monkeypatch.setattr(sb, "_get_client", lambda: client)
    monkeypatch.setattr(sb, "ensure_workspace_network", lambda ws_id: None)
    monkeypatch.setattr(sb, "_host_mount_source", lambda p: p)
    monkeypatch.setattr(sb, "_image_home_for_uid", lambda *a: "/home/raven")

    sb.ensure_workspace_container("proj", "/workspaces/proj")

    create = [c for c in client.containers.run.call_args_list if c.kwargs.get("name")]
    assert create, "the sandbox was not created"
    assert create[-1].kwargs["environment"] == {"HOME": "/home/raven"}


def test_an_old_container_with_credentials_in_its_env_is_recreated():
    old = _container(["PATH=/usr/bin", "GITHUB_TOKEN=server-owner-github"])
    client = _client(old)
    assert sb._get_or_recreate(client, "wsbox-proj", "img") is None
    old.remove.assert_called_once_with(force=True)


def test_a_clean_container_is_reused():
    c = _container(["PATH=/usr/bin", "HOME=/home/raven"])
    assert sb._get_or_recreate(_client(c), "wsbox-proj", "img") is c


def test_the_shared_mapping_reads_no_server_environment(server_secrets):
    assert sb.user_credential_env({"user": "someone"}) == {}
    assert sb.user_credential_env(None) == {}
    assert sb.build_exec_env(home="/home/raven") == {
        **{k: os.environ[k] for k in sb._SANDBOX_BASE_ENV_KEYS if k in os.environ},
        "HOME": "/home/raven",
    }


def test_a_failed_home_lookup_is_not_cached():
    sb._HOME_CACHE.clear()
    c = MagicMock()
    c.id = "home-probe"
    c.exec_run.side_effect = [RuntimeError("daemon busy"), MagicMock(exit_code=0, output=b"/home/raven\n")]
    assert sb._home_for_uid(c, 1000) == "/tmp"
    assert sb._home_for_uid(c, 1000) == "/home/raven"
    assert sb._home_for_uid(c, 1000) == "/home/raven"
    assert c.exec_run.call_count == 2


def test_configure_git_credentials_writes_helper_and_gitconfig():
    """The credential helper reads GH_TOKEN/GITHUB_TOKEN from the exec's own
    environment (the calling user's), via a system-wide /etc/gitconfig."""
    calls = {}

    class FakeContainer:
        def exec_run(self, cmd, user=None):
            calls.setdefault("cmds", []).append((cmd, user))
            return None

    sb._configure_git_credentials(FakeContainer())
    joined = " ".join(str(c[0]) for c in calls["cmds"])
    assert "git-credential-gh-token" in joined
    assert "GH_TOKEN" in joined
    assert "/etc/gitconfig" in joined
    assert "helper = /usr/local/bin/git-credential-gh-token" in joined

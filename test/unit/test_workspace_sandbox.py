"""Unit tests for the per-workspace sandbox execution (services.workspace_sandbox).

These tests mock the Docker SDK so they run without a live daemon and verify
the command shaping, path translation, and return-value contract that the
execution service and workspace_runtime depend on.
"""
import asyncio
from unittest.mock import MagicMock, patch

import pytest

import services.workspace_sandbox as sb


def _fake_container(exit_code: int = 0, stdout: bytes = b"", stderr: bytes = b""):
    c = MagicMock()
    c.status = "running"
    c.exec_run.return_value = MagicMock(exit_code=exit_code, output=(stdout, stderr))
    return c


def _fake_client(container):
    client = MagicMock()
    client.containers.get.return_value = container
    client.networks.get.side_effect = sb.NotFound("nope")
    client.networks.create.return_value = MagicMock()
    return client


@pytest.fixture
def fake_docker():
    container = _fake_container(stdout=b"hello\n", stderr=b"")
    client = _fake_client(container)
    with patch.object(sb, "_docker_client", return_value=client):
        yield container, client


def test_slug_normalization():
    assert sb._slug("Raven 3D Shooter (Python)") == "raven-3d-shooter-python"
    assert sb._slug("My_Workspace/1") == "my-workspace-1"


def test_container_cwd_is_host_path(fake_docker):
    # The workspace mounts at its identical absolute host path, so a host cwd
    # must be returned unchanged (no translation needed).
    host = "/workspaces/users/default/raven"
    assert sb._to_container_cwd(f"{host}/sub", host) == f"{host}/sub"
    assert sb._to_container_cwd(host, host) == host
    assert sb._to_container_cwd(None, host) == host


def test_run_workspace_cmd_shapes_exec(fake_docker):
    container, _ = fake_docker

    async def go():
        return await sb.run_workspace_cmd(
            "raven-3d",
            "/workspaces/users/default/raven",
            ["git", "status", "--short"],
            cwd="/workspaces/users/default/raven/src",
            timeout=30.0,
            shell=False,
            env={"FOO": "bar"},
        )

    res = asyncio.run(go())
    assert res["returncode"] == 0
    assert res["stdout"] == "hello\n"
    # exec_run was called with the absolute (container-equivalent) cwd
    args, kwargs = container.exec_run.call_args
    assert args[0] == ["git", "status", "--short"]
    assert kwargs["workdir"] == "/workspaces/users/default/raven/src"
    assert kwargs["user"].startswith("1000:")
    # our env was merged in
    assert kwargs["environment"]["FOO"] == "bar"


def test_run_workspace_cmd_timeout(fake_docker):
    _container, _ = fake_docker

    async def go():
        with patch("asyncio.to_thread", side_effect=TimeoutError()):
            return await sb.run_workspace_cmd("ws", "/w", "sleep 10", shell=True, timeout=0.01)

    res = asyncio.run(go())
    assert res["returncode"] == 124
    assert "timed out" in res["stderr"]


def test_run_workspace_cmd_shell_wraps_sh(fake_docker):
    container, _ = fake_docker

    async def go():
        return await sb.run_workspace_cmd("ws", "/workspaces/w", "echo hi && ls", shell=True, timeout=5.0)

    asyncio.run(go())
    args, _ = container.exec_run.call_args
    assert args[0] == ["/bin/sh", "-c", "echo hi && ls"]

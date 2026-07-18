"""Sandbox credential inheritance: the sandbox container must inherit the user's
integration credentials (GitHub token, etc.) so `gh`/`git push` inside the
sandbox can authenticate. Without this, git push fails with rc=128 / "not
logged in" even though the execution service has the token.
"""
import os

from services.workspace_sandbox import _sandbox_credential_env, _SANDBOX_CRED_ENV_KEYS


def test_credential_env_collects_host_tokens(monkeypatch):
    for k in _SANDBOX_CRED_ENV_KEYS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("GH_TOKEN", "ghp_example")
    monkeypatch.setenv("GIT_TOKEN", "gl_example")
    monkeypatch.setenv("UNRELATED", "nope")

    env = _sandbox_credential_env()

    assert env.get("GH_TOKEN") == "ghp_example"
    assert env.get("GIT_TOKEN") == "gl_example"
    assert "UNRELATED" not in env


def test_credential_env_empty_when_no_tokens(monkeypatch):
    for k in _SANDBOX_CRED_ENV_KEYS:
        monkeypatch.delenv(k, raising=False)
    assert _sandbox_credential_env() == {}


def test_ensure_workspace_container_injects_creds(monkeypatch):
    """ensure_workspace_container must pass inherited creds into the container env."""
    import services.workspace_sandbox as ws

    for k in _SANDBOX_CRED_ENV_KEYS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("GH_TOKEN", "ghp_injected")

    created = {}

    class FakeContainer:
        def __init__(self, **kwargs):
            created.update(kwargs)

    class FakeContainers:
        def get(self, name):
            from docker.errors import NotFound

            raise NotFound("container not found")

        def run(self, *args, **kwargs):
            return FakeContainer(**kwargs)

    class FakeClient:
        containers = FakeContainers()

        def images(self):
            raise Exception("no image check needed")

        def images_get(self, img):
            return object()

    monkeypatch.setattr(ws, "_get_client", lambda: FakeClient())
    monkeypatch.setattr(ws, "ensure_workspace_network", lambda ws_id: None)
    monkeypatch.setattr(ws, "_host_mount_source", lambda p: p)
    monkeypatch.setattr(ws, "SANDBOX_IMAGE", "img:test")
    monkeypatch.setattr(ws, "SANDBOX_MEM", "1g")
    monkeypatch.setattr(ws, "SANDBOX_PIDS", 256)
    monkeypatch.setattr(ws, "SANDBOX_CPU_QUOTA", 50000)
    monkeypatch.setattr(
        ws,
        "_exec_blocking",
        lambda *a, **k: (0, "", ""),
    )

    # Patch docker client.containers.run
    import docker

    monkeypatch.setattr(
        docker.models.containers.ContainerCollection,
        "run",
        lambda self, *a, **k: FakeContainer(**k),
        raising=False,
    )

    ws.ensure_workspace_container("test-ws", "/tmp/test-ws")
    assert created.get("environment", {}).get("GH_TOKEN") == "ghp_injected"


def test_configure_git_credentials_writes_helper_and_gitconfig(monkeypatch):
    """_configure_git_credentials must install a git credential helper that reads
    GH_TOKEN/GITHUB_TOKEN and a system-wide /etc/gitconfig pointing at it, so raw
    `git push`/`git ls-remote` over HTTPS authenticate inside the sandbox."""
    import services.workspace_sandbox as ws

    calls = {}

    class FakeContainer:
        def exec_run(self, cmd, user=None):
            calls.setdefault("cmds", []).append((cmd, user))
            return None

    ws._configure_git_credentials(FakeContainer())
    assert "cmds" in calls, "expected exec_run to write credential files"
    joined = " ".join(str(c[0]) for c in calls["cmds"])
    # helper reads GH_TOKEN and is made executable
    assert "git-credential-gh-token" in joined
    assert "GH_TOKEN" in joined
    assert "/etc/gitconfig" in joined
    # the gitconfig must reference the helper by absolute path (no leading '!')
    assert "helper = /usr/local/bin/git-credential-gh-token" in joined

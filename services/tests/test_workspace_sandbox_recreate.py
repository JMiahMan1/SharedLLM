"""Sandbox image freshness: existing wsbox-* containers must be recreated when
the sandbox image changed (deploys rebuild :latest but docker compose up -d
only recreates service containers, so stale sandboxes would lack tools the
toolchain probe advertises — e.g. pdflatex after a texlive deploy)."""

from docker.errors import NotFound

import services.workspace_sandbox as ws


class FakeImage:
    def __init__(self, image_id):
        self.id = image_id


class FakeContainer:
    status = "running"

    def __init__(self, image_id):
        self.image = FakeImage(image_id)
        self.removed = False

    def start(self):
        self.status = "running"

    def remove(self, force=False):
        self.removed = True


class FakeContainers:
    def __init__(self, container):
        self._container = container

    def get(self, name):
        if self._container is None:
            raise NotFound("container not found")
        return self._container

    def run(self, *args, **kwargs):
        self.created_kwargs = kwargs
        return FakeContainer("new-image")


class FakeClient:
    def __init__(self, container, local_image_id):
        self.containers = FakeContainers(container)
        self.images = self
        self._local_image_id = local_image_id

    def get(self, img):
        return FakeImage(self._local_image_id)


def _install(monkeypatch, client):
    monkeypatch.setattr(ws, "_get_client", lambda: client)
    monkeypatch.setattr(ws, "ensure_workspace_network", lambda ws_id: None)
    monkeypatch.setattr(ws, "_host_mount_source", lambda p: p)
    monkeypatch.setattr(ws, "SANDBOX_IMAGE", "img:test")
    monkeypatch.setattr(ws, "SANDBOX_MEM", "1g")
    monkeypatch.setattr(ws, "SANDBOX_PIDS", 256)
    monkeypatch.setattr(ws, "SANDBOX_CPU_QUOTA", 50000)
    monkeypatch.setattr(ws, "_configure_git_credentials", lambda c: None)


def test_reuses_container_when_image_unchanged(monkeypatch):
    old = FakeContainer("same-image")
    client = FakeClient(old, "same-image")
    _install(monkeypatch, client)

    c = ws.ensure_workspace_container("test-ws", "/tmp/test-ws")

    assert c is old
    assert not old.removed


def test_recreates_container_when_image_changed(monkeypatch):
    old = FakeContainer("old-image")
    client = FakeClient(old, "new-image")
    _install(monkeypatch, client)

    c = ws.ensure_workspace_container("test-ws", "/tmp/test-ws")

    assert old.removed
    assert c is not old
    assert c.image.id == "new-image"


def test_recreates_in_exec_path_when_image_changed(monkeypatch):
    old = FakeContainer("old-image")
    client = FakeClient(old, "new-image")
    _install(monkeypatch, client)

    c = ws.ensure_workspace_container("test-ws", "/tmp/test-ws")

    assert old.removed
    assert c is not old


def test_exec_blocking_recreates_stale_container(monkeypatch):
    old = FakeContainer("old-image")
    client = FakeClient(old, "new-image")
    _install(monkeypatch, client)
    monkeypatch.setattr(ws, "_sandbox_credential_env", lambda: {})
    calls = {}

    class FakeExecResult:
        exit_code = 0
        output = (b"out", b"")

    def fake_exec(container, cmd, **kwargs):
        calls["container"] = container
        return FakeExecResult()

    FakeContainer.exec_run = fake_exec

    rc, out, err = ws._exec_blocking(
        "test-ws", "/tmp/test-ws", ["/bin/sh", "-c", "echo hi"],
        cwd="/tmp/test-ws", env=None, uid=1000, gid=1000, image=None,
    )

    assert old.removed, "stale sandbox must be recreated before exec"
    assert rc == 0
    assert out == "out"

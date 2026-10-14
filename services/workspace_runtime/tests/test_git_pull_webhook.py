"""The GitHub push webhook, and what it is allowed to do to a workspace.

Three behaviours are pinned, all of them learned from the production log:

- A workspace whose local git is authoritative is not pulled at all. The two
  sharedllm workspaces carry thousands of commits the remote does not, so every
  push produced "You have divergent branches" and downloaded nothing.
- A workspace that does track the remote is brought up to date, which the old
  bare ``git pull`` could not do once the branch had diverged.
- A genuine divergence is reported with an explanation and no merge is created:
  an automatic pull must never choose a reconciliation on someone's behalf.
"""

import os
import subprocess

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, StaticPool, create_engine

os.environ["WORKSPACE_RUNTIME_ROOT"] = os.path.abspath(".tmp")

import services.workspace_runtime.main as main

SECRET = {"X-Internal-Secret": "test-secret"}
#: The root the service itself resolves from WORKSPACE_RUNTIME_ROOT. The create
#: route refuses a local_path outside it, so the fixtures have to live here
#: rather than in a private directory of this module's own.
ROOT = os.path.abspath(".tmp/workspaces")


@pytest.fixture(name="session")
def session_fixture():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


os.makedirs(ROOT, exist_ok=True)


@pytest.fixture(name="client")
def client_fixture(session, monkeypatch):
    from services.workspace_runtime.main import app

    monkeypatch.setattr(main, "engine", session.bind)
    monkeypatch.setattr(main, "WORKSPACE_ROOT", main.Path(ROOT))
    main._WORKSPACE_ROOT_CACHE["ts"] = 0.0
    monkeypatch.setattr(
        main, "_resolve_identity_context", lambda ref: {"user": "test", "is_admin": True}
    )
    # The handler imports GIT_WEBHOOK_SECRET from the config module at call time,
    # so the module attribute is what has to change, not the environment.
    import services.config as cfg

    monkeypatch.setattr(cfg, "GIT_WEBHOOK_SECRET", "hook-secret", raising=False)
    yield TestClient(app)


def _git(cwd: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    )


def _clone_of_remote(base: str, name: str) -> tuple[str, str]:
    """A bare remote with one commit, plus a clone of it."""
    remote = os.path.join(base, "remote.git")
    seed = os.path.join(base, "seed")
    work = os.path.join(base, name)
    subprocess.run(["git", "init", "--bare", "-b", "main", remote], check=True, capture_output=True)
    subprocess.run(["git", "clone", remote, seed], check=True, capture_output=True)
    for repo in (seed,):
        _git(repo, "config", "user.email", "test@example.com")
        _git(repo, "config", "user.name", "Test User")
        _git(repo, "config", "commit.gpgsign", "false")
    with open(os.path.join(seed, "file.txt"), "w") as f:
        f.write("original\n")
    _git(seed, "add", "file.txt")
    _git(seed, "commit", "-m", "init")
    _git(seed, "push", "origin", "main")
    subprocess.run(["git", "clone", remote, work], check=True, capture_output=True)
    _git(work, "config", "user.email", "test@example.com")
    _git(work, "config", "user.name", "Test User")
    _git(work, "config", "commit.gpgsign", "false")
    return remote, work


def _push_remote_change(base: str, remote: str, text: str) -> None:
    pusher = os.path.join(base, f"pusher-{text}")
    subprocess.run(["git", "clone", remote, pusher], check=True, capture_output=True)
    _git(pusher, "config", "user.email", "test@example.com")
    _git(pusher, "config", "user.name", "Test User")
    _git(pusher, "config", "commit.gpgsign", "false")
    with open(os.path.join(pusher, "file.txt"), "w") as f:
        f.write(f"{text}\n")
    _git(pusher, "add", "file.txt")
    _git(pusher, "commit", "-m", text)
    _git(pusher, "push", "origin", "main")


def _workspace(client: TestClient, ws_id: str, path: str, *, sync_mode: str, repo_url: str = ""):
    resp = client.post(
        "/workspaces",
        json={
            "id": ws_id,
            "display_name": ws_id,
            "local_path": path,
            "repo_url": repo_url,
            "default_branch": "main",
            "access_policy": "authenticated",
            "scope": "system",
            "sync_mode": sync_mode,
            "auto_pull_enabled": True,
        },
        headers=SECRET,
    )
    assert resp.status_code == 200, resp.text


def _pull(client: TestClient, ws_id: str):
    return client.post(
        f"/webhook/git-pull/{ws_id}",
        params={"token": "hook-secret"},
        headers=SECRET,
    )


def test_a_workspace_that_is_behind_the_remote_is_caught_up(client: TestClient):
    import tempfile

    base = tempfile.mkdtemp(dir=ROOT)
    remote, work = _clone_of_remote(base, "work")
    _push_remote_change(base, remote, "changed-by-remote")
    _workspace(client, "tracking", work, sync_mode="git", repo_url=remote)

    resp = _pull(client, "tracking")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "SUCCESS"
    with open(os.path.join(work, "file.txt")) as f:
        assert "changed-by-remote" in f.read()


def test_an_authoritative_workspace_is_not_pulled_at_all(client: TestClient):
    import tempfile

    base = tempfile.mkdtemp(dir=ROOT)
    remote, work = _clone_of_remote(base, "authoritative")
    _push_remote_change(base, remote, "not-for-us")
    _workspace(
        client,
        "authoritative",
        work,
        sync_mode="local_git_authoritative",
        repo_url=remote,
    )

    resp = _pull(client, "authoritative")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "SKIPPED"
    assert "authoritative" in body["message"]
    # The point of skipping: the local copy is untouched.
    with open(os.path.join(work, "file.txt")) as f:
        assert "original" in f.read()


def test_a_diverged_workspace_is_reported_and_left_alone(client: TestClient):
    import tempfile

    base = tempfile.mkdtemp(dir=ROOT)
    remote, work = _clone_of_remote(base, "diverged")
    # A commit of its own, and a different one on the remote.
    with open(os.path.join(work, "local.txt"), "w") as f:
        f.write("local work\n")
    _git(work, "add", "local.txt")
    _git(work, "commit", "-m", "local only")
    _push_remote_change(base, remote, "remote only")
    _workspace(client, "diverged", work, sync_mode="git", repo_url=remote)

    before = _git(work, "rev-list", "--count", "HEAD").stdout.strip()
    resp = _pull(client, "diverged")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ERROR"
    assert "pull by hand" in body["detail"]
    # Nothing was merged or rebased on its behalf.
    after = _git(work, "rev-list", "--count", "HEAD").stdout.strip()
    assert after == before
    status = _git(work, "status", "--porcelain").stdout
    assert "local.txt" not in status

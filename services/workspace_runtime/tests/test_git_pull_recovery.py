import os
import subprocess

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, StaticPool, create_engine

os.environ["INTERNAL_SECRET"] = "test-secret"

# Import the service module at collection time so SQLModel metadata (Workspace table)
# is registered before we call create_all() in the fixtures below.
import services.workspace_runtime.main as main


@pytest.fixture(name="session")
def session_fixture():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture(name="client")
def client_fixture(session):
    from services.workspace_runtime.main import app

    original_engine = main.engine
    main.engine = session.bind
    # The identity service is unavailable in unit tests; act as admin.
    main._resolve_identity_context = lambda ref: {"user": "test", "is_admin": True}
    client = TestClient(app)
    yield client
    main.engine = original_engine


def _git(cwd: str, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


def _make_remote_with_conflict(base: str) -> str:
    remote = os.path.join(base, "remote.git")
    work = os.path.join(base, "work")
    remote_work = os.path.join(base, "remote_work")
    subprocess.run(["git", "init", "--bare", "-b", "main", remote], check=True, capture_output=True)
    subprocess.run(["git", "clone", remote, work], check=True, capture_output=True)
    _git(work, "checkout", "-b", "main")
    with open(os.path.join(work, "file.txt"), "w") as f:
        f.write("original\n")
    _git(work, "add", "file.txt")
    _git(work, "commit", "-m", "init")
    _git(work, "push", "origin", "main")

    # Divergent change pushed straight to the remote
    subprocess.run(["git", "clone", remote, remote_work], check=True, capture_output=True)
    with open(os.path.join(remote_work, "file.txt"), "w") as f:
        f.write("changed-by-remote\n")
    _git(remote_work, "add", "file.txt")
    _git(remote_work, "commit", "-m", "remote change")
    _git(remote_work, "push", "origin", "main")
    return work


def test_pull_recovers_dirty_tree(client: TestClient):
    import tempfile

    base = tempfile.mkdtemp()
    work = _make_remote_with_conflict(base)

    # Uncommitted local change that the incoming pull would overwrite
    with open(os.path.join(work, "file.txt"), "w") as f:
        f.write("local-uncommitted\n")

    client.post(
        "/workspaces",
        json={
            "id": "pulltest",
            "display_name": "Pull Test",
            "local_path": work,
            "access_policy": "authenticated",
            "scope": "user",
        },
        headers={"X-Internal-Secret": "test-secret"},
    )

    resp = client.post(
        "/git/pull",
        json={"workspace_id": "pulltest"},
        headers={"X-Internal-Secret": "test-secret"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "SUCCESS"
    assert data["recovered"] is True
    assert "stash" in (data.get("recovery_note") or "").lower()

    # The remote change should now be present
    with open(os.path.join(work, "file.txt")) as f:
        assert "changed-by-remote" in f.read()

    # The stashed local work must be preserved for the user to reapply
    stash_list = subprocess.run(
        ["git", "stash", "list"], cwd=work, capture_output=True, text=True
    )
    assert "sharedllm-auto-pull" in stash_list.stdout


def test_pull_clean_repo_no_recovery(client: TestClient):
    import tempfile

    base = tempfile.mkdtemp()
    remote = os.path.join(base, "remote.git")
    work = os.path.join(base, "work")
    subprocess.run(["git", "init", "--bare", "-b", "main", remote], check=True, capture_output=True)
    subprocess.run(["git", "clone", remote, work], check=True, capture_output=True)
    _git(work, "checkout", "-b", "main")
    with open(os.path.join(work, "file.txt"), "w") as f:
        f.write("original\n")
    _git(work, "add", "file.txt")
    _git(work, "commit", "-m", "init")
    _git(work, "push", "origin", "main")

    client.post(
        "/workspaces",
        json={
            "id": "pullclean",
            "display_name": "Pull Clean",
            "local_path": work,
            "access_policy": "authenticated",
            "scope": "user",
        },
        headers={"X-Internal-Secret": "test-secret"},
    )

    resp = client.post(
        "/git/pull",
        json={"workspace_id": "pullclean"},
        headers={"X-Internal-Secret": "test-secret"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["recovered"] is False
    assert data.get("recovery_note") is None

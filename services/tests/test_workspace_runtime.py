import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from workspace_runtime.main import app
import workspace_runtime.main as runtime

pytestmark = pytest.mark.local_only


@pytest.fixture
def runtime_env(tmp_path, monkeypatch):
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    repo_dir = workspace_root / "demo"
    repo_dir.mkdir()

    (repo_dir / "sample.py").write_text("VALUE = 1\n")
    (repo_dir / "test_sample.py").write_text(
        "from sample import VALUE\n\n\ndef test_value():\n    assert VALUE == 1\n"
    )

    subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_dir, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo_dir, check=True, capture_output=True, text=True)
    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo_dir, check=True, capture_output=True, text=True)

    registry_path = tmp_path / "workspaces.json"
    registry_path.write_text(
        json.dumps(
            {
                "workspaces": [
                    {
                        "id": "demo",
                        "display_name": "Demo Workspace",
                        "access_policy": "authenticated",
                        "local_path": "demo",
                        "default_branch": "main",
                    },
                    {
                        "id": "demo_system",
                        "display_name": "Demo System Workspace",
                        "scope": "system",
                        "access_policy": "authenticated",
                        "capabilities": ["read", "git_status", "git_diff"],
                        "local_path": "demo",
                        "default_branch": "main",
                    },
                    {
                        "id": "demo_admin",
                        "display_name": "Demo Admin Workspace",
                        "access_policy": "admin_only",
                        "local_path": "demo",
                        "default_branch": "main",
                    }
                ]
            }
        )
    )

    monkeypatch.setattr(runtime, "INTERNAL_SECRET", "test-secret")
    monkeypatch.setattr(runtime, "WORKSPACE_ROOT", workspace_root.resolve())
    monkeypatch.setattr(runtime, "WORKSPACE_REGISTRY_PATH", str(registry_path))
    monkeypatch.setattr(
        runtime,
        "_resolve_identity_context",
        lambda ref: {"user": ref.rag_user, "is_admin": ref.rag_user == "admin"} if ref.rag_user else None,
    )

    return repo_dir


@pytest.fixture
def client(runtime_env):
    with TestClient(app) as test_client:
        yield test_client


def _headers():
    return {"X-Internal-Secret": "test-secret"}


def test_list_workspaces(client):
    resp = client.get("/workspaces", params={"rag_user": "jeremiah"}, headers=_headers())
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "SUCCESS"
    assert data["workspaces"][0]["id"] == "demo"
    assert data["workspaces"][0]["available"] is True


def test_list_workspaces_allows_admin_override(client):
    resp = client.get("/workspaces", params={"rag_user": "admin"}, headers=_headers())
    assert resp.status_code == 200
    data = resp.json()
    ids = {item["id"] for item in data["workspaces"]}
    assert "demo" in ids
    assert "demo_system" in ids
    assert "demo_admin" in ids


def test_list_workspaces_hides_admin_only_from_non_admin(client):
    resp = client.get("/workspaces", params={"rag_user": "jeremiah"}, headers=_headers())
    assert resp.status_code == 200
    data = resp.json()
    ids = {item["id"] for item in data["workspaces"]}
    assert "demo" in ids
    assert "demo_system" in ids
    assert "demo_admin" not in ids


def test_read_file_blocks_parent_traversal(client):
    resp = client.post(
        "/files/read",
        headers=_headers(),
        json={"workspace_id": "demo", "rag_user": "jeremiah", "relative_path": "../secret.txt"},
    )
    assert resp.status_code == 400


def test_git_status_reports_dirty_workspace(client, runtime_env):
    (runtime_env / "sample.py").write_text("VALUE = 2\n")

    resp = client.post("/git/status", headers=_headers(), json={"workspace_id": "demo", "rag_user": "jeremiah"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "SUCCESS"
    assert data["dirty"] is True
    assert any("sample.py" in line for line in data["porcelain"])


def test_pytest_endpoint_runs_targeted_tests(client):
    resp = client.post(
        "/tests/pytest",
        headers=_headers(),
        json={"workspace_id": "demo", "rag_user": "jeremiah", "targets": ["test_sample.py"]},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "SUCCESS"
    assert data["passed"] is True
    assert data["returncode"] == 0


def test_system_workspace_blocks_pytest_for_non_admin(client):
    resp = client.post(
        "/tests/pytest",
        headers=_headers(),
        json={"workspace_id": "demo_system", "rag_user": "jeremiah", "targets": ["test_sample.py"]},
    )
    assert resp.status_code == 403


def test_system_workspace_allows_pytest_for_admin(client):
    resp = client.post(
        "/tests/pytest",
        headers=_headers(),
        json={"workspace_id": "demo_system", "rag_user": "admin", "targets": ["test_sample.py"]},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["passed"] is True

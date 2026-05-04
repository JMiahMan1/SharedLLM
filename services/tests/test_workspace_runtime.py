import json
import subprocess
import hashlib
from pathlib import Path

import pytest
from fastapi import HTTPException

import workspace_runtime.main as runtime

pytestmark = pytest.mark.local_only


@pytest.fixture(autouse=True)
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


def test_list_workspaces():
    data = runtime.list_workspaces(rag_user="jeremiah", x_internal_secret="test-secret")
    assert data["status"] == "SUCCESS"
    assert data["workspaces"][0]["id"] == "demo"
    assert data["workspaces"][0]["available"] is True


def test_list_workspaces_allows_admin_override():
    data = runtime.list_workspaces(rag_user="admin", x_internal_secret="test-secret")
    ids = {item["id"] for item in data["workspaces"]}
    assert "demo" in ids
    assert "demo_system" in ids
    assert "demo_admin" in ids


def test_list_workspaces_hides_admin_only_from_non_admin():
    data = runtime.list_workspaces(rag_user="jeremiah", x_internal_secret="test-secret")
    ids = {item["id"] for item in data["workspaces"]}
    assert "demo" in ids
    assert "demo_system" in ids
    assert "demo_admin" not in ids


def test_read_file_blocks_parent_traversal():
    with pytest.raises(HTTPException) as exc:
        runtime.read_file(
            runtime.FileReadRequest(workspace_id="demo", rag_user="jeremiah", relative_path="../secret.txt"),
            "test-secret",
        )
    assert exc.value.status_code == 400


def test_write_file_updates_user_workspace(runtime_env):
    original = (runtime_env / "sample.py").read_text()
    original_sha = hashlib.sha256(original.encode()).hexdigest()

    data = runtime.write_file(
        runtime.FileWriteRequest(
            workspace_id="demo",
            rag_user="jeremiah",
            relative_path="sample.py",
            content="VALUE = 3\n",
            expected_sha256=original_sha,
        ),
        "test-secret",
    )
    assert data["status"] == "SUCCESS"
    assert data["created"] is False
    assert data["previous_sha256"] == original_sha
    assert (runtime_env / "sample.py").read_text() == "VALUE = 3\n"


def test_write_file_rejects_conflict():
    with pytest.raises(HTTPException) as exc:
        runtime.write_file(
            runtime.FileWriteRequest(
                workspace_id="demo",
                rag_user="jeremiah",
                relative_path="sample.py",
                content="VALUE = 4\n",
                expected_sha256="deadbeef",
            ),
            "test-secret",
        )
    assert exc.value.status_code == 409


def test_system_workspace_blocks_write_for_non_admin():
    with pytest.raises(HTTPException) as exc:
        runtime.write_file(
            runtime.FileWriteRequest(
                workspace_id="demo_system",
                rag_user="jeremiah",
                relative_path="sample.py",
                content="VALUE = 5\n",
            ),
            "test-secret",
        )
    assert exc.value.status_code == 403


def test_git_status_reports_dirty_workspace(runtime_env):
    (runtime_env / "sample.py").write_text("VALUE = 2\n")

    data = runtime.git_status(runtime.WorkspaceRef(workspace_id="demo", rag_user="jeremiah"), "test-secret")
    assert data["status"] == "SUCCESS"
    assert data["dirty"] is True
    assert any("sample.py" in line for line in data["porcelain"])


def test_pytest_endpoint_runs_targeted_tests():
    data = runtime.run_pytest(
        runtime.PytestRequest(workspace_id="demo", rag_user="jeremiah", targets=["test_sample.py"]),
        "test-secret",
    )
    assert data["status"] == "SUCCESS"
    assert data["passed"] is True
    assert data["returncode"] == 0


def test_system_workspace_blocks_pytest_for_non_admin():
    with pytest.raises(HTTPException) as exc:
        runtime.run_pytest(
            runtime.PytestRequest(workspace_id="demo_system", rag_user="jeremiah", targets=["test_sample.py"]),
            "test-secret",
        )
    assert exc.value.status_code == 403


def test_system_workspace_allows_pytest_for_admin():
    data = runtime.run_pytest(
        runtime.PytestRequest(workspace_id="demo_system", rag_user="admin", targets=["test_sample.py"]),
        "test-secret",
    )
    assert data["passed"] is True

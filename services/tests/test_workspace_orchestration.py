import json
import os
import subprocess

import pytest
from fastapi import HTTPException

import services.workspace_runtime.main as runtime
from services.workspace_runtime.main import (
    FileReadRequest,
    FileWriteRequest,
    GitPushRequest,
    WorkflowWriteSyncCommitRequest,
    git_push,
    read_file,
    workflow_write_sync_commit,
    write_file,
)

os.environ["INTERNAL_SECRET"] = "test-secret"


@pytest.fixture
def workspace_env(monkeypatch, tmp_path):
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    demo_dir = workspace_root / "demo"
    demo_dir.mkdir()

    subprocess.run(["git", "init", "-b", "main"], cwd=demo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=demo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=demo_dir, check=True, capture_output=True)
    (demo_dir / ".gitignore").write_text("__pycache__/\n")
    subprocess.run(["git", "add", "."], cwd=demo_dir, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=demo_dir, check=True, capture_output=True)

    remote_dir = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote_dir)], check=True, capture_output=True)
    subprocess.run(["git", "remote", "add", "origin", str(remote_dir)], cwd=demo_dir, check=True, capture_output=True)

    registry_path = tmp_path / "workspaces.json"
    registry_path.write_text(
        json.dumps(
            {
                "workspaces": [
                    {
                        "id": "demo",
                        "display_name": "Demo Workspace",
                        "local_path": "demo",
                        "access_policy": "authenticated",
                    }
                ]
            }
        )
    )

    from sqlmodel import Session, SQLModel, StaticPool, create_engine

    import services.workspace_runtime.database as db
    import services.workspace_runtime.main as main
    from services.workspace_runtime.models import Workspace

    test_engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    monkeypatch.setattr(db, "engine", test_engine)
    monkeypatch.setattr(main, "engine", test_engine)

    SQLModel.metadata.create_all(test_engine)

    with Session(test_engine) as session:
        ws = Workspace(
            id="demo",
            display_name="Demo Workspace",
            local_path="demo",
            sync_mode="git",
            scope="user",
            capabilities=["read", "write"]
        )
        session.add(ws)
        session.commit()

    from unittest.mock import MagicMock
    mock_redis = MagicMock()
    mock_redis.zcard.return_value = 0
    monkeypatch.setattr(runtime, "_get_redis", lambda: mock_redis)

    monkeypatch.setattr(runtime, "WORKSPACE_ROOT", workspace_root)
    monkeypatch.setattr(runtime, "get_workspace_root", lambda: workspace_root)
    monkeypatch.setattr(runtime, "WORKSPACE_REGISTRY_PATH", str(registry_path))
    monkeypatch.setattr(runtime, "INTERNAL_SECRET", "test-secret")
    monkeypatch.setattr(runtime, "_load_registry", lambda: json.loads(registry_path.read_text())["workspaces"])

    def mock_resolve(ref):
        return {
            "user": ref.rag_user or "admin",
            "is_admin": True,
            "forbidden_branches": ["main", "master", "development", "dev"],
            "nextcloud_url": "http://cloud",
            "nextcloud_user": "admin",
            "nextcloud_pass": "pass",
        }

    monkeypatch.setattr(runtime, "_resolve_identity_context", mock_resolve)
    monkeypatch.setattr(
        runtime,
        "_run_lint_for_file",
        lambda workspace_path, relative_path: {
            "path": relative_path,
            "passed": True,
            "results": [{"tool": "mock-lint", "returncode": 0, "output": ""}],
        },
    )

    yield {
        "workspace_root": workspace_root,
        "demo_dir": demo_dir,
        "remote_dir": remote_dir,
    }


def test_workspace_file_operations(workspace_env):
    write_resp = write_file(
        FileWriteRequest(
            workspace_id="demo",
            rag_user="admin",
            relative_path="temp_test.py",
            content="print('hello')",
        ),
        "test-secret",
    )
    assert write_resp["status"] == "SUCCESS"

    read_resp = read_file(
        FileReadRequest(
            workspace_id="demo",
            rag_user="admin",
            relative_path="temp_test.py",
        ),
        "test-secret",
    )
    assert read_resp["content"] == "print('hello')"


def test_autonomous_fix_it_loop(workspace_env):
    with pytest.raises(HTTPException) as exc_info:
        workflow_write_sync_commit(
            WorkflowWriteSyncCommitRequest(
                workspace_id="demo",
                rag_user="admin",
                relative_path="broken.py",
                content="def broken_func() return True",
                commit_message="test commit",
                pytest_targets=["broken.py"],
                sync_to_provider=False,
            ),
            "test-secret",
        )

    assert exc_info.value.status_code == 400
    assert "Pytest failed" in exc_info.value.detail


def test_workflow_requires_pytest_before_push(workspace_env):
    with pytest.raises(HTTPException) as exc_info:
        workflow_write_sync_commit(
            WorkflowWriteSyncCommitRequest(
                workspace_id="demo",
                rag_user="admin",
                relative_path="test_safe.py",
                content="def test_ok():\n    assert True\n",
                commit_message="push without tests",
                sync_to_provider=False,
                push=True,
            ),
            "test-secret",
        )

    assert exc_info.value.status_code == 400
    assert "pytest_targets are required" in exc_info.value.detail


def test_git_push_blocks_protected_branch(workspace_env):
    with pytest.raises(HTTPException) as exc_info:
        git_push(
            GitPushRequest(workspace_id="demo", rag_user="admin"),
            "test-secret",
        )

    assert exc_info.value.status_code == 403
    assert "protected branch 'main'" in exc_info.value.detail


def test_workflow_auto_creates_review_branch_and_returns_review_metadata(workspace_env):
    result = workflow_write_sync_commit(
        WorkflowWriteSyncCommitRequest(
            workspace_id="demo",
            rag_user="admin",
            relative_path="test_safe.py",
            content="def test_ok():\n    assert True\n",
            commit_message="Add safe workflow example",
            pytest_targets=["test_safe.py"],
            sync_to_provider=False,
            push=True,
            set_upstream=True,
        ),
        "test-secret",
    )

    assert result is not None
    assert result["push"]["branch"].startswith("raven/admin/test_safe-")
    assert result["review"]["head"] == result["push"]["branch"]
    assert result["review"]["base"] == "main"
    review_summary = result["review"]["summary"]
    pytest_result: dict = review_summary["pytest"]
    lint_result: list = review_summary["lint"]
    assert isinstance(pytest_result, dict)
    assert isinstance(lint_result, list)
    assert "test_safe.py" in review_summary["changed_files"]
    assert pytest_result["passed"] is True
    assert lint_result[0]["passed"] is True


def test_path_traversal_protection(workspace_env):
    with pytest.raises(HTTPException) as read_exc:
        read_file(
            FileReadRequest(
                workspace_id="demo",
                rag_user="admin",
                relative_path="../../../etc/passwd",
            ),
            "test-secret",
        )
    assert read_exc.value.status_code in [400, 403]

    with pytest.raises(HTTPException) as write_exc:
        write_file(
            FileWriteRequest(
                workspace_id="demo",
                rag_user="admin",
                relative_path="../hack.txt",
                content="pwned",
            ),
            "test-secret",
        )
    assert write_exc.value.status_code in [400, 403]

import json
import subprocess
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import workspace_runtime.main as runtime
from workspace_runtime.models import Workspace
from sqlmodel import Session, SQLModel, create_engine

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

    subprocess.run(["git", "init", "-b", "main"], cwd=repo_dir, check=True, capture_output=True, text=True)
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
                        "nextcloud_path": "/Code/SharedLLM",
                        "default_branch": "main",
                    },
                    {
                        "id": "demo_system",
                        "display_name": "Demo System Workspace",
                        "scope": "system",
                        "access_policy": "authenticated",
                        "capabilities": ["read", "git_status", "git_diff"],
                        "local_path": "demo",
                        "nextcloud_path": "/Code/SharedLLM",
                        "default_branch": "main",
                    },
                    {
                        "id": "demo_admin",
                        "display_name": "Demo Admin Workspace",
                        "access_policy": "admin_only",
                        "local_path": "demo",
                        "nextcloud_path": "/Code/SharedLLM",
                        "default_branch": "main",
                    }
                ]
            }
        )
    )

    monkeypatch.setattr(runtime, "INTERNAL_SECRET", "test-secret")
    monkeypatch.setattr(runtime, "WORKSPACE_ROOT", workspace_root.resolve())
    monkeypatch.setattr(runtime, "WORKSPACE_REGISTRY_PATH", str(registry_path))
    
    # DB Setup for test
    test_engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(test_engine)
    monkeypatch.setattr(runtime, "engine", test_engine)
    
    # Seed DB for tests
    with Session(test_engine) as session:
        data = json.loads(registry_path.read_text())
        for ws_data in data["workspaces"]:
            session.add(Workspace(**ws_data))
        session.commit()
    def resolve_identity(ref):
        if not ref.rag_user:
            return None
        if ref.rag_user == "admin":
            return {
                "user": "admin",
                "is_admin": True,
                "github_user": "admin-gh",
                "nextcloud_url": "https://cloud.local",
                "nextcloud_user": "admin",
                "nextcloud_pass": "secret",
            }
        if ref.rag_user == "gitlab-user":
            return {
                "user": "gitlab-user",
                "is_admin": False,
                "gitlab_user": "gitlab-handle",
                "nextcloud_url": "https://cloud.local",
                "nextcloud_user": "gitlab-user",
                "nextcloud_pass": "secret",
            }
        return {
            "user": ref.rag_user,
            "is_admin": False,
            "github_user": "jeremiah-gh",
            "nextcloud_url": "https://cloud.local",
            "nextcloud_user": ref.rag_user,
            "nextcloud_pass": "secret",
        }

    monkeypatch.setattr(runtime, "_resolve_identity_context", resolve_identity)

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


def test_list_files_returns_workspace_entries(runtime_env):
    docs_dir = runtime_env / "docs"
    docs_dir.mkdir()
    (docs_dir / "note.md").write_text("# note\n")

    data = runtime.list_files(
        runtime.FileListRequest(workspace_id="demo", rag_user="jeremiah", relative_path=".", recursive=True, max_depth=2),
        "test-secret",
    )
    paths = {item["path"] for item in data["entries"]}
    assert data["status"] == "SUCCESS"
    assert "sample.py" in paths
    assert "test_sample.py" in paths
    assert "docs" in paths
    assert "docs/note.md" in paths


def test_list_files_truncates_at_max_entries(runtime_env):
    for idx in range(5):
        (runtime_env / f"extra_{idx}.txt").write_text(f"{idx}\n")

    data = runtime.list_files(
        runtime.FileListRequest(
            workspace_id="demo",
            rag_user="jeremiah",
            relative_path=".",
            recursive=False,
            max_entries=3,
        ),
        "test-secret",
    )
    assert data["status"] == "SUCCESS"
    assert len(data["entries"]) == 3
    assert data["truncated"] is True


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


def test_write_file_applies_patch(runtime_env):
    (runtime_env / "sample.py").write_text("LINE 1\nLINE 2\n")
    patch_content = """--- sample.py
+++ sample.py
@@ -1,2 +1,2 @@
-LINE 1
+MODIFIED 1
 LINE 2
"""
    data = runtime.write_file(
        runtime.FileWriteRequest(
            workspace_id="demo",
            rag_user="jeremiah",
            relative_path="sample.py",
            patch=patch_content,
        ),
        "test-secret",
    )
    assert data["status"] == "SUCCESS"
    assert "MODIFIED 1\nLINE 2\n" in (runtime_env / "sample.py").read_text()


def test_provider_scan_uses_workspace_nextcloud_binding(monkeypatch):
    calls = []

    def fake_post(url, json=None, timeout=None):
        calls.append({"url": url, "json": json, "timeout": timeout})
        return SimpleNamespace(
            status_code=200,
            json=lambda: {
                "status": "SUCCESS",
                "count": 1,
                "entries": [{"path": "/Code/SharedLLM/sample.py", "name": "sample.py", "is_dir": False}],
            },
            text="",
        )

    monkeypatch.setattr(runtime.httpx, "post", fake_post)
    data = runtime.provider_scan(
        runtime.ProviderScanRequest(workspace_id="demo", rag_user="jeremiah", recursive=True),
        "test-secret",
    )
    assert data["status"] == "SUCCESS"
    assert data["provider_kind"] == "nextcloud"
    assert calls[0]["url"].endswith("/providers/list")
    assert calls[0]["json"]["path"] == "/Code/SharedLLM"


def test_provider_sync_file_writes_local_file_to_provider(runtime_env, monkeypatch):
    calls = []

    def fake_post(url, json=None, timeout=None):
        calls.append({"url": url, "json": json, "timeout": timeout})
        return SimpleNamespace(
            status_code=200,
            json=lambda: {
                "status": "SUCCESS",
                "result": {"path": json["path"], "bytes_written": len(json["content"].encode("utf-8")), "verified": True},
            },
            text="",
        )

    monkeypatch.setattr(runtime.httpx, "post", fake_post)
    (runtime_env / "sample.py").write_text("VALUE = 11\n")

    data = runtime.provider_sync_file(
        runtime.ProviderSyncFileRequest(workspace_id="demo", rag_user="jeremiah", relative_path="sample.py"),
        "test-secret",
    )
    assert data["status"] == "SUCCESS"
    assert data["provider_path"] == "/Code/SharedLLM/sample.py"
    assert calls[0]["url"].endswith("/providers/write")
    assert calls[0]["json"]["content"] == "VALUE = 11\n"


def test_workflow_write_sync_commit_updates_file_and_commits(runtime_env, monkeypatch):
    calls = []

    def fake_post(url, json=None, timeout=None):
        calls.append({"url": url, "json": json, "timeout": timeout})
        if url.endswith("/providers/write"):
            return SimpleNamespace(
                status_code=200,
                json=lambda: {
                    "status": "SUCCESS",
                    "result": {"path": json["path"], "bytes_written": len(json["content"].encode("utf-8")), "verified": True},
                },
                text="",
            )
        raise AssertionError(f"Unexpected URL {url}")

    monkeypatch.setattr(runtime.httpx, "post", fake_post)
    data = runtime.workflow_write_sync_commit(
        runtime.WorkflowWriteSyncCommitRequest(
            workspace_id="demo",
            rag_user="jeremiah",
            relative_path="sample.py",
            content="VALUE = 21\n",
            commit_message="Workflow update sample",
            sync_to_provider=True,
        ),
        "test-secret",
    )
    assert data["status"] == "SUCCESS"
    assert (runtime_env / "sample.py").read_text() == "VALUE = 21\n"
    assert data["provider_sync"]["provider_path"] == "/Code/SharedLLM/sample.py"
    assert data["commit"]["author_email"] == "jeremiah-gh@users.noreply.github.com"
    assert calls[0]["url"].endswith("/providers/write")

    commit_subject = subprocess.run(
        ["git", "log", "-1", "--format=%s"],
        cwd=runtime_env,
        check=True,
        capture_output=True,
        text=True,
    )
    assert commit_subject.stdout.strip() == "Workflow update sample"


def test_workflow_write_sync_commit_can_push_to_local_remote(runtime_env, monkeypatch, tmp_path):
    remote_dir = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote_dir)], check=True, capture_output=True, text=True)
    current_branch = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=runtime_env,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "remote", "add", "origin", str(remote_dir)],
        cwd=runtime_env,
        check=True,
        capture_output=True,
        text=True,
    )

    def fake_post(url, json=None, timeout=None):
        return SimpleNamespace(
            status_code=200,
            json=lambda: {
                "status": "SUCCESS",
                "result": {"path": json["path"], "bytes_written": len(json["content"].encode("utf-8")), "verified": True},
            },
            text="",
        )

    monkeypatch.setattr(runtime.httpx, "post", fake_post)
    data = runtime.workflow_write_sync_commit(
        runtime.WorkflowWriteSyncCommitRequest(
            workspace_id="demo",
            rag_user="jeremiah",
            relative_path="sample.py",
            content="VALUE = 22\n",
            commit_message="Workflow push sample",
            sync_to_provider=True,
            push=True,
            set_upstream=True,
        ),
        "test-secret",
    )
    assert data["status"] == "SUCCESS"
    assert data["push"]["remote"] == "origin"
    assert data["push"]["branch"] == current_branch
    assert data["push"]["upstream"] == f"origin/{current_branch}"

    remote_head = subprocess.run(
        ["git", "--git-dir", str(remote_dir), "rev-parse", f"refs/heads/{current_branch}"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert remote_head.stdout.strip() == data["commit"]["commit"]


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


def test_git_add_stages_specific_paths(runtime_env):
    (runtime_env / "sample.py").write_text("VALUE = 7\n")

    data = runtime.git_add(
        runtime.GitAddRequest(workspace_id="demo", rag_user="jeremiah", pathspecs=["sample.py"]),
        "test-secret",
    )
    assert data["status"] == "SUCCESS"
    assert any(line.endswith("sample.py") for line in data["porcelain"])


def test_git_commit_stages_and_commits_with_github_derived_author(runtime_env):
    (runtime_env / "sample.py").write_text("VALUE = 8\n")

    data = runtime.git_commit(
        runtime.GitCommitRequest(
            workspace_id="demo",
            rag_user="jeremiah",
            message="Update sample value",
            pathspecs=["sample.py"],
        ),
        "test-secret",
    )
    assert data["status"] == "SUCCESS"
    assert data["author_name"] == "jeremiah"
    assert data["author_email"] == "jeremiah-gh@users.noreply.github.com"

    author = subprocess.run(
        ["git", "log", "-1", "--format=%an <%ae>"],
        cwd=runtime_env,
        check=True,
        capture_output=True,
        text=True,
    )
    assert author.stdout.strip() == "jeremiah <jeremiah-gh@users.noreply.github.com>"


def test_git_commit_derives_gitlab_author_when_github_missing(runtime_env):
    data = runtime.git_commit(
        runtime.GitCommitRequest(
            workspace_id="demo",
            rag_user="gitlab-user",
            message="Empty commit for author test",
            allow_empty=True,
        ),
        "test-secret",
    )
    assert data["status"] == "SUCCESS"
    assert data["author_name"] == "gitlab-user"
    assert data["author_email"] == "gitlab-handle@users.noreply.gitlab.local"


def test_git_branch_create_checks_out_new_branch():
    data = runtime.git_branch_create(
        runtime.GitBranchCreateRequest(workspace_id="demo", rag_user="jeremiah", branch_name="feature/runtime-git"),
        "test-secret",
    )
    assert data["status"] == "SUCCESS"
    assert data["branch"] == "feature/runtime-git"
    assert data["current_branch"] == "feature/runtime-git"


def test_system_workspace_blocks_git_write_for_non_admin(runtime_env):
    (runtime_env / "sample.py").write_text("VALUE = 9\n")
    with pytest.raises(HTTPException) as exc:
        runtime.git_add(
            runtime.GitAddRequest(workspace_id="demo_system", rag_user="jeremiah", pathspecs=["sample.py"]),
            "test-secret",
        )
    assert exc.value.status_code == 403


def test_system_workspace_allows_git_write_for_admin():
    data = runtime.git_branch_create(
        runtime.GitBranchCreateRequest(
            workspace_id="demo_system",
            rag_user="admin",
            branch_name="admin/system-maintenance",
        ),
        "test-secret",
    )
    assert data["status"] == "SUCCESS"
    assert data["current_branch"] == "admin/system-maintenance"


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


def test_git_fetch_calls_git_fetch(runtime_env, monkeypatch):
    subprocess.run(["git", "remote", "add", "origin", "https://github.com/user/repo.git"], cwd=runtime_env)
    
    calls = []
    def fake_run_git(workspace_path, args, identity, remote_url, timeout_seconds=60):
        calls.append(args)
        return {"returncode": 0, "stdout": "fetched", "stderr": "", "args": args}
    
    monkeypatch.setattr(runtime, "_run_git_with_optional_askpass", fake_run_git)
    
    data = runtime.git_fetch(
        runtime.GitFetchRequest(workspace_id="demo", rag_user="jeremiah", prune=True),
        "test-secret"
    )
    assert data["status"] == "SUCCESS"
    assert ["git", "fetch", "--prune", "origin"] in calls


def test_git_pull_calls_git_pull(runtime_env, monkeypatch):
    subprocess.run(["git", "remote", "add", "origin", "https://github.com/user/repo.git"], cwd=runtime_env)
    
    calls = []
    def fake_run_git(workspace_path, args, identity, remote_url, timeout_seconds=60):
        calls.append(args)
        return {"returncode": 0, "stdout": "pulled", "stderr": "", "args": args}
    
    monkeypatch.setattr(runtime, "_run_git_with_optional_askpass", fake_run_git)
    
    data = runtime.git_pull(
        runtime.GitPullRequest(workspace_id="demo", rag_user="jeremiah", rebase=True),
        "test-secret"
    )
    assert data["status"] == "SUCCESS"
    # Assuming current branch is main as set in runtime_env fixture
    assert ["git", "pull", "--rebase", "origin", "main"] in calls


def test_git_rebase_calls_git_rebase(runtime_env, monkeypatch):
    calls = []
    def fake_run_command(workspace_path, args, timeout_seconds=30, env_overrides=None):
        calls.append(args)
        return {"returncode": 0, "stdout": "rebased", "stderr": "", "args": args}
    
    monkeypatch.setattr(runtime, "_run_command", fake_run_command)
    
    data = runtime.git_rebase(
        runtime.GitRebaseRequest(workspace_id="demo", rag_user="jeremiah", upstream="origin/main"),
        "test-secret"
    )
    assert data["status"] == "SUCCESS"
    assert ["git", "rebase", "origin/main"] in calls


def test_provider_sync_binary_file_uses_base64(runtime_env, monkeypatch):
    calls = []

    def fake_post(url, json=None, timeout=None):
        calls.append({"url": url, "json": json})
        return SimpleNamespace(
            status_code=200,
            json=lambda: {"status": "SUCCESS", "result": {"path": json["path"], "verified": True}},
            text="",
        )

    monkeypatch.setattr(runtime.httpx, "post", fake_post)
    binary_content = b"\xff\xd8\xff\xe0" # JPEG header
    (runtime_env / "image.jpg").write_bytes(binary_content)

    data = runtime.provider_sync_file(
        runtime.ProviderSyncFileRequest(workspace_id="demo", rag_user="jeremiah", relative_path="image.jpg"),
        "test-secret",
    )
    assert data["status"] == "SUCCESS"
    assert "content_b64" in calls[0]["json"]
    import base64
    assert base64.b64decode(calls[0]["json"]["content_b64"]) == binary_content


def test_create_workspace():
    ws_data = {
        "id": "new_ws",
        "display_name": "New Workspace",
        "local_path": "new_ws",
    }
    data = runtime.create_workspace(Workspace(**ws_data), "test-secret")
    assert data["status"] == "SUCCESS"
    assert data["workspace"].id == "new_ws"


def test_update_workspace():
    data = runtime.update_workspace("demo", {"display_name": "Updated Demo"}, "test-secret")
    assert data["status"] == "SUCCESS"
    assert data["workspace"].display_name == "Updated Demo"


def test_delete_workspace():
    data = runtime.delete_workspace("demo", "test-secret")
    assert data["status"] == "SUCCESS"
    
    # Verify it's gone
    with pytest.raises(HTTPException) as exc:
        runtime.resolve_workspace(runtime.WorkspaceRef(workspace_id="demo"), "test-secret")
    assert exc.value.status_code == 404

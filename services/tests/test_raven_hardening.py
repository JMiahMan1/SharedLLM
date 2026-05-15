import json
import sys
import os
import importlib
from collections import defaultdict

import pytest
from fastapi import HTTPException

# Add execution service to path for absolute imports (from schemas import ...)
_execution_path = os.path.join(os.path.dirname(__file__), '..', 'execution')
if _execution_path not in sys.path:
    sys.path.insert(0, _execution_path)

from services.execution.handlers import git as git_handler
from services.execution.handlers import workspace as workspace_handler
from services.execution.schemas import GitOperationRequest, UserContext, WorkspaceShellRequest
from services.gateway.messaging import InferenceJobQueue, JobStatus
class FakeRedis:
    def __init__(self):
        self.kv = {}
        self.lists = defaultdict(list)

    async def set(self, key, value, ex=None):
        self.kv[key] = value

    async def get(self, key):
        return self.kv.get(key)

    async def rpush(self, key, value):
        self.lists[key].append(value)

    async def lmove(self, src, dst, src_side, dst_side):
        source = self.lists[src]
        if not source:
            return None
        value = source.pop(0 if src_side == "LEFT" else -1)
        if dst_side == "LEFT":
            self.lists[dst].insert(0, value)
        else:
            self.lists[dst].append(value)
        return value

    async def lrem(self, key, count, value):
        removed = 0
        items = []
        for item in self.lists[key]:
            if item == value and (count == 0 or removed < count):
                removed += 1
                continue
            items.append(item)
        self.lists[key] = items
        return removed

    async def delete(self, key):
        self.kv.pop(key, None)

    async def exists(self, key):
        return 1 if key in self.kv else 0

    async def lrange(self, key, start, end):
        items = self.lists[key]
        if end == -1:
            end = len(items) - 1
        return items[start:end + 1]

    async def expire(self, key, ttl):
        return True

    async def close(self):
        return None


@pytest.mark.asyncio
async def test_inference_queue_reclaims_expired_job():
    queue = InferenceJobQueue("redis://fake")
    queue._redis = FakeRedis()

    job_id = await queue.enqueue_job("raven", {"query": "fix something"})
    claimed = await queue.claim_job()
    assert claimed["job_id"] == job_id
    assert claimed["status"] == JobStatus.PROCESSING

    await queue._redis.delete(f"{queue.LEASE_PREFIX}{job_id}")
    reclaimed = await queue.reclaim_expired_jobs()

    assert reclaimed == 1
    status = await queue.get_job_status(job_id)
    assert status["status"] == JobStatus.QUEUED
    assert queue._redis.lists[queue.QUEUE_KEY] == [job_id]


@pytest.mark.asyncio
async def test_inference_queue_dead_letters_after_max_attempts():
    queue = InferenceJobQueue("redis://fake")
    queue._redis = FakeRedis()

    job_id = await queue.enqueue_job("raven", {"query": "fix something"})
    job_raw = json.loads(await queue._redis.get(f"{queue.JOB_PREFIX}{job_id}"))
    job_raw["status"] = JobStatus.PROCESSING
    job_raw["attempts"] = queue.MAX_ATTEMPTS
    await queue._redis.set(f"{queue.JOB_PREFIX}{job_id}", json.dumps(job_raw))
    await queue._redis.rpush(queue.PROCESSING_KEY, job_id)

    reclaimed = await queue.reclaim_expired_jobs()

    assert reclaimed == 0
    status = await queue.get_job_status(job_id)
    assert status["status"] == JobStatus.FAILED
    assert queue._redis.lists[queue.DEAD_LETTER_KEY] == [job_id]


@pytest.mark.asyncio
async def test_logging_service_sanitizes_secrets_and_requires_auth(monkeypatch, tmp_path):
    monkeypatch.setenv("LOGGING_DB_PATH", str(tmp_path / "logs.db"))
    logging_main = importlib.import_module("services.logging.main")
    monkeypatch.setattr(logging_main, "DB_PATH", str(tmp_path / "logs.db"))
    monkeypatch.setattr(logging_main, "INTERNAL_SECRET", "test-secret")
    logging_main.init_db()

    with pytest.raises(HTTPException) as exc_info:
        logging_main._require_internal_secret(None)
    assert exc_info.value.status_code == 403

    entry = logging_main.LogEntry(
        **{
            "service": "gateway",
            "level": "INFO",
            "message": "Authorization: Bearer abc123 github_pat_supersecret",
            "context": {
                "token": "abc123",
                "nested": {"nextcloud_pass": "secret-pass"},
                "details": "keep this",
            },
        }
    )
    response = await logging_main.log_event(entry, "test-secret")
    assert response["status"] == "success"

    rows = await logging_main._fetch_logs(user_id="admin")
    assert rows[0]["message"].count("[REDACTED]") >= 1
    context = json.loads(rows[0]["context"])
    assert context["token"] == "[REDACTED]"
    assert context["nested"]["nextcloud_pass"] == "[REDACTED]"
    assert context["details"] == "keep this"


@pytest.mark.asyncio
async def test_workspace_shell_blocks_mutating_commands():
    req = WorkspaceShellRequest(
        user_context=UserContext(user="raven", is_admin=True),
        command="rm -rf services",
        cwd=".",
        timeout=5,
    )

    result = await workspace_handler.handle_workspace_shell(req)

    assert result.status == "FAILURE"
    assert "blocked" in result.message.lower()


@pytest.mark.asyncio
async def test_workspace_shell_allows_safe_read_only_commands(monkeypatch, tmp_path):
    monkeypatch.setattr(workspace_handler, "WORKSPACE_ROOT", str(tmp_path))
    req = WorkspaceShellRequest(
        user_context=UserContext(user="raven", is_admin=True),
        command="pwd",
        cwd=".",
        timeout=5,
    )

    result = await workspace_handler.handle_workspace_shell(req)

    assert result.status == "SUCCESS"


@pytest.mark.asyncio
async def test_git_handler_allows_autonomous_commit(monkeypatch, tmp_path):
    """After removing the autonomous block, Raven can commit directly."""
    # Use tmp_path workspace, NOT ~/SharedLLM
    monkeypatch.setattr(git_handler, "WORKSPACE_ROOT", str(tmp_path))

    async def fake_branch():
        return "main"

    async def fake_run_git(args, cwd=None, env_override=None):
        # Simulate successful git commit
        return {
            "returncode": 0,
            "stdout": "abc123 commit message",
            "stderr": ""
        }

    monkeypatch.setattr(git_handler, "_get_current_branch", fake_branch)
    monkeypatch.setattr(git_handler, "_run_git", fake_run_git)

    req = GitOperationRequest(
        user_context=UserContext(user="raven", is_admin=True),
        action="commit",
        commit_message="test commit",
    )

    result = await git_handler.handle_git(req)

    assert result.status == "SUCCESS"
    assert "commit" in result.message.lower()


@pytest.mark.asyncio
async def test_git_handler_blocks_reset_for_all_users(monkeypatch, tmp_path):
    # Use tmp_path workspace, NOT ~/SharedLLM
    monkeypatch.setattr(git_handler, "WORKSPACE_ROOT", str(tmp_path))

    async def fake_branch():
        return "main"

    monkeypatch.setattr(git_handler, "_get_current_branch", fake_branch)
    req = GitOperationRequest(
        user_context=UserContext(user="admin", is_admin=True),
        action="reset",
    )

    result = await git_handler.handle_git(req)

    # Handler returns dict for blocked actions
    status = result.status if hasattr(result, 'status') else result.get("status")
    detail = result.detail if hasattr(result, 'detail') else result.get("detail", {})
    assert status == "FAILURE"
    assert detail.get("error") == "unsafe_git_action_blocked"

import json
import importlib
from collections import defaultdict
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

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
    fake_redis = FakeRedis()
    setattr(queue, "_redis", fake_redis)

    job_id = await queue.enqueue_job("raven", {"query": "fix something"})
    claimed = await queue.claim_job()
    assert claimed is not None
    assert claimed["job_id"] == job_id
    assert claimed["status"] == JobStatus.PROCESSING

    await fake_redis.delete(f"{queue.LEASE_PREFIX}{job_id}")
    reclaimed = await queue.reclaim_expired_jobs()

    assert reclaimed == 1
    status = await queue.get_job_status(job_id)
    assert status is not None
    assert status["status"] == JobStatus.QUEUED
    assert fake_redis.lists[queue.QUEUE_KEY] == [job_id]


@pytest.mark.asyncio
async def test_inference_queue_dead_letters_after_max_attempts():
    queue = InferenceJobQueue("redis://fake")
    fake_redis = FakeRedis()
    setattr(queue, "_redis", fake_redis)

    job_id = await queue.enqueue_job("raven", {"query": "fix something"})
    job_data = await fake_redis.get(f"{queue.JOB_PREFIX}{job_id}")
    assert job_data is not None
    job_raw = json.loads(job_data)
    job_raw["status"] = JobStatus.PROCESSING
    job_raw["attempts"] = queue.MAX_ATTEMPTS
    await fake_redis.set(f"{queue.JOB_PREFIX}{job_id}", json.dumps(job_raw))
    await fake_redis.rpush(queue.PROCESSING_KEY, job_id)

    reclaimed = await queue.reclaim_expired_jobs()

    assert reclaimed == 0
    status = await queue.get_job_status(job_id)
    assert status is not None
    assert status["status"] == JobStatus.FAILED
    assert fake_redis.lists[queue.DEAD_LETTER_KEY] == [job_id]


@pytest.mark.asyncio
async def test_logging_service_sanitizes_secrets_and_requires_auth(monkeypatch):
    monkeypatch.setenv("INTERNAL_SECRET", "test-secret")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    logging_main = importlib.import_module("services.logging.main")
    monkeypatch.setattr(logging_main, "INTERNAL_SECRET", "test-secret")

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
    
    mock_redis = AsyncMock()
    mock_redis.pubsub.return_value = AsyncMock()
    
    async def mock_get_redis():
        return mock_redis
    
    monkeypatch.setattr(logging_main, "get_redis", mock_get_redis)
    
    response = await logging_main.log_event(entry, "test-secret")
    assert response["status"] == "success"
    
    # Verify zadd was called with sanitized data
    zadd_call = mock_redis.zadd.call_args
    stored_json = list(zadd_call[0][1].keys())[0]
    stored_data = json.loads(stored_json)
    
    assert "[REDACTED]" in stored_data["message"]
    assert stored_data["context"]["token"] == "[REDACTED]"
    assert stored_data["context"]["nested"]["nextcloud_pass"] == "[REDACTED]"
    assert stored_data["context"]["details"] == "keep this"


@pytest.mark.asyncio
async def test_workspace_shell_blocks_mutating_commands():
    req = WorkspaceShellRequest(
        user_context=UserContext(user="raven", is_admin=True),
        command="sudo reboot",
        commands=None,
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
        commands=None,
        cwd=".",
        timeout=5,
    )

    result = await workspace_handler.handle_workspace_shell(req)

    assert result.status == "SUCCESS"


@pytest.mark.asyncio
async def test_git_handler_allows_autonomous_commit(monkeypatch, tmp_path):
    """After removing the autonomous block, Raven can commit directly."""
    monkeypatch.setattr(git_handler, "WORKSPACE_ROOT", str(tmp_path))

    async def fake_resolve_workspace(*args, **kwargs):
        return str(tmp_path)

    async def fake_branch(cwd=None):
        return "main"

    async def fake_run_git(args, cwd=None, env_override=None):
        return {
            "returncode": 0,
            "stdout": "abc123 commit message",
            "stderr": ""
        }

    monkeypatch.setattr(git_handler, "_resolve_workspace_path", fake_resolve_workspace)
    monkeypatch.setattr(git_handler, "_get_current_branch", fake_branch)
    monkeypatch.setattr(git_handler, "_run_git", fake_run_git)

    req = GitOperationRequest(
        user_context=UserContext(user="raven", is_admin=True),
        workspace_id=None,
        action="commit",
        path=".",
        commit_message="test commit",
        branch="microservices",
        log_count=10,
    )

    result = await git_handler.handle_git(req)

    assert result.status == "SUCCESS"
    assert "commit" in result.message.lower()


@pytest.mark.asyncio
async def test_git_handler_blocks_reset_for_all_users(monkeypatch, tmp_path):
    monkeypatch.setattr(git_handler, "WORKSPACE_ROOT", str(tmp_path))

    async def fake_resolve_workspace(*args, **kwargs):
        return str(tmp_path)

    async def fake_branch(cwd=None):
        return "main"

    monkeypatch.setattr(git_handler, "_resolve_workspace_path", fake_resolve_workspace)
    monkeypatch.setattr(git_handler, "_get_current_branch", fake_branch)
    req = GitOperationRequest(
        user_context=UserContext(user="admin", is_admin=True),
        workspace_id=None,
        action="reset",
        path=".",
        commit_message=None,
        branch="microservices",
        log_count=10,
    )

    result = await git_handler.handle_git(req)

    assert result is not None
    if isinstance(result, dict):
        status = result.get("status")
        detail = result.get("detail", {})
    else:
        status = result.status
        detail = result.detail if result.detail is not None else {}
    assert status == "FAILURE"
    assert detail.get("error") == "unsafe_git_action_blocked"

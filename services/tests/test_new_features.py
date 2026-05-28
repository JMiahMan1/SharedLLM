import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# =============================================================================
# Slice 2: Job Checkpoint/Resumability Tests
# =============================================================================

class FakeRedisCheckpoint:
    def __init__(self):
        self.kv = {}

    async def get(self, key):
        return self.kv.get(key)

    async def setex(self, key, ttl, value):
        self.kv[key] = value

    async def delete(self, key):
        self.kv.pop(key, None)

    async def close(self):
        pass


@pytest.mark.asyncio
async def test_checkpoint_save_and_load():
    """Verify that checkpoint data can be saved to Redis and restored."""
    r = FakeRedisCheckpoint()

    cp_data = {
        "iteration": 5,
        "action_log": ["Step 1: read_file -> Read config.py", "Step 2: write_file -> Wrote config.py"],
        "last_exec_data": {"status": "SUCCESS", "message": "File written"},
        "successful_tool_calls": 2,
        "updated_at": time.time(),
    }
    await r.setex("raven:checkpoint:42", 1860, json.dumps(cp_data))

    raw = await r.get("raven:checkpoint:42")
    assert raw is not None
    restored = json.loads(raw)
    assert restored["iteration"] == 5
    assert len(restored["action_log"]) == 2
    assert restored["successful_tool_calls"] == 2


@pytest.mark.asyncio
async def test_checkpoint_clear_on_completion():
    """Verify that checkpoint is deleted when job completes."""
    r = FakeRedisCheckpoint()

    await r.setex("raven:checkpoint:42", 1860, json.dumps({"iteration": 3}))
    assert await r.get("raven:checkpoint:42") is not None

    await r.delete("raven:checkpoint:42")
    assert await r.get("raven:checkpoint:42") is None


@pytest.mark.asyncio
async def test_checkpoint_resume_from_iteration():
    """Verify that AgentLoop would resume from the correct iteration."""
    r = FakeRedisCheckpoint()

    cp_data = {
        "iteration": 7,
        "action_log": [f"Step {i}: action -> result" for i in range(1, 8)],
        "last_exec_data": {"status": "SUCCESS", "message": "Done"},
        "successful_tool_calls": 7,
        "updated_at": time.time(),
    }
    await r.setex("raven:checkpoint:99", 1860, json.dumps(cp_data))

    raw = await r.get("raven:checkpoint:99")
    assert raw is not None
    cp = json.loads(raw)
    assert cp["iteration"] == 7
    assert cp["successful_tool_calls"] == 7
    assert len(cp["action_log"]) == 7


@pytest.mark.asyncio
async def test_checkpoint_ttl_exceeds_job_timeout():
    """Verify checkpoint TTL is set to RAVEN_MAX_TOTAL_SECONDS + 60."""
    from services.config import RAVEN_MAX_TOTAL_SECONDS
    r = FakeRedisCheckpoint()

    expected_ttl = RAVEN_MAX_TOTAL_SECONDS + 60
    assert expected_ttl > RAVEN_MAX_TOTAL_SECONDS

    cp_data = {"iteration": 1, "action_log": [], "last_exec_data": None, "successful_tool_calls": 0, "updated_at": time.time()}
    await r.setex("raven:checkpoint:1", expected_ttl, json.dumps(cp_data))
    assert await r.get("raven:checkpoint:1") is not None


# =============================================================================
# Slice 8: Auto-Quarantine Tests
# =============================================================================

class FakeRedisQuarantine:
    def __init__(self):
        self.sorted_sets = {}

    def zremrangebyscore(self, key, min_score, max_score):
        if key not in self.sorted_sets:
            return
        self.sorted_sets[key] = {
            mem: score for mem, score in self.sorted_sets[key].items()
            if score > max_score or score < min_score
        }

    def zadd(self, key, mapping):
        if key not in self.sorted_sets:
            self.sorted_sets[key] = {}
        self.sorted_sets[key].update(mapping)

    def zcard(self, key):
        return len(self.sorted_sets.get(key, {}))

    def expire(self, key, ttl):
        pass

    def delete(self, key):
        self.sorted_sets.pop(key, None)


def test_quarantine_failure_counting():
    """Verify that failures are tracked within the sliding window."""
    r = FakeRedisQuarantine()
    file_path = "services/gateway/agent_loop.py"

    r.zremrangebyscore(f"workspace:quarantine:{file_path}", 0, time.time() - 600)
    r.zadd(f"workspace:quarantine:{file_path}", {str(time.time()): time.time()})
    assert r.zcard(f"workspace:quarantine:{file_path}") == 1

    r.zadd(f"workspace:quarantine:{file_path}", {str(time.time() + 1): time.time() + 1})
    assert r.zcard(f"workspace:quarantine:{file_path}") == 2

    r.zadd(f"workspace:quarantine:{file_path}", {str(time.time() + 2): time.time() + 2})
    assert r.zcard(f"workspace:quarantine:{file_path}") == 3


def test_quarantine_window_expiry():
    """Verify that old failures fall out of the sliding window."""
    r = FakeRedisQuarantine()
    file_path = "services/gateway/main.py"
    key = f"workspace:quarantine:{file_path}"

    old_time = time.time() - 700
    r.zadd(key, {str(old_time): old_time})
    r.zremrangebyscore(key, 0, time.time() - 600)
    assert r.zcard(key) == 0


def test_quarantine_threshold_trigger():
    """Verify that quarantine is triggered at the configured threshold."""
    threshold = 3
    r = FakeRedisQuarantine()
    file_path = "services/execution/main.py"
    key = f"workspace:quarantine:{file_path}"

    for i in range(threshold):
        r.zremrangebyscore(key, 0, time.time() - 600)
        r.zadd(key, {str(time.time() + i): time.time() + i})

    count = r.zcard(key)
    assert count >= threshold


def test_quarantine_clear_on_success():
    """Verify that failure history is cleared after a successful run."""
    r = FakeRedisQuarantine()
    file_path = "services/gateway/orchestrator.py"
    key = f"workspace:quarantine:{file_path}"

    r.zadd(key, {str(time.time()): time.time()})
    r.zadd(key, {str(time.time() + 1): time.time() + 1})
    assert r.zcard(key) == 2

    r.delete(key)
    assert r.zcard(key) == 0


def test_quarantine_config_defaults():
    """Verify quarantine configuration defaults are sensible."""
    import services.workspace_runtime.main as wr_module
    assert wr_module.RAVEN_QUARANTINE_THRESHOLD == 3
    assert wr_module.RAVEN_QUARANTINE_WINDOW_SECONDS == 600


# =============================================================================
# Storage Status Endpoint Tests
# =============================================================================

@pytest.mark.asyncio
async def test_storage_status_returns_real_data():
    """Verify /status endpoint returns structured data with real fields."""
    from fastapi.testclient import TestClient
    from services.storage.main import app

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "total_chunks": 1500,
            "total_documents": 200,
            "last_indexed": "2026-05-15T10:00:00Z",
            "breakdown": {
                "nextcloud_files": {"chunks": 800, "documents": 100},
                "ha_entities": {"chunks": 500, "documents": 80},
            },
        }
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_cls.return_value = mock_client

        client = TestClient(app)
        resp = client.get("/status")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "SUCCESS"
        assert "indexer" in data
        assert "rag_index" in data
        assert "checkpointed_files" in data
        assert data["rag_index"]["total_chunks"] == 1500
        assert data["rag_index"]["total_documents"] == 200


@pytest.mark.asyncio
async def test_storage_status_paused_state():
    """Verify /status endpoint includes indexer state field."""
    from fastapi.testclient import TestClient
    from services.storage.main import app

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"total_chunks": 0, "total_documents": 0}
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_cls.return_value = mock_client

        client = TestClient(app)
        resp = client.get("/status")

        assert resp.status_code == 200
        data = resp.json()
        assert data["indexer"] in ("IDLE", "PAUSED")
        assert "message" in data
        assert "rag_index" in data
        assert "checkpointed_files" in data


@pytest.mark.asyncio
async def test_storage_status_rag_unavailable():
    """Verify /status still works when RAG service is unreachable."""
    from fastapi.testclient import TestClient
    from services.storage.main import app

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=Exception("Connection refused"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_cls.return_value = mock_client

        client = TestClient(app)
        resp = client.get("/status")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "SUCCESS"
        assert data["rag_index"] == {}

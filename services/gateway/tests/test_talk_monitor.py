import pytest
import asyncio
import httpx
import respx
import json
from unittest.mock import AsyncMock, MagicMock, patch
try:
    from background_worker import RavenWorker, INTERNAL_SECRET, EXECUTION_SVC
except ImportError:
    from gateway.background_worker import RavenWorker, INTERNAL_SECRET, EXECUTION_SVC

@pytest.fixture
def worker():
    worker = RavenWorker()
    worker.job_queue = MagicMock()
    worker.job_queue.push_job = AsyncMock()
    return worker

@pytest.mark.asyncio
@respx.mock
async def test_talk_monitor_finds_mention(worker):
    # 1. Mock system creds resolution
    respx.post("http://identity:8001/api/resolve").mock(
        return_value=httpx.Response(200, json={
            "user": "default",
            "nextcloud_user": "jarvis_bot",
            "nextcloud_pass": "secret",
            "nextcloud_url": "http://nextcloud"
        })
    )

    # 2. Mock talk list
    respx.post(f"{EXECUTION_SVC}/execute/talk", json={"user_context": {"user": "default", "nextcloud_user": "jarvis_bot", "nextcloud_pass": "secret", "nextcloud_url": "http://nextcloud"}, "action": "list"}).mock(
        return_value=httpx.Response(200, json={"detail": {"conversations": [{"token": "room1"}]}})
    )

    # 3. Mock talk messages (one with @jarvis)
    respx.post(f"{EXECUTION_SVC}/execute/talk", json={"user_context": {"user": "default", "nextcloud_user": "jarvis_bot", "nextcloud_pass": "secret", "nextcloud_url": "http://nextcloud"}, "action": "messages", "token": "room1", "limit": 5}).mock(
        return_value=httpx.Response(200, json={"detail": {"messages": [
            {"id": 1, "message": "Hello world", "actor_id": "user1"},
            {"id": 2, "message": "@jarvis how are you?", "actor_id": "user1"}
        ]}})
    )

    # 4. Mock Redis
    mock_redis = AsyncMock()
    mock_redis.get.return_value = None
    
    # Run the extracted logic once
    await worker._check_talk_once(mock_redis)

    # Verify push_job was called
    worker.job_queue.push_job.assert_called_once()
    args, kwargs = worker.job_queue.push_job.call_args
    assert "how are you?" in kwargs["payload"]["query"]
    assert kwargs["payload"]["_talk_token"] == "room1"

@pytest.mark.asyncio
@respx.mock
async def test_talk_callback(worker):
    # Mock talk send response
    respx.post(f"{EXECUTION_SVC}/execute/talk").mock(
        return_value=httpx.Response(200, json={"status": "SUCCESS"})
    )

    payload = {
        "creds": {"user": "test"},
        "_talk_token": "room1"
    }
    message = "I am fine, thank you!"

    await worker._trigger_talk_callback(payload, message)

    # Verify request was sent
    assert len(respx.calls) == 1
    call = respx.calls[0]
    sent_data = json.loads(call.request.content)
    assert sent_data["action"] == "send"
    assert sent_data["token"] == "room1"
    assert sent_data["message"] == message

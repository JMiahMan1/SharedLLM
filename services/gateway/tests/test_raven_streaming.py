from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from services.gateway.main import app

client = TestClient(app)


def _aio_resp(status=200, json_data=None, text=""):
    """aiohttp-compatible mock response (code does `await resp.json()`/`resp.status`)."""
    m = MagicMock()
    m.status = status
    m.json = AsyncMock(return_value=json_data if json_data is not None else {"status": "SUCCESS"})
    m.text = AsyncMock(return_value=text)
    return m

def test_raven_mission_stream_rejects_non_websocket():
    response = client.get("/api/raven/missions/123/stream")
    assert response.status_code in [400, 426, 404]

@pytest.mark.asyncio
async def test_raven_mission_stream_websocket(monkeypatch):
    """
    Test the websocket endpoint by mocking redis and identity service.
    """
    import json

    class MockPubSub:
        async def subscribe(self, channel):
            self.channel = channel

        async def listen(self):
            yield {"type": "message", "data": json.dumps({"type": "reasoning", "data": "Thinking..."})}
            yield {"type": "message", "data": json.dumps({"type": "action", "data": "Testing tool"})}

        async def unsubscribe(self, channel):
            pass

    class MockRedis:
        def pubsub(self):
            return MockPubSub()

        async def lrange(self, key, start, end):
            return []

        async def close(self):
            pass

    class MockRedisModule:
        @staticmethod
        def from_url(url, **kwargs):
            return MockRedis()

    import redis.asyncio as redis
    monkeypatch.setattr(redis, "from_url", MockRedisModule.from_url)

    mock_resp = _aio_resp(200, {"id": 999, "status": "executing", "output_log": "[]"})

    with patch("aiohttp.ClientSession.get", new=AsyncMock(return_value=mock_resp)), client.websocket_connect("/api/raven/missions/999/stream?token=test-token") as websocket:
            data1 = websocket.receive_text()
            assert "Thinking..." in data1
            data2 = websocket.receive_text()
            assert "Testing tool" in data2


def test_raven_mission_stream_rejects_missing_token():
    """A stream connection without a token must be closed, not silently accepted."""
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect), client.websocket_connect("/api/raven/missions/999/stream"):
        pass

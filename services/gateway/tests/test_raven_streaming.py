import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, AsyncMock, MagicMock
from services.gateway.main import app

client = TestClient(app)

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

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"id": 999, "status": "executing", "output_log": "[]"}
    
    with patch("aiohttp.ClientSession.get", new=AsyncMock(return_value=mock_resp)):
        with client.websocket_connect("/api/raven/missions/999/stream") as websocket:
            data1 = websocket.receive_text()
            assert "Thinking..." in data1
            data2 = websocket.receive_text()
            assert "Testing tool" in data2

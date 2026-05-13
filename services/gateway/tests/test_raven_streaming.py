import pytest
from fastapi.testclient import TestClient
from services.gateway.main import app

client = TestClient(app)

def test_raven_mission_stream_rejects_non_websocket():
    # Attempting to GET a websocket endpoint via standard HTTP should return a 400 or 426
    response = client.get("/api/raven/missions/123/stream")
    # FastAPI usually returns 400 Bad Request if you try to hit a WS endpoint with HTTP GET
    assert response.status_code in [400, 426, 404]

@pytest.mark.asyncio
async def test_raven_mission_stream_websocket(monkeypatch):
    """
    Test the websocket endpoint by mocking redis.
    """
    import json
    
    # We mock redis.from_url to return a mock Redis instance that yields predefined messages.
    class MockPubSub:
        async def subscribe(self, channel):
            self.channel = channel
            
        async def listen(self):
            # Yield a message immediately and then exit
            yield {"type": "message", "data": json.dumps({"type": "reasoning", "data": "Thinking..."})}
            yield {"type": "message", "data": json.dumps({"type": "action", "data": "Testing tool"})}
            
        async def unsubscribe(self, channel):
            pass
            
    class MockRedis:
        def pubsub(self):
            return MockPubSub()
            
        async def close(self):
            pass
            
    # Mock redis module
    class MockRedisModule:
        @staticmethod
        def from_url(url, **kwargs):
            return MockRedis()
            
    import services.gateway.main
    import redis.asyncio as redis
    monkeypatch.setattr(redis, "from_url", MockRedisModule.from_url)

    # Use TestClient websocket context to test stream
    with client.websocket_connect("/api/raven/missions/999/stream") as websocket:
        # The endpoint should push the two messages and then stay open
        data1 = websocket.receive_text()
        assert "Thinking..." in data1
        data2 = websocket.receive_text()
        assert "Testing tool" in data2

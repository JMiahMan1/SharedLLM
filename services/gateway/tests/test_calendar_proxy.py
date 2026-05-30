import pytest
import sys
from fastapi.testclient import TestClient
import os
from unittest.mock import MagicMock

os.environ["INTERNAL_SECRET"] = "test-secret"
os.environ["EXECUTION_SVC"] = "http://execution:8003"

# Mock dependencies before importing main
mock_redis = MagicMock()
sys.modules["redis"] = mock_redis
sys.modules["redis.asyncio"] = mock_redis
sys.modules["fastembed"] = MagicMock()
sys.modules["intent_engine"] = MagicMock()
sys.modules["background_worker"] = MagicMock()

@pytest.fixture(name="client")
def client_fixture(monkeypatch):
    from services.gateway.main import app
    return TestClient(app)

@pytest.mark.asyncio
async def test_calendar_events_proxy_forwards_calendar_name(monkeypatch):
    from services.gateway import main
    
    captured_payload = {}
    
    async def mock_proxy_execution(request, path, payload):
        captured_payload.update(payload)
        return MagicMock(status_code=200, json=lambda: {"status": "SUCCESS"})

    monkeypatch.setattr(main, "_proxy_execution_with_identity", mock_proxy_execution)
    
    # Simulate a request with calendar_name query param
    from fastapi import Request
    mock_request = MagicMock(spec=Request)
    
    await main.proxy_read_calendar(mock_request, calendar_name="Work")
    
    assert captured_payload["action"] == "read"
    assert captured_payload["calendar_name"] == "Work"

@pytest.mark.asyncio
async def test_calendar_events_proxy_no_calendar_name(monkeypatch):
    from services.gateway import main
    
    captured_payload = {}
    
    async def mock_proxy_execution(request, path, payload):
        captured_payload.update(payload)
        return MagicMock(status_code=200, json=lambda: {"status": "SUCCESS"})

    monkeypatch.setattr(main, "_proxy_execution_with_identity", mock_proxy_execution)
    
    from fastapi import Request
    mock_request = MagicMock(spec=Request)
    
    await main.proxy_read_calendar(mock_request, calendar_name="")
    
    assert captured_payload["action"] == "read"
    assert "calendar_name" not in captured_payload

import sys
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient


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


@pytest.mark.asyncio
async def test_calendar_events_proxy_forwards_the_window_on_screen(monkeypatch):
    """A month grid or agenda asks for its own dates; without them a read is the next week."""
    from services.gateway import main

    captured_payload = {}

    async def mock_proxy_execution(request, path, payload):
        captured_payload.update(payload)
        return MagicMock(status_code=200)

    monkeypatch.setattr(main, "_proxy_execution_with_identity", mock_proxy_execution)
    from fastapi import Request

    await main.proxy_read_calendar(MagicMock(spec=Request), start="2026-10-01", end="2026-11-12")
    assert captured_payload["range_start"] == "2026-10-01"
    assert captured_payload["range_end"] == "2026-11-12"

    captured_payload.clear()
    await main.proxy_read_calendar(MagicMock(spec=Request))
    assert "range_start" not in captured_payload

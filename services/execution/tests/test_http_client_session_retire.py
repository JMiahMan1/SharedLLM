"""A pooled session that ages out is retired, not closed under running requests.

Closing it at once failed every request still using it with "Connector is
closed" (seen on Talk calls in production right as the Notes page loaded).
"""
from services.execution import http_client


async def test_an_aged_session_keeps_serving_its_running_requests(monkeypatch):
    host = "https://retire.example"
    first = await http_client.get_session(host)
    monkeypatch.setattr(http_client, "_CACHE_MAX_AGE", -1)

    second = await http_client.get_session(host)

    try:
        assert second is not first
        assert not first.closed, "requests still running on the old session must finish"
        assert first in http_client._RETIRING
    finally:
        await http_client.close_all_sessions()

    assert first.closed and second.closed
    assert not http_client._RETIRING

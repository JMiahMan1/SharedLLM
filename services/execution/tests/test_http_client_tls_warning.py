"""A verify=False request is still verified (without MEDIA_ALLOW_INSECURE_TLS),
and the warning about it is logged once per host, not on every request."""
import logging

import pytest

from services.execution import http_client


@pytest.mark.asyncio
async def test_the_insecure_request_warning_is_logged_once_per_host(monkeypatch, caplog):
    monkeypatch.setattr(http_client, "_TLS_WARNED", set())
    monkeypatch.setattr(http_client, "_insecure_tls_allowed", lambda: False)
    monkeypatch.setattr(http_client, "_SESSION_CACHE", {})
    with caplog.at_level(logging.WARNING):
        for _ in range(5):
            await http_client.get_session("cloud.example", verify=False)
        await http_client.get_session("other.example", verify=False)
    warned = [r.getMessage() for r in caplog.records if "verifying anyway" in r.getMessage()]
    assert warned == [
        "TLS verification turned OFF for cloud.example but MEDIA_ALLOW_INSECURE_TLS is not enabled; verifying anyway",
        "TLS verification turned OFF for other.example but MEDIA_ALLOW_INSECURE_TLS is not enabled; verifying anyway",
    ]
    for session, _ in http_client._SESSION_CACHE.values():
        await session.close()

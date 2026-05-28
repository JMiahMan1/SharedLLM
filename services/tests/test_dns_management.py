import pytest
from unittest.mock import AsyncMock, patch
import json


@pytest.mark.server_only
@pytest.mark.asyncio
async def test_get_dns_config_returns_mappings():
    from services.gateway.main import get_dns_config
    from starlette.requests import Request

    mock_request = Request({
        "type": "http",
        "method": "GET",
        "path": "/api/admin/dns",
        "query_string": b"",
        "headers": [],
    })

    with patch("services.gateway.main.fetch_global_setting", new_callable=AsyncMock) as mock_fetch:
        async def side_effect(key, default=""):
            return {
                "dns_mappings": '{"ollama-server.local": "192.168.4.179"}',
                "dns_upstream": "8.8.8.8,1.1.1.1",
                "dns_poll_interval": "30",
            }.get(key, default)
        mock_fetch.side_effect = side_effect

        result = await get_dns_config(mock_request)

    assert result["dns_mappings"] == {"ollama-server.local": "192.168.4.179"}
    assert result["dns_upstream"] == "8.8.8.8,1.1.1.1"
    assert result["dns_poll_interval"] == 30


@pytest.mark.server_only
@pytest.mark.asyncio
async def test_register_dns_entry_adds_mapping():
    from services.gateway.main import register_dns_entry
    from starlette.requests import Request

    body = json.dumps({"hostname": "new-host", "ip": "192.168.4.179"}).encode()

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    mock_request = Request({
        "type": "http",
        "method": "POST",
        "path": "/api/admin/dns/register",
        "query_string": b"",
        "headers": [(b"content-type", b"application/json")],
    }, receive)

    mock_client = AsyncMock()
    mock_client.patch.return_value = AsyncMock(status_code=200)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("services.gateway.main.fetch_global_setting", new_callable=AsyncMock, return_value='{"existing": "10.0.0.1"}'), \
         patch("services.gateway.main.httpx.AsyncClient", return_value=mock_client):
        result = await register_dns_entry(mock_request)

    assert result["status"] == "SUCCESS"
    patch_call = mock_client.patch.call_args
    patched_value = json.loads(patch_call[1]["json"]["value"])
    assert patched_value["existing"] == "10.0.0.1"
    assert patched_value["new-host"] == "192.168.4.179"


@pytest.mark.server_only
@pytest.mark.asyncio
async def test_remove_dns_entry_deletes_mapping():
    from services.gateway.main import remove_dns_entry
    from starlette.requests import Request

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    mock_request = Request({
        "type": "http",
        "method": "DELETE",
        "path": "/api/admin/dns/old-host",
        "query_string": b"",
        "headers": [],
        "receive": receive,
    })

    mock_client = AsyncMock()
    mock_client.patch.return_value = AsyncMock(status_code=200)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("services.gateway.main.fetch_global_setting", new_callable=AsyncMock, return_value='{"old-host": "10.0.0.5", "keep": "10.0.0.6"}'), \
         patch("services.gateway.main.httpx.AsyncClient", return_value=mock_client):
        result = await remove_dns_entry("old-host", mock_request)

    assert result["status"] == "SUCCESS"
    patch_call = mock_client.patch.call_args
    patched_value = json.loads(patch_call[1]["json"]["value"])
    assert "old-host" not in patched_value
    assert patched_value["keep"] == "10.0.0.6"

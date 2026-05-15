import pytest
from unittest.mock import AsyncMock, patch
import json


@pytest.mark.server_only
@pytest.mark.asyncio
async def test_get_dns_config_returns_mappings():
    from services.gateway.main import get_dns_config
    from starlette.requests import Request

    mock_client = AsyncMock()
    mock_client.get.return_value = AsyncMock(
        json=lambda: [
            {"key": "dns_mappings", "value": '{"ollama-server": "192.168.4.179"}'},
            {"key": "dns_upstream", "value": "8.8.8.8,1.1.1.1"},
            {"key": "dns_poll_interval", "value": "30"},
        ]
    )
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    mock_request = Request({
        "type": "http",
        "method": "GET",
        "path": "/api/admin/dns",
        "query_string": b"",
        "headers": [],
    })

    with patch("services.gateway.main._resolve_identity_from_request", return_value={"user": "admin", "is_admin": True}), \
         patch("services.gateway.main.borrow_http_client", return_value=mock_client):
        result = await get_dns_config(mock_request)

    assert result["dns_mappings"] == {"ollama-server": "192.168.4.179"}
    assert result["dns_upstream"] == "8.8.8.8,1.1.1.1"
    assert result["dns_poll_interval"] == 30


@pytest.mark.server_only
@pytest.mark.asyncio
async def test_register_dns_entry_adds_mapping():
    from services.gateway.main import register_dns_entry
    from starlette.requests import Request

    mock_client = AsyncMock()
    mock_client.get.return_value = AsyncMock(
        json=lambda: {"key": "dns_mappings", "value": '{"existing": "10.0.0.1"}'}
    )
    mock_client.patch.return_value = AsyncMock(status_code=200)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

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

    with patch("services.gateway.main.borrow_http_client", return_value=mock_client):
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

    mock_client = AsyncMock()
    mock_client.get.return_value = AsyncMock(
        json=lambda: {"key": "dns_mappings", "value": '{"old-host": "10.0.0.5", "keep": "10.0.0.6"}'}
    )
    mock_client.patch.return_value = AsyncMock(status_code=200)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

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

    with patch("services.gateway.main._resolve_identity_from_request", return_value={"user": "admin", "is_admin": True}), \
         patch("services.gateway.main.borrow_http_client", return_value=mock_client):
        result = await remove_dns_entry("old-host", mock_request)

    assert result["status"] == "SUCCESS"
    patch_call = mock_client.patch.call_args
    patched_value = json.loads(patch_call[1]["json"]["value"])
    assert "old-host" not in patched_value
    assert patched_value["keep"] == "10.0.0.6"

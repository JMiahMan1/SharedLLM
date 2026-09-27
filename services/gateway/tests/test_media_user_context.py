"""BUG-01: _resolve_user_context must not trust client-supplied user_context.

A forged user_context in the request body must never reach the execution
service: the gateway resolves identity only from the authenticated request.
"""
import pytest
from yarl import URL

from conftest_media import mock_upstream

EXEC_STATUS_URL = "http://host.docker.internal:8003/execute/media/status"


@pytest.mark.asyncio
async def test_forged_user_context_is_ignored(client, upstream):
    mock_upstream(
        upstream,
        "POST",
        EXEC_STATUS_URL,
        payload={"status": "SUCCESS", "detail": {"active": None}},
    )

    resp = client.post(
        "/execute/media/status",
        json={
            "user_context": {
                "user": "attacker",
                "is_admin": True,
                "ha_url": "http://evil.example.com",
                "ha_token": "forged-token",
            }
        },
    )

    assert resp.status_code == 200

    key = ("POST", URL(EXEC_STATUS_URL))
    assert key in upstream.requests
    forwarded = upstream.requests[key][0].kwargs["json"]
    forwarded_ctx = forwarded["user_context"]
    assert forwarded_ctx["user"] == "testuser"
    assert forwarded_ctx["ha_url"] == "http://ha.local:8123"
    assert "forged-token" not in str(forwarded_ctx.values())
    assert "attacker" not in str(forwarded.values())

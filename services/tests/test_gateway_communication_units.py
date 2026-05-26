import os
import json
from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request

os.environ.setdefault("INTERNAL_SECRET", "test-secret")
os.environ.setdefault("OLLAMA_URL", "http://ollama")
os.environ.setdefault("IDENTITY_SVC_URL", "http://identity")
os.environ.setdefault("EXECUTION_SVC_URL", "http://execution")
os.environ.setdefault("RAG_SVC_URL", "http://rag")
os.environ.setdefault("STORAGE_SVC_URL", "http://storage")
os.environ.setdefault("LOGGING_SVC_URL", "http://logging")
os.environ.setdefault("WORKSPACE_RUNTIME_SVC_URL", "http://workspace_runtime")

import gateway.main as gateway_main


def _request_with_auth() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/communication/talk/messages",
            "query_string": b"",
            "headers": [(b"authorization", b"Bearer test-token")],
        }
    )


def test_auth_body_merges_bearer_token():
    request = _request_with_auth()

    merged = gateway_main._auth_body_from_request(request, {"token": "room-alpha"})

    assert merged["token"] == "room-alpha"
    assert merged["api_key"] == "test-token"


@pytest.mark.asyncio
async def test_proxy_execution_with_identity_posts_talk_payload(mocker):
    request = _request_with_auth()
    mocker.patch(
        "gateway.main._resolve_identity_from_request",
        new_callable=AsyncMock,
        return_value={
            "user": "default",
            "is_admin": True,
            "nextcloud_url": "https://cloud.example.com",
            "nextcloud_user": "default",
            "nextcloud_pass": "secret",
        },
    )
    execution_post = mocker.patch("httpx.AsyncClient.post", new_callable=AsyncMock)
    execution_response = mocker.Mock()
    execution_response.status_code = 200
    execution_response.json.return_value = {
        "status": "SUCCESS",
        "message": "Chat message sent.",
        "service": "talk_send",
    }
    execution_post.return_value = execution_response

    response = await gateway_main._proxy_execution_with_identity(
        request,
        "/execute/talk",
        {"action": "send", "token": "room-alpha", "message": "hello world"},
    )
    payload = json.loads(response.body if isinstance(response.body, bytes) else response.body.tobytes())

    assert response.status_code == 200
    assert payload["service"] == "talk_send"
    execution_post.assert_awaited_once()
    _, kwargs = execution_post.await_args
    assert kwargs["json"]["action"] == "send"
    assert kwargs["json"]["token"] == "room-alpha"
    assert kwargs["json"]["message"] == "hello world"
    assert kwargs["json"]["user_context"]["nextcloud_user"] == "default"

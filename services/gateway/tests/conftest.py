"""Gateway test suite conftest.

Media fixtures live in conftest_media.py (named per the media overhaul plan);
re-export them here so pytest auto-discovers them.
"""
import types

import pytest
from conftest_media import client, fake_identity, upstream  # noqa: F401

WORKSPACE_CALLER = {"user": "alice", "is_admin": False}


@pytest.fixture
def runtime_upstream(monkeypatch):
    """Fake workspace runtime: records the gateway's call; the caller is WORKSPACE_CALLER."""
    from services.gateway import main as gateway_main

    captured: dict = {"caller": WORKSPACE_CALLER}

    class _Client:
        async def post(self, url, **kwargs):
            # The gateway also ships request logs through this client; only
            # record the workspace runtime call under test.
            if not url.startswith(gateway_main.WORKSPACE_RUNTIME_SVC):
                return types.SimpleNamespace(status=200, _json={})
            captured["url"] = url
            captured["headers"] = kwargs.get("headers") or {}
            captured["json"] = kwargs.get("json")
            data = kwargs.get("data")
            if data is not None:
                body = b""
                async for chunk in data:
                    body += chunk
                captured["body"] = body
            async def _read():
                return b"{}"

            return types.SimpleNamespace(
                status=200, _json={"status": "SUCCESS"}, read=_read, headers={"content-type": "application/json"}
            )

        async def request(self, method, url, **kwargs):
            captured["method"] = method
            return await self.post(url, **kwargs)

    async def _resolve(request, body):
        body.pop("user_context", None)
        return dict(WORKSPACE_CALLER)

    async def _proxy_json_response(resp):
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=resp.status, content=resp._json)

    monkeypatch.setattr(gateway_main, "get_http_client", lambda: _Client())
    monkeypatch.setattr(gateway_main, "_resolve_user_context", _resolve)
    monkeypatch.setattr(gateway_main, "_proxy_json_response", _proxy_json_response)
    return captured

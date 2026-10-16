"""The strict API-key check sends the key in a header, never the URL: URLs are
written to identity's access log, and this check runs on most requests."""
import pytest

from services.gateway import main


class _Resp:
    status = 200

    async def json(self):
        return {"user": "jeremiah", "user_id": 2, "is_admin": True}


@pytest.mark.asyncio
async def test_the_key_travels_in_a_header(monkeypatch):
    seen = {}

    class _Client:
        async def get(self, url, **kw):
            seen.update(url=url, **kw)
            return _Resp()

    monkeypatch.setattr(main, "get_http_client", lambda: _Client())
    ident = await main._resolve_strict_identity("sk-abc123")
    assert ident and ident.get("user") == "jeremiah"
    assert "params" not in seen and "sk-abc123" not in seen["url"]
    assert seen["headers"]["X-Api-Key"] == "sk-abc123"

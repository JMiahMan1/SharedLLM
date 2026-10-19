import pytest
from starlette.requests import Request

from services.gateway import main as gateway_main


def _request() -> Request:
    return Request({
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [],
        "query_string": b"",
    })


@pytest.mark.asyncio
async def test_direct_resolution_without_an_identity_is_anonymous(monkeypatch):
    """The gateway's own resolver must not ask Identity for a system default."""
    seen = {}

    async def fake_cache(payload, _loader):
        seen.update(payload)
        return {"user": "anonymous", "is_admin": False}

    monkeypatch.setattr("services.gateway.cache.get_cached_identity", fake_cache)

    result = await gateway_main.resolve_identity({})

    assert seen == {"anonymous": True}
    assert result["is_admin"] is False


@pytest.mark.asyncio
async def test_a_named_device_is_left_for_identity_to_resolve(monkeypatch):
    """A device id may resolve to its user or to the shared household."""
    seen = {}

    async def fake_cache(payload, _loader):
        seen.update(payload)
        return {"user": "household", "is_admin": False}

    monkeypatch.setattr("services.gateway.cache.get_cached_identity", fake_cache)

    await gateway_main.resolve_identity({"device_id": "media_player.kitchen"})

    assert seen == {"device_id": "media_player.kitchen"}


@pytest.mark.asyncio
async def test_anonymous_identity_defaults_to_non_admin(monkeypatch):
    seen = {}

    async def fake_resolve(payload):
        seen.update(payload)
        return {"user": "anonymous", "is_admin": False}

    monkeypatch.setattr(gateway_main, "resolve_identity", fake_resolve)

    result = await gateway_main._resolve_identity_allow_anonymous(_request())

    assert seen["anonymous"] is True
    assert result["is_admin"] is False


@pytest.mark.asyncio
async def test_internal_household_identity_is_explicit_and_non_admin(monkeypatch):
    seen = {}

    async def fake_resolve(payload):
        seen.update(payload)
        return {"user": "household", "is_admin": False}

    monkeypatch.setattr(gateway_main, "resolve_identity", fake_resolve)

    result = await gateway_main._resolve_identity_allow_anonymous(
        _request(), household=True
    )

    assert seen["household"] is True
    assert "anonymous" not in seen
    assert result["is_admin"] is False


@pytest.mark.asyncio
async def test_internal_secret_without_a_user_resolves_as_household(monkeypatch):
    seen = {}

    async def fake_resolve(payload):
        seen.update(payload)
        return {"user": "household", "is_admin": False}

    monkeypatch.setattr(gateway_main, "resolve_identity", fake_resolve)
    monkeypatch.setattr(gateway_main, "INTERNAL_SECRET", "service-secret")
    request = Request({
        "type": "http",
        "method": "GET",
        "path": "/api/entities",
        "headers": [(b"x-internal-secret", b"service-secret")],
        "query_string": b"",
    })

    result = await gateway_main._require_identity_from_request(request)

    assert seen["household"] is True
    assert "anonymous" not in seen
    assert result == {"user": "household", "is_admin": False}


@pytest.mark.asyncio
async def test_a_validated_key_cannot_be_overridden_by_body_or_query_ids(monkeypatch):
    seen = {}

    async def fake_resolve(payload):
        seen.update(payload)
        return {"user": "alice", "is_admin": False}

    monkeypatch.setattr(gateway_main, "resolve_identity", fake_resolve)
    request = Request({
        "type": "http",
        "method": "POST",
        "path": "/",
        "headers": [(b"authorization", b"Bearer alice-key")],
        "query_string": b"user_id=jeremiah",
    })

    await gateway_main._resolve_identity_allow_anonymous(
        request, {"user_id": 1, "rag_user": "jeremiah", "voice_id": "jeremiah"}
    )

    assert seen == {"api_key": "alice-key"}

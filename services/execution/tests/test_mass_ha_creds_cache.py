"""MA/HA credential resolution is cached per user so widget bursts hit upstream once."""
import asyncio

import pytest

import services.execution.main as exec_main
from services.execution.main import ServiceNotConfiguredError, _resolve_mass_ha_creds


@pytest.fixture(autouse=True)
def _fresh_creds_cache():
    exec_main._clear_mass_ha_creds_cache()
    yield
    exec_main._clear_mass_ha_creds_cache()


def _stub_resolver(monkeypatch, creds, calls):
    async def fake(user_id=None, rag_user=None):
        calls["resolve"] += 1
        return dict(creds) if creds is not None else None

    monkeypatch.setattr(exec_main, "resolve_internal_user", fake)


def _stub_entry_lookup(monkeypatch, calls):
    import services.execution.ha_client as ha_client

    async def fake(ha_url, ha_token):
        calls["entry"] += 1
        return "mass-entry-1"

    monkeypatch.setattr(ha_client, "find_mass_config_entry", fake)


def test_second_call_reuses_the_cached_credentials(monkeypatch):
    calls = {"resolve": 0, "entry": 0}
    _stub_resolver(
        monkeypatch,
        {"user": "cacheuser", "ha_url": "http://ha.cacheuser", "ha_token": "tok"},
        calls,
    )
    _stub_entry_lookup(monkeypatch, calls)

    first = asyncio.run(_resolve_mass_ha_creds("cacheuser"))
    second = asyncio.run(_resolve_mass_ha_creds("cacheuser"))

    assert calls == {"resolve": 1, "entry": 1}
    assert first["mass_config_entry_id"] == "mass-entry-1"
    assert second == first
    assert second is not first


def test_cache_expires_after_the_ttl(monkeypatch):
    calls = {"resolve": 0, "entry": 0}
    _stub_resolver(
        monkeypatch,
        {"user": "cacheuser", "mass_url": "http://ma", "mass_token": "t"},
        calls,
    )
    monkeypatch.setattr(exec_main, "_MASS_HA_CREDS_TTL_SECONDS", 0.0)

    asyncio.run(_resolve_mass_ha_creds("cacheuser"))
    asyncio.run(_resolve_mass_ha_creds("cacheuser"))

    assert calls["resolve"] == 2
    assert calls["entry"] == 0


def test_concurrent_calls_share_one_resolution(monkeypatch):
    calls = {"resolve": 0, "entry": 0}
    _stub_resolver(
        monkeypatch,
        {"user": "cacheuser", "mass_url": "http://ma", "mass_token": "t"},
        calls,
    )

    async def two_calls():
        return await asyncio.gather(
            _resolve_mass_ha_creds("cacheuser"),
            _resolve_mass_ha_creds("cacheuser"),
        )

    first, second = asyncio.run(two_calls())

    assert calls["resolve"] == 1
    assert first == second


def test_named_user_without_credentials_is_not_cached(monkeypatch):
    calls = {"resolve": 0, "entry": 0}
    _stub_resolver(monkeypatch, None, calls)

    for _ in range(2):
        with pytest.raises(ServiceNotConfiguredError):
            asyncio.run(_resolve_mass_ha_creds("cacheuser"))

    assert calls["resolve"] == 2


def test_missing_user_fails_before_any_upstream_call(monkeypatch):
    calls = {"resolve": 0, "entry": 0}
    _stub_resolver(monkeypatch, {"user": "cacheuser"}, calls)

    with pytest.raises(ServiceNotConfiguredError):
        asyncio.run(_resolve_mass_ha_creds(""))

    assert calls == {"resolve": 0, "entry": 0}

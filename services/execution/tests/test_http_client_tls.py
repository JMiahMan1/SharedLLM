# services/execution/tests/test_http_client_tls.py
"""BUG-09: TLS verification defaults ON; MEDIA_ALLOW_INSECURE_TLS gates opt-out.

Plan row (docs/MEDIA_OVERHAUL.md BUG-09): default verify_ssl=True; no
per-user allow_insecure_tls identity flag exists, so env
MEDIA_ALLOW_INSECURE_TLS=false (opt-out) is the gate. Unit test on the
session factory (http_client.get_session).
"""
import inspect
import logging
import ssl as ssl_mod

import pytest

from services.execution import http_client


@pytest.fixture(autouse=True)
async def _isolated_session_cache():
    """Isolate the module-global session cache around every test."""

    async def _drain():
        for key in list(http_client._SESSION_CACHE):
            session, _ = http_client._SESSION_CACHE.pop(key)
            try:
                await session.close()
            except Exception:
                pass

    await _drain()
    yield
    await _drain()


async def test_get_session_verifies_tls_by_default(caplog):
    with caplog.at_level(logging.WARNING, logger="execution.http"):
        session = await http_client.get_session("https://default.example")

    assert isinstance(session.connector._ssl, ssl_mod.SSLContext)
    assert not [r for r in caplog.records if "MEDIA_ALLOW_INSECURE_TLS" in r.getMessage()]


async def test_verify_false_without_env_still_verifies_and_warns(caplog):
    with caplog.at_level(logging.WARNING, logger="execution.http"):
        session = await http_client.get_session("https://noenv.example", verify=False)

    assert isinstance(session.connector._ssl, ssl_mod.SSLContext)
    assert any("MEDIA_ALLOW_INSECURE_TLS" in r.getMessage() for r in caplog.records)


async def test_verify_false_with_env_disables_verification(monkeypatch, caplog):
    monkeypatch.setenv("MEDIA_ALLOW_INSECURE_TLS", "true")
    with caplog.at_level(logging.WARNING, logger="execution.http"):
        session = await http_client.get_session("https://insecure.example", verify=False)

    assert session.connector._ssl is False
    assert any("DISABLED" in r.getMessage() for r in caplog.records)


async def test_session_cache_keyed_by_effective_verification(monkeypatch):
    monkeypatch.setenv("MEDIA_ALLOW_INSECURE_TLS", "true")
    host = "https://cached.example"

    verified = await http_client.get_session(host)
    unverified = await http_client.get_session(host, verify=False)

    assert verified is not unverified
    assert await http_client.get_session(host, verify=False) is unverified
    assert await http_client.get_session(host) is verified


async def test_verify_false_without_env_shares_verified_session():
    host = "https://forced.example"

    a = await http_client.get_session(host, verify=False)
    b = await http_client.get_session(host)

    assert a is b


async def test_factory_and_request_defaults_are_secure():
    assert inspect.signature(http_client.get_session).parameters["verify"].default is True
    assert inspect.signature(http_client.request).parameters["verify"].default is True

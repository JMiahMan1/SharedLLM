"""Telemetry reads the app checks' accounts from the hub's integrations.

Every key here is made up.
"""
import logging

import pytest

from services.telemetry import app_catalog

KEY = "made-up-abuse-ch-key"


@pytest.fixture
def hub(monkeypatch):
    answers: dict[str, object] = {}
    calls: list[str] = []

    async def _get(_session, domain):
        calls.append(domain)
        answer = answers.get(domain, {"configured": False, "data": {}})
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(app_catalog, "hub_integration", _get)
    monkeypatch.setattr(app_catalog, "_settings_cache", None)
    monkeypatch.setattr(app_catalog, "_hub_down", False)
    return {"answers": answers, "calls": calls}


def _expire(_hub):
    """The minute is up (the fixture restores the cache afterwards)."""
    app_catalog._settings_cache = None


async def test_both_set_up(hub):
    hub["answers"]["malwarebazaar"] = {"configured": True, "data": {"auth_key": f" {KEY} "}}
    hub["answers"]["common_sense_media"] = {"configured": True, "data": {}}
    found = await app_catalog.check_settings(None)
    assert found == {"malwarebazaar_key": KEY, "common_sense": True}
    assert app_catalog.common_sense_enabled(found) is True


async def test_neither_set_up(hub):
    found = await app_catalog.check_settings(None)
    assert found == {"malwarebazaar_key": "", "common_sense": False}
    assert app_catalog.common_sense_enabled(found) is False


async def test_read_per_batch_with_a_short_cache(hub):
    await app_catalog.check_settings(None)
    await app_catalog.check_settings(None)
    assert len(hub["calls"]) == 2  # one read of each, then the cache
    hub["answers"]["common_sense_media"] = {"configured": True, "data": {}}
    _expire(hub)
    assert (await app_catalog.check_settings(None))["common_sense"] is True  # a change shows within a minute


async def test_hub_down_is_not_set_up_and_logged_once(hub, caplog):
    hub["answers"]["malwarebazaar"] = ConnectionError("refused")
    caplog.set_level(logging.INFO, logger="telemetry.app_catalog")
    for _ in range(3):
        found = await app_catalog.check_settings(None)
        assert found["malwarebazaar_key"] == "" and found["common_sense"] is False
        _expire(hub)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and "homehub" in warnings[0].getMessage()
    hub["answers"]["malwarebazaar"] = {"configured": True, "data": {"auth_key": KEY}}
    assert (await app_catalog.check_settings(None))["malwarebazaar_key"] == KEY
    assert any("answers again" in r.getMessage() for r in caplog.records)
    assert KEY not in caplog.text


async def test_the_hub_is_asked_with_the_internal_secret(monkeypatch):
    seen = {}

    class Resp:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def json(self, content_type=None):
            return {"configured": True, "data": {"auth_key": KEY}}

    class Session:
        def get(self, url, headers=None, timeout=None):
            seen.update(url=url, headers=headers)
            return Resp()

    monkeypatch.setattr(app_catalog, "HOMEHUB_URL", "http://hub.invalid:8123")
    monkeypatch.setattr(app_catalog, "INTERNAL_SECRET", "made-up-internal-secret")
    out = await app_catalog.hub_integration(Session(), "malwarebazaar")
    assert out["data"]["auth_key"] == KEY
    assert seen["url"] == "http://hub.invalid:8123/hub/integrations/malwarebazaar"
    assert seen["headers"] == {"Authorization": "Bearer made-up-internal-secret"}

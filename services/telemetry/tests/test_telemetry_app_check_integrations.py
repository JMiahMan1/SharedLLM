"""Where telemetry's app checks get their settings.

Common Sense Media is the hub's integration; the abuse.ch Auth-Key comes from
identity's resolve (a person's own, else the household default). Every key
here is made up.
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


async def test_common_sense_set_up(hub):
    hub["answers"]["common_sense_media"] = {"configured": True, "data": {}}
    found = await app_catalog.check_settings(None)
    assert found == {"common_sense": True}
    assert app_catalog.common_sense_enabled(found) is True
    assert hub["calls"] == ["common_sense_media"]  # the hub is not asked about abuse.ch any more


async def test_common_sense_not_set_up(hub):
    found = await app_catalog.check_settings(None)
    assert found == {"common_sense": False}
    assert app_catalog.common_sense_enabled(found) is False


async def test_read_per_batch_with_a_short_cache(hub):
    await app_catalog.check_settings(None)
    await app_catalog.check_settings(None)
    assert len(hub["calls"]) == 1
    hub["answers"]["common_sense_media"] = {"configured": True, "data": {}}
    _expire(hub)
    assert (await app_catalog.check_settings(None))["common_sense"] is True  # a change shows within a minute


async def test_hub_down_is_not_set_up_and_logged_once(hub, caplog):
    hub["answers"]["common_sense_media"] = ConnectionError("refused")
    caplog.set_level(logging.INFO, logger="telemetry.app_catalog")
    for _ in range(3):
        assert (await app_catalog.check_settings(None))["common_sense"] is False
        _expire(hub)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and "homehub" in warnings[0].getMessage()
    hub["answers"]["common_sense_media"] = {"configured": True, "data": {}}
    assert (await app_catalog.check_settings(None))["common_sense"] is True
    assert any("answers again" in r.getMessage() for r in caplog.records)


async def test_the_hub_is_asked_with_the_internal_secret(monkeypatch):
    seen = {}

    class Resp:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def json(self, content_type=None):
            return {"configured": True, "data": {}}

    class Session:
        def get(self, url, headers=None, timeout=None):
            seen.update(url=url, headers=headers)
            return Resp()

    monkeypatch.setattr(app_catalog, "HOMEHUB_URL", "http://hub.invalid:8123")
    monkeypatch.setattr(app_catalog, "INTERNAL_SECRET", "made-up-internal-secret")
    out = await app_catalog.hub_integration(Session(), "common_sense_media")
    assert out["configured"] is True
    assert seen["url"] == "http://hub.invalid:8123/hub/integrations/common_sense_media"
    assert seen["headers"] == {"Authorization": "Bearer made-up-internal-secret"}


# --- the abuse.ch key, from identity ------------------------------------------------


class _Resp:
    def __init__(self, status, body):
        self.status, self._body = status, body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def json(self, content_type=None):
        return self._body


class _Identity:
    """identity's POST /api/resolve: ``people`` maps a username (or "household") to its answer."""

    def __init__(self, people):
        self.people, self.posts = people, []

    def post(self, url, json=None, headers=None, timeout=None):
        self.posts.append((url, json, headers))
        who = json.get("rag_user") or ("household" if json.get("household") else None)
        answer = self.people.get(who)
        if isinstance(answer, Exception):
            raise answer
        if answer is None:
            return _Resp(401, {"detail": "Invalid credentials"})
        return _Resp(200, answer)


@pytest.fixture
def keys(monkeypatch):
    monkeypatch.setattr(app_catalog, "_key_cache", {})
    monkeypatch.setattr(app_catalog, "_identity_down", False)
    monkeypatch.setattr(app_catalog, "IDENTITY_SVC", "http://identity.invalid:8001")
    monkeypatch.setattr(app_catalog, "INTERNAL_SECRET", "made-up-internal-secret")


def _creds(key, source):
    return {"user": "x", "malwarebazaar_key": key, "credential_sources": {"abuse_ch": source}}


async def test_a_persons_key_and_its_source_come_from_resolve(keys):
    identity = _Identity({"kid": _creds(KEY, "household"), "teen": _creds("made-up-own", "own"),
                          "household": _creds(KEY, "household")})
    assert await app_catalog.abuse_ch_key(identity, "kid") == (KEY, "household")
    assert await app_catalog.abuse_ch_key(identity, "teen") == ("made-up-own", "own")
    assert await app_catalog.abuse_ch_key(identity, None) == (KEY, "household")
    url, body, headers = identity.posts[0]
    assert url == "http://identity.invalid:8001/api/resolve" and body == {"rag_user": "kid"}
    assert headers == {"X-Internal-Secret": "made-up-internal-secret"}
    assert identity.posts[2][1] == {"household": True}


async def test_no_key_or_an_unknown_person_is_absent(keys):
    identity = _Identity({"kid": _creds(None, "absent")})
    assert await app_catalog.abuse_ch_key(identity, "kid") == ("", "absent")
    assert await app_catalog.abuse_ch_key(identity, "nobody") == ("", "absent")


async def test_keys_are_cached_a_minute(keys):
    identity = _Identity({"kid": _creds(KEY, "household")})
    for _ in range(3):
        await app_catalog.abuse_ch_key(identity, "kid")
    assert len(identity.posts) == 1
    app_catalog._key_cache.clear()
    identity.people["kid"] = _creds("made-up-own", "own")
    assert await app_catalog.abuse_ch_key(identity, "kid") == ("made-up-own", "own")


async def test_identity_down_raises_and_is_logged_once(keys, caplog):
    identity = _Identity({"kid": ConnectionError("refused")})
    caplog.set_level(logging.INFO, logger="telemetry.app_catalog")
    for _ in range(3):
        with pytest.raises(ConnectionError):
            await app_catalog.abuse_ch_key(identity, "kid")
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and "identity" in warnings[0].getMessage()
    identity.people["kid"] = _creds(KEY, "household")
    assert await app_catalog.abuse_ch_key(identity, "kid") == (KEY, "household")
    assert any("answers again" in r.getMessage() for r in caplog.records)
    assert KEY not in caplog.text

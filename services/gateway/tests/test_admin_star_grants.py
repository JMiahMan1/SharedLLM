"""Admin star grants, and the optional Skylight mirror.

Regressions this covers:

* The mirror must never run when the ledger write failed -- mirroring an award
  that was never recorded would be a lie.
* A Skylight outage must NOT fail the grant. `docs/ACHIEVEMENTS.md`: "Everything
  is recorded in the points ledger first, then mirrored to Skylight, so a
  Skylight outage cannot lose the award." The two outcomes are reported
  separately so the admin can retry the mirror without re-running the grant.
* Admin-only, enforced via `_caller_is_admin` (the strict resolver). Never
  `_resolve_identity_from_request`, which falls back to the default admin.
* The mirror uses the **target's** Skylight credentials, not the admin's.
"""
import json
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient

from services.gateway import main as gateway_main
from services.gateway.main import app


def _patch_upstreams(monkeypatch, ledger_status=200, ledger_body=None,
                     mirror_status=200, mirror_body=None, mirror_raw=None):
    """Capture gateway -> geo/execution calls. Only those two URLs are recorded."""
    calls = []

    class _Resp:
        def __init__(self, status, payload, raw=None):
            self.status = status
            self._payload = payload
            self._raw = raw

        async def json(self):
            if self._raw is not None:
                raise ValueError("Expecting value: line 1 column 1 (char 0)")
            return self._payload

        async def read(self):
            if self._raw is not None:
                return self._raw.encode()
            return json.dumps(self._payload).encode()

    class _Client:
        async def _record(self, verb, url, **kwargs):
            if url.startswith(str(gateway_main.GEO_SVC)):
                calls.append({"svc": "geo", "verb": verb, "url": url, **kwargs})
                return _Resp(ledger_status, ledger_body if ledger_body is not None else
                             {"user_id": "michele", "stars": 2, "balance": 3})
            if url.startswith(str(gateway_main.EXECUTION_SVC)):
                calls.append({"svc": "execution", "verb": verb, "url": url, **kwargs})
                return _Resp(
                    mirror_status,
                    mirror_body if mirror_body is not None else
                    {"status": "SUCCESS", "message": "Granted 2 star(s)"},
                    raw=mirror_raw,
                )
            return _Resp(200, {})

        async def get(self, url, **kw):
            return await self._record("get", url, **kw)

        async def post(self, url, **kw):
            return await self._record("post", url, **kw)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    @asynccontextmanager
    async def fake():
        yield _Client()

    monkeypatch.setattr(gateway_main, "shared_http_client", fake)
    return calls


@pytest.fixture
def admin_client(monkeypatch):
    async def strict(_key):
        return {"user": "jeremiah", "is_admin": True}

    monkeypatch.setattr(gateway_main, "_resolve_strict_identity", strict)
    # _acting_identity returns None without a Bearer token, so every request
    # would be anonymous and hit the 403 for the wrong reason.
    return TestClient(app, headers={"Authorization": "Bearer test-key"})


@pytest.fixture
def user_client(monkeypatch):
    async def strict(_key):
        return {"user": "michele", "is_admin": False}

    monkeypatch.setattr(gateway_main, "_resolve_strict_identity", strict)
    return TestClient(app, headers={"Authorization": "Bearer test-key"})


def _post(client, **body):
    return client.post("/api/admin/users/michele/stars", json=body)


class TestAdminGrantWithMirror:
    def test_a_non_admin_cannot_grant(self, user_client, monkeypatch):
        calls = _patch_upstreams(monkeypatch)
        r = _post(user_client, stars=2, reason="chore")
        assert r.status_code == 403
        assert calls == [], "a refused grant must not reach geo"

    def test_anonymous_cannot_grant(self, monkeypatch):
        calls = _patch_upstreams(monkeypatch)
        r = TestClient(app).post("/api/admin/users/michele/stars", json={"stars": 2, "reason": "chore"})
        assert r.status_code == 403
        assert calls == []

    def test_records_in_the_ledger_then_mirrors(self, admin_client, monkeypatch):
        calls = _patch_upstreams(monkeypatch)
        r = _post(admin_client, stars=2, reason="chore", note="dishes")
        assert r.status_code == 200
        body = r.json()
        assert body["user_id"] == "michele"
        assert body["ledger"]["balance"] == 3
        assert body["skylight"]["status"] == "SUCCESS"
        # Ledger first, mirror second -- the order is the whole point.
        assert [c["svc"] for c in calls] == ["geo", "execution"]

    def test_does_not_mirror_when_the_ledger_refused(self, admin_client, monkeypatch):
        """A refused grant must not be mirrored; that would report a star award
        that was never recorded."""
        calls = _patch_upstreams(
            monkeypatch,
            ledger_status=422,
            ledger_body={"detail": "balance cannot go negative"},
        )
        r = _post(admin_client, stars=-2, reason="chore")
        assert r.status_code == 422
        assert "negative" in r.json()["detail"]
        assert [c["svc"] for c in calls] == ["geo"], "mirror ran despite a refused ledger write"

    def test_a_skylight_outage_does_not_fail_the_grant(self, admin_client, monkeypatch):
        """docs/ACHIEVEMENTS.md: the ledger is authoritative, so a Skylight
        outage cannot lose the award."""
        _patch_upstreams(
            monkeypatch,
            mirror_status=200,
            mirror_body={"status": "FAILURE", "message": "skylight_stars_path is not configured"},
        )
        r = _post(admin_client, stars=2, reason="chore")
        assert r.status_code == 200, "the grant stands even when the mirror fails"
        body = r.json()
        assert body["ledger"]["balance"] == 3
        assert body["skylight"]["status"] == "FAILURE"
        assert "not configured" in body["skylight"]["message"]

    def test_an_unreadable_mirror_response_is_reported_not_raised(self, admin_client, monkeypatch):
        # A gateway error page or an HTML body: json() raises.
        _patch_upstreams(monkeypatch, mirror_status=502, mirror_raw="<html>Bad Gateway</html>")
        r = _post(admin_client, stars=2, reason="chore")
        assert r.status_code == 200
        assert r.json()["skylight"]["status"] == "FAILURE"
        assert "502" in r.json()["skylight"]["message"]

    def test_the_mirror_uses_the_targets_credentials(self, admin_client, monkeypatch):
        """The stars belong to the target, so the mirror must use *their*
        Skylight account rather than the admin's."""
        calls = _patch_upstreams(monkeypatch)
        _post(admin_client, stars=2, reason="chore")
        mirror = [c for c in calls if c["svc"] == "execution"][0]
        assert mirror["json"]["user"] == "michele"
        assert mirror["json"]["member"] == "michele"

    def test_mirroring_can_be_declined(self, admin_client, monkeypatch):
        calls = _patch_upstreams(monkeypatch)
        r = _post(admin_client, stars=2, reason="chore", mirror_to_skylight=False)
        assert r.status_code == 200
        assert r.json()["skylight"]["status"] == "SKIPPED"
        assert [c["svc"] for c in calls] == ["geo"]

    def test_the_target_is_lowercased(self, admin_client, monkeypatch):
        calls = _patch_upstreams(monkeypatch)
        admin_client.post("/api/admin/users/MiChElE/stars", json={"stars": 1, "reason": "chore"})
        assert calls[0]["json"]["user_id"] == "michele"

    def test_a_blank_user_id_is_rejected(self, admin_client, monkeypatch):
        calls = _patch_upstreams(monkeypatch)
        r = admin_client.post("/api/admin/users/%20/stars", json={"stars": 1, "reason": "chore"})
        assert r.status_code in (404, 422)
        assert calls == []

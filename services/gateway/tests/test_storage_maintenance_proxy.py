"""The purge proxy's body shape, and the maintenance route built alongside it.

The proxy used to send the filter dict as the *bare* body with the user on
the query string. RAG's handler reads both from the body, so every scoped
purge from the UI arrived as user ``"default"`` with no filter at all -- a
whole-collection delete of the default user's rows, silently. The contract
is ``{user_id, filter}``, both routes resolve identity strictly (no
fallback user: a delete must never run as somebody else because a key
could not be verified), and the caller's identity always overrides any
``user_id`` in the payload.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from services.gateway import main as gateway_main
from services.gateway.main import app

client = TestClient(app)
AUTH = {"Authorization": "Bearer test-token"}


@pytest.fixture(autouse=True)
def _no_identity_cache(monkeypatch):
    """Bypass resolve_identity's body-keyed cache for this file.

    Both storage test files present the same ``test-token``, so whichever
    file resolved first would have its answer served to the other one's
    tests (user_id leaking across files). Going straight to the factory
    makes each test resolve against its own session and leaves nothing
    behind for anyone else."""
    from services.gateway import cache as gw_cache

    async def _uncached(_body, factory):
        return await factory()

    monkeypatch.setattr(gw_cache, "get_cached_identity", _uncached)


def _aio_resp(status=200, json_data=None):
    m = MagicMock()
    m.status = status
    m.json = AsyncMock(return_value=json_data if json_data is not None else {"status": "SUCCESS"})
    m.text = AsyncMock(return_value="")
    return m


def _session(validate_status=200, validate_payload=None):
    """Mock aiohttp session modelling Identity's strict key validation."""
    async def post_side_effect(url, **kwargs):
        if "resolve" in url and "validate" not in url:
            return _aio_resp(200, {"user": "alice", "user_id": 1, "is_admin": False})
        if "rag/purge" in url:
            return _aio_resp(200, {"status": "SUCCESS", "removed": 1, "recorded": kwargs.get("json")})
        if "rag/maintenance" in url:
            return _aio_resp(200, {"status": "SUCCESS", "mode": "report", "recorded": kwargs.get("json")})
        return _aio_resp(200, {})

    async def get_side_effect(url, **kwargs):
        if "validate-api-key" in url:
            if validate_status != 200:
                return _aio_resp(validate_status, {"detail": "bad key"})
            return _aio_resp(200, validate_payload or {"user": "alice", "user_id": 1, "is_admin": False})
        return _aio_resp(200, {})

    sess = AsyncMock()
    sess.post.side_effect = post_side_effect
    sess.get.side_effect = get_side_effect
    sess.__aenter__.return_value = sess
    sess.__aexit__.return_value = False
    return sess


def _patch(monkeypatch, **kwargs):
    sess = _session(**kwargs)
    monkeypatch.setattr(gateway_main, "get_http_client", lambda: sess)
    return sess


def _purge_upstream(sess):
    calls = [c for c in sess.post.call_args_list if "rag/purge" in str(c[0][0])]
    assert calls, "the purge was never forwarded to RAG"
    return calls[0][1]["json"]


def test_purge_forwards_the_userscope_and_filter_together(monkeypatch):
    sess = _patch(monkeypatch)
    resp = client.post(
        "/api/storage/purge/calibre_files",
        json={"filter": {"path": "/gone.md"}},
        headers=AUTH,
    )
    assert resp.status_code == 200, resp.text
    assert _purge_upstream(sess) == {"user_id": "alice", "filter": {"path": "/gone.md"}}


def test_purge_without_a_filter_sends_an_empty_filter_not_a_missing_one(monkeypatch):
    sess = _patch(monkeypatch)
    resp = client.post("/api/storage/purge/calibre_files", json={}, headers=AUTH)
    assert resp.status_code == 200, resp.text
    assert _purge_upstream(sess) == {"user_id": "alice", "filter": {}}


def test_purge_never_trusts_a_user_id_in_the_payload(monkeypatch):
    """A crafted body must not aim the delete at another family member."""
    sess = _patch(monkeypatch)
    resp = client.post(
        "/api/storage/purge/calibre_files",
        json={"user_id": "victim", "filter": {}},
        headers=AUTH,
    )
    assert resp.status_code == 200, resp.text
    assert _purge_upstream(sess)["user_id"] == "alice"


def test_purge_without_a_verifiable_identity_is_401_not_someone_else(monkeypatch):
    """The old route fell back to the first user; a delete must not."""
    sess = _patch(monkeypatch, validate_status=401)
    resp = client.post("/api/storage/purge/calibre_files", json={"filter": {}}, headers=AUTH)
    assert resp.status_code == 401
    assert not [c for c in sess.post.call_args_list if "rag/purge" in str(c[0][0])], (
        "an unverified caller must not reach RAG"
    )


def test_purge_with_no_key_at_all_is_401(monkeypatch):
    _patch(monkeypatch)
    resp = client.post("/api/storage/purge/calibre_files", json={"filter": {}})
    assert resp.status_code == 401


def test_maintenance_forwards_the_callers_identity_and_defaults_to_report(monkeypatch):
    sess = _patch(monkeypatch)
    resp = client.post("/api/storage/maintenance", json={}, headers=AUTH)
    assert resp.status_code == 200, resp.text
    upstream = [c for c in sess.post.call_args_list if "rag/maintenance" in str(c[0][0])]
    assert upstream, "maintenance was never forwarded to RAG"
    sent = upstream[0][1]["json"]
    assert sent["user_id"] == "alice"
    assert "mode" not in sent, "an absent mode must reach RAG as its report default"


def test_maintenance_forwards_report_and_purge_modes(monkeypatch):
    sess = _patch(monkeypatch)
    resp = client.post(
        "/api/storage/maintenance",
        json={"mode": "purge", "collections": ["telemetry_alerts"], "user_id": "spoofed"},
        headers=AUTH,
    )
    assert resp.status_code == 200, resp.text
    sent = [c for c in sess.post.call_args_list if "rag/maintenance" in str(c[0][0])][0][1]["json"]
    assert sent["user_id"] == "alice", "the payload's user_id must be overridden"
    assert sent["mode"] == "purge"
    assert sent["collections"] == ["telemetry_alerts"]


def test_maintenance_without_a_verifiable_identity_is_401(monkeypatch):
    sess = _patch(monkeypatch, validate_status=401)
    resp = client.post("/api/storage/maintenance", json={"mode": "purge"}, headers=AUTH)
    assert resp.status_code == 401
    assert not [c for c in sess.post.call_args_list if "rag/maintenance" in str(c[0][0])]

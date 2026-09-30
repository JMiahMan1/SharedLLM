"""The image/music/OCR gate: ``_sd_request_authorized`` must verify, not assume.

Regression under test. The gate used to end in ``return bool(api_key)`` — a
*presence* check — so any non-empty string authorized the caller. The obvious
"fix" of validating through ``resolve_identity`` would have been far worse:
that endpoint falls back to the system default user, which is an admin, so
every string resolves successfully (see services/identity/tests/
test_validate_api_key.py). These tests pin the correct behaviour: a key is
checked against Identity's strict endpoint, and uncertainty denies.

``_api_key_is_valid`` is stubbed per-test so this file tests the *gate*
(header handling, ordering, fail-closed) rather than the HTTP call itself.
The strict endpoint is tested in services/identity.
"""
import pytest
from fastapi.testclient import TestClient

from services.gateway import main
from services.gateway.main import app

# One cheap, harmless gated route used to prove the gate itself rejects.
# GET /api/images/models does no identity work beyond the gate, so a 401 here
# can only have come from the gate.
GATED_ROUTE = "/api/images/models"


def _client(**headers) -> TestClient:
    return TestClient(app, headers=headers)


@pytest.fixture
def valid_key(monkeypatch):
    """Identity says the presented key is real."""

    async def _ok(api_key: str) -> bool:
        return bool(api_key and api_key.strip())

    monkeypatch.setattr(main, "_api_key_is_valid", _ok)


@pytest.fixture
def rejected_key(monkeypatch):
    """Identity says the presented key is not real."""

    async def _no(api_key: str) -> bool:
        return False

    monkeypatch.setattr(main, "_api_key_is_valid", _no)


def test_a_junk_bearer_token_is_rejected(rejected_key):
    """The bug: a present-but-worthless key used to be enough."""
    resp = _client(**{"Authorization": "Bearer totally-bogus-key"}).get(GATED_ROUTE)
    assert resp.status_code == 401


def test_a_junk_x_api_key_header_is_rejected(rejected_key):
    resp = _client(**{"X-API-Key": "totally-bogus-key"}).get(GATED_ROUTE)
    assert resp.status_code == 401


def test_no_credentials_at_all_is_rejected(rejected_key):
    resp = _client().get(GATED_ROUTE)
    assert resp.status_code == 401


def test_a_valid_bearer_token_is_allowed(valid_key):
    # 500/502 rather than 401 is fine: the route is allowed through the gate
    # and then fails on its own upstream. What matters is that it is not 401.
    resp = _client(**{"Authorization": "Bearer a-real-key"}).get(GATED_ROUTE)
    assert resp.status_code != 401


def test_a_valid_x_api_key_is_allowed(valid_key):
    resp = _client(**{"X-API-Key": "a-real-key"}).get(GATED_ROUTE)
    assert resp.status_code != 401


def test_a_blank_bearer_token_is_rejected(rejected_key):
    resp = _client(**{"Authorization": "Bearer    "}).get(GATED_ROUTE)
    assert resp.status_code == 401


def test_the_internal_secret_still_authorizes_service_to_service(rejected_key):
    """Service-to-service callers bypass key validation, by design."""
    resp = _client(**{"X-Internal-Secret": main.INTERNAL_SECRET}).get(GATED_ROUTE)
    assert resp.status_code != 401


def test_identity_being_unreachable_fails_closed(monkeypatch):
    """Uncertainty must deny, not wave the request through."""

    class _Boom:
        def get(self, *a, **k):
            raise RuntimeError("identity unreachable")

    monkeypatch.setattr(main, "get_http_client", lambda: _Boom())
    resp = _client(**{"Authorization": "Bearer some-key"}).get(GATED_ROUTE)
    assert resp.status_code == 401


def test_identity_answering_500_fails_closed(monkeypatch):
    class _Resp:
        status = 500

    class _Client:
        def get(self, *a, **k):
            return _Resp()

    monkeypatch.setattr(main, "get_http_client", lambda: _Client())
    resp = _client(**{"Authorization": "Bearer some-key"}).get(GATED_ROUTE)
    assert resp.status_code == 401


@pytest.mark.parametrize(
    "route",
    [
        "/api/images/models",
        "/api/ai/capabilities",
    ],
)
def test_every_gated_get_route_rejects_a_junk_key(rejected_key, route):
    resp = _client(**{"Authorization": "Bearer totally-bogus-key"}).get(route)
    assert resp.status_code == 401

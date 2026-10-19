"""Identity's CORS allowlist.

The service used to answer ``allow_origins=["*"]``. Identity returns decrypted
credentials, so a wildcard lets any site a signed-in browser visits read them.
Only the app origin and the local development servers may call it directly;
service-to-service calls carry no Origin header and are unaffected.
"""
from fastapi.testclient import TestClient

from services.config import IDENTITY_CORS_ORIGINS
from services.identity.main import app

APP_ORIGIN = "https://jarvis.sumemail.com"
DEV_ORIGIN = "http://localhost:5173"
FOREIGN_ORIGIN = "https://evil.example"


def _client() -> TestClient:
    # No context manager: these tests only exercise middleware, not lifespan.
    return TestClient(app)


def test_the_default_allowlist_contains_the_app_and_local_dev_origins():
    assert APP_ORIGIN in IDENTITY_CORS_ORIGINS
    assert DEV_ORIGIN in IDENTITY_CORS_ORIGINS
    assert "*" not in IDENTITY_CORS_ORIGINS


def test_an_allowed_origin_is_echoed_back():
    resp = _client().get("/health", headers={"Origin": APP_ORIGIN})
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == APP_ORIGIN


def test_a_local_dev_origin_is_allowed():
    resp = _client().get("/health", headers={"Origin": DEV_ORIGIN})
    assert resp.headers["access-control-allow-origin"] == DEV_ORIGIN


def test_a_foreign_origin_is_not_allowed():
    resp = _client().get("/health", headers={"Origin": FOREIGN_ORIGIN})
    assert "access-control-allow-origin" not in resp.headers


def test_service_to_service_calls_without_an_origin_are_unaffected():
    """CORS only applies to browsers; internal calls carry no Origin header."""
    resp = _client().get("/health")
    assert resp.status_code == 200
    assert "access-control-allow-origin" not in resp.headers


def test_preflight_from_a_foreign_origin_is_refused():
    resp = _client().options(
        "/api/resolve",
        headers={
            "Origin": FOREIGN_ORIGIN,
            "Access-Control-Request-Method": "POST",
        },
    )
    assert "access-control-allow-origin" not in resp.headers


def test_preflight_from_an_allowed_origin_is_accepted():
    resp = _client().options(
        "/api/resolve",
        headers={
            "Origin": APP_ORIGIN,
            "Access-Control-Request-Method": "POST",
        },
    )
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == APP_ORIGIN

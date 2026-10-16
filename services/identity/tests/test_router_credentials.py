"""The router's login for the network monitor (docs/NETWORK.md): stored on the
system user with the password encrypted, read back only over the internal API,
and never part of /api/resolve."""
from sqlmodel import select

from services.identity.crypto import decrypt
from services.identity.main import app, require_internal
from services.identity.models import User
from services.identity.tests.test_audit_trail import client_fixture, session_fixture  # noqa: F401


def _seed(client, field, value):
    resp = client.post("/api/admin/seed-credential", json={"field": field, "value": value})
    assert resp.status_code == 200, resp.text


def _owner(session):
    session.expire_all()
    return session.exec(select(User).where(User.username == "default")).first()


def test_the_router_login_is_stored_encrypted_and_read_internally(client, session):
    _seed(client, "router_url", "https://192.168.2.1")
    _seed(client, "router_user", "jarvis")
    _seed(client, "router_pass", "r0uter-pw")
    owner = _owner(session)
    assert owner.router_pass_enc and owner.router_pass_enc != "r0uter-pw"
    assert decrypt(owner.router_pass_enc) == "r0uter-pw"

    got = client.get("/api/internal/router").json()
    assert got == {"url": "https://192.168.2.1", "user": "jarvis", "password": "r0uter-pw", "cert_sha256": None}

    resolved = client.post("/api/resolve", json={"username": "default"}).text
    assert "r0uter-pw" not in resolved and "router_" not in resolved


def test_a_new_router_address_drops_the_pinned_certificate(client, session):
    _seed(client, "router_url", "https://192.168.2.1")
    _seed(client, "router_cert_sha256", "ab" * 32)
    _seed(client, "router_url", "https://192.168.2.1")
    assert _owner(session).router_cert_sha256 == "ab" * 32
    _seed(client, "router_url", "https://10.0.0.1")
    assert _owner(session).router_cert_sha256 is None


def test_the_internal_read_refuses_everyone_else(client):
    app.dependency_overrides.pop(require_internal, None)
    assert client.get("/api/internal/router").status_code in (401, 403)

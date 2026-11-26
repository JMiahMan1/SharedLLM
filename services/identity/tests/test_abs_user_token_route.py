"""One member's own Audiobookshelf token, filed by the gateway.

Audiobookshelf mints one token per account and shows it to an administrator
(``POST /api/users/{id}/token`` is a 404 on 2.33), so "give everyone their own
token" is a copy: the household keeps the administrator credential on the
default account, and this route files each account's own token with the member.
The token is a secret, so it is encrypted at rest, audited, and never returned.
"""
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, StaticPool, create_engine, select

import services.identity.main as identity_main
from services.identity.crypto import decrypt, digest_secret
from services.identity.main import app
from services.identity.models import User

SECRET = {"X-Internal-Secret": "test-secret"}
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.payload.signature"


@pytest.fixture(name="session")
def session_fixture():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(User(username="admin", display_name="Admin", is_admin=True))
        session.add(User(username="sam", display_name="Sam"))
        session.commit()
        yield session


@pytest.fixture(name="client")
def client_fixture(session: Session):
    identity_main.engine = session.bind
    yield TestClient(app)
    app.dependency_overrides = {}


def _user(session: Session, username: str) -> User:
    session.expire_all()
    return session.exec(select(User).where(User.username == username)).first()


def test_the_internal_caller_files_a_members_own_token(client, session):
    resp = client.post(
        "/api/users/sam/audiobookshelf-token",
        json={"token": TOKEN, "audiobookshelf_user": "sam"},
        headers=SECRET,
    )

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"username": "sam", "stored": True, "audiobookshelf_user": "sam"}

    sam = _user(session, "sam")
    # Encrypted at rest, and the plaintext is not in the row.
    assert sam.audiobookshelf_api_key_enc != TOKEN
    assert decrypt(sam.audiobookshelf_api_key_enc) == TOKEN
    assert sam.audiobookshelf_user == "sam"


def test_a_blank_token_is_refused_and_changes_nothing(client, session):
    resp = client.post(
        "/api/users/sam/audiobookshelf-token",
        json={"token": "   "},
        headers=SECRET,
    )

    assert resp.status_code == 422
    assert _user(session, "sam").audiobookshelf_api_key_enc is None


def test_an_unknown_member_is_a_404(client, session):
    resp = client.post(
        "/api/users/nobody/audiobookshelf-token",
        json={"token": TOKEN},
        headers=SECRET,
    )

    assert resp.status_code == 404


def test_nobody_without_the_secret_or_an_admin_may_write_it(client, session):
    # No credentials at all.
    assert client.post("/api/users/sam/audiobookshelf-token", json={"token": TOKEN}).status_code == 401
    # The wrong secret is not the internal one.
    assert client.post(
        "/api/users/sam/audiobookshelf-token",
        json={"token": TOKEN},
        headers={"X-Internal-Secret": "wrong"},
    ).status_code == 401
    assert _user(session, "sam").audiobookshelf_api_key_enc is None


def test_the_account_owner_may_file_their_own(client, session):
    sam = _user(session, "sam")
    sam.api_key_hash = digest_secret("sam-key")
    session.add(sam)
    session.commit()

    resp = client.post(
        "/api/users/sam/audiobookshelf-token",
        json={"token": TOKEN},
        headers={"Authorization": "Bearer sam-key"},
    )

    assert resp.status_code == 200, resp.text
    assert decrypt(_user(session, "sam").audiobookshelf_api_key_enc) == TOKEN

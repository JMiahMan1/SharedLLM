"""Linked outside accounts (Spotify): the member's own, tokens kept encrypted."""
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, StaticPool, create_engine, select

import services.identity.main as main
from services.identity.main import app, require_api_key, require_internal
from services.identity.models import LinkedAccount, User


@pytest.fixture
def family():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    main.engine = engine
    with Session(engine) as session:
        session.add(User(username="emma", family_role="child"))
        session.add(User(username="dad", family_role="father"))
        session.commit()
    acting = {"as": "emma"}

    def caller():
        with Session(engine) as s:
            return next(u for u in s.exec(select(User)).all() if u.username == acting["as"])

    app.dependency_overrides[require_api_key] = caller
    app.dependency_overrides[require_internal] = lambda: True

    def act(name):
        acting["as"] = name
        return TestClient(app)

    yield act, engine
    app.dependency_overrides = {}


def test_the_callback_stores_tokens_encrypted_and_the_member_sees_only_the_link(family):
    act, engine = family
    stored = act("emma").put("/api/internal/linked-accounts/spotify/emma", json={
        "access_token": "AT", "refresh_token": "RT", "expires_at": 123, "account_name": "Emma S", "account_id": "e1",
    })
    assert stored.status_code == 200
    with Session(engine) as s:
        row = s.get(LinkedAccount, ("emma", "spotify"))
        assert row.access_token_enc and row.access_token_enc != "AT" and row.refresh_token_enc != "RT"
    mine = act("emma").get("/api/linked-accounts/spotify").json()
    assert mine["linked"] is True and mine["account_name"] == "Emma S"
    assert "access_token" not in mine and "refresh_token" not in mine
    assert act("dad").get("/api/linked-accounts/spotify").json() == {"linked": False}


def test_the_sync_reads_tokens_and_moves_the_cursor_forward_only(family):
    act, _ = family
    act("emma").put("/api/internal/linked-accounts/spotify/emma", json={"access_token": "AT", "refresh_token": "RT"})
    accounts = act("emma").get("/api/internal/linked-accounts/spotify").json()["accounts"]
    assert accounts == [{"username": "emma", "access_token": "AT", "refresh_token": "RT", "expires_at": 0, "cursor_ms": 0}]
    act("emma").patch("/api/internal/linked-accounts/spotify/emma", json={"cursor_ms": 500, "synced": True, "last_error": None})
    act("emma").patch("/api/internal/linked-accounts/spotify/emma", json={"cursor_ms": 100, "access_token": "AT2", "expires_at": 9})
    after = act("emma").get("/api/internal/linked-accounts/spotify").json()["accounts"][0]
    assert after["cursor_ms"] == 500 and after["access_token"] == "AT2"
    act("emma").patch("/api/internal/linked-accounts/spotify/emma", json={"last_error": "Spotify answered 503"})
    assert act("emma").get("/api/linked-accounts/spotify").json()["last_error"] == "Spotify answered 503"


def test_unlinking_deletes_the_tokens_and_unknown_providers_are_refused(family):
    act, engine = family
    act("emma").put("/api/internal/linked-accounts/spotify/emma", json={"access_token": "AT", "refresh_token": "RT"})
    assert act("emma").delete("/api/linked-accounts/spotify").json() == {"linked": False}
    with Session(engine) as s:
        assert s.get(LinkedAccount, ("emma", "spotify")) is None
    assert act("emma").get("/api/linked-accounts/myspace").status_code == 404
    assert act("emma").put("/api/internal/linked-accounts/spotify/emma", json={"access_token": "AT"}).status_code == 422

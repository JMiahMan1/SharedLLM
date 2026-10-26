"""The startup warning has to name the two silent sign-in problems.

Observed on the live household: the administrator still signed in with the
placeholder word in `DEFAULT_ADMIN_PASSWORD`, and two imported members had no
password at all so they could never sign in. Nothing said either thing.
"""
import logging

import pytest
from sqlmodel import Session, SQLModel, StaticPool, create_engine

from services.identity.main import _warn_about_sign_in
from services.identity.models import User
from services.identity.seed import hash_password


@pytest.fixture(name="session")
def session_fixture():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def test_it_names_the_administrator_still_using_the_seeded_password(session, monkeypatch, caplog):
    monkeypatch.setenv("DEFAULT_ADMIN_PASSWORD", "changeme")
    session.add(
        User(
            username="default",
            is_admin=True,
            is_system_default=True,
            password_hash=hash_password("changeme"),
        )
    )
    session.commit()

    with caplog.at_level(logging.WARNING, logger="services.identity.main"):
        _warn_about_sign_in(session)

    said = " ".join(record.getMessage() for record in caplog.records)
    assert "short or obvious" in said
    assert "still signs in with DEFAULT_ADMIN_PASSWORD" in said


def test_it_names_every_account_that_cannot_sign_in(session, monkeypatch, caplog):
    monkeypatch.setenv("DEFAULT_ADMIN_PASSWORD", "a-long-enough-passphrase")
    session.add(User(username="default", is_admin=True, is_system_default=True))
    session.add(User(username="kaleb", api_key="key-kaleb"))
    session.add(User(username="gracie", api_key="key-gracie"))
    session.add(
        User(username="alice", password_hash=hash_password("a-password"), api_key="key-alice")
    )
    session.commit()

    with caplog.at_level(logging.WARNING, logger="services.identity.main"):
        _warn_about_sign_in(session)

    said = " ".join(record.getMessage() for record in caplog.records)
    assert "2 account(s) have no password" in said
    assert "gracie" in said and "kaleb" in said
    # The administrator's own missing hash is not the same problem.
    assert "default" not in said.split("cannot sign in:")[1]


def test_a_strong_password_that_matches_nothing_says_nothing(session, monkeypatch, caplog):
    monkeypatch.setenv("DEFAULT_ADMIN_PASSWORD", "a-long-enough-passphrase")
    session.add(
        User(
            username="default",
            is_admin=True,
            is_system_default=True,
            password_hash=hash_password("something-else-entirely"),
        )
    )
    session.add(User(username="alice", password_hash=hash_password("a-password"), api_key="k"))
    session.commit()

    with caplog.at_level(logging.WARNING, logger="services.identity.main"):
        _warn_about_sign_in(session)

    assert [record.getMessage() for record in caplog.records if record.levelno >= logging.WARNING] == []

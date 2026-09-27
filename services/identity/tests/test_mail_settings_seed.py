import os

os.environ["INTERNAL_SECRET"] = "test-secret"
os.environ["FERNET_KEY"] = "bW9ja2VkLWtleS1mb3ItdGVzdGluZy1wdXJwb3NlcyE="
os.environ["DEFAULT_ADMIN_PASSWORD"] = "changeme"
os.environ["MAIL_URL"] = "https://mail.sumemail.com"
os.environ["MAIL_ADMIN_URL"] = "https://mail.sumemail.com/admin"
os.environ["MAIL_ADMIN"] = "house-admin"
os.environ["MAIL_PASS"] = "super-secret-mail-password"
os.environ["MAIL_USER"] = "mom"
os.environ["MAIL_USER_PASS"] = "super-secret-user-password"

import logging

from sqlmodel import Session, SQLModel, StaticPool, create_engine, select

from services.identity.crypto import decrypt
from services.identity.models import GlobalSetting, User
from services.identity.seed import seed_from_env


def _session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    session = Session(engine)
    session.add(User(username="mom"))
    session.add(User(username="dad"))
    session.commit()
    return session


def test_mail_settings_are_seeded_into_the_database():
    with _session() as session:
        seed_from_env(session, force=True)

        stored = {g.key: g.value for g in session.exec(select(GlobalSetting)).all()}

    assert stored["mail_url"] == "https://mail.sumemail.com"
    assert stored["mail_admin_url"] == "https://mail.sumemail.com/admin"
    assert stored["mail_admin"] == "house-admin"
    assert stored["mail_pass"] == "super-secret-mail-password"
    # Per-user credentials are NOT global settings.
    assert "mail_user" not in stored
    assert "mail_user_pass" not in stored


def test_seeded_mail_password_is_never_logged(caplog):
    with _session() as session:
        with caplog.at_level(logging.INFO, logger="identity.seed"):
            seed_from_env(session, force=True)

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "super-secret-mail-password" not in logged
    assert "super-secret-user-password" not in logged
    # The key is still announced so an operator can see it landed.
    assert "MAIL_PASS" in logged


def test_mail_user_credentials_land_on_the_matching_jarvis_user():
    with _session() as session:
        seed_from_env(session, force=True)

        mom = session.exec(select(User).where(User.username == "mom")).first()
        assert mom is not None
        assert mom.mail_user == "mom"
        assert decrypt(mom.mail_pass_enc) == "super-secret-user-password"

        # The credential did not leak onto anyone else.
        others = [u for u in session.exec(select(User)).all() if u.username != "mom"]
        assert others and all(u.mail_user is None for u in others)


def test_mail_user_matching_nobody_is_reported_not_shared(caplog):
    os.environ["MAIL_USER"] = "nobody-at-all"
    try:
        with _session() as session:
            with caplog.at_level(logging.INFO, logger="identity.seed"):
                seed_from_env(session, force=True)

        logged = "\n".join(r.getMessage() for r in caplog.records)
        assert "matches no Jarvis user" in logged
        # Nobody ended up holding the credential.
        with _session() as session:
            assert all(u.mail_user is None for u in session.exec(select(User)).all())
    finally:
        os.environ["MAIL_USER"] = "mom"

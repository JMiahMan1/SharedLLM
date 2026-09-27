import os

os.environ["INTERNAL_SECRET"] = "test-secret"
os.environ["FERNET_KEY"] = "bW9ja2VkLWtleS1mb3ItdGVzdGluZy1wdXJwb3NlcyE="
os.environ["DEFAULT_ADMIN_PASSWORD"] = "changeme"
os.environ["MAIL_URL"] = "https://mail.sumemail.com"
os.environ["MAIL_ADMIN_URL"] = "https://mail.sumemail.com/admin"
os.environ["MAIL_ADMIN"] = "house-admin"
os.environ["MAIL_PASS"] = "super-secret-mail-password"
os.environ["MAIL_USER"] = "house"
os.environ["MAIL_USER_PASS"] = "super-secret-user-password"

import logging

from sqlmodel import Session, SQLModel, StaticPool, create_engine, select

from services.identity.models import GlobalSetting
from services.identity.seed import seed_from_env


def _session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def test_mail_settings_are_seeded_into_the_database():
    with _session() as session:
        seed_from_env(session, force=True)

        stored = {g.key: g.value for g in session.exec(select(GlobalSetting)).all()}

    assert stored["mail_url"] == "https://mail.sumemail.com"
    assert stored["mail_admin_url"] == "https://mail.sumemail.com/admin"
    assert stored["mail_admin"] == "house-admin"
    assert stored["mail_pass"] == "super-secret-mail-password"
    assert stored["mail_user"] == "house"
    assert stored["mail_user_pass"] == "super-secret-user-password"


def test_seeded_mail_password_is_never_logged(caplog):
    with _session() as session:
        with caplog.at_level(logging.INFO, logger="identity.seed"):
            seed_from_env(session, force=True)

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "super-secret-mail-password" not in logged
    assert "super-secret-user-password" not in logged
    # The key is still announced so an operator can see it landed.
    assert "MAIL_PASS" in logged

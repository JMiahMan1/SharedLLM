"""DNS settings come from .env once, at seed time; after that the database is the authority."""
import json
import os

import pytest
from sqlmodel import Session, SQLModel, StaticPool, create_engine, select

from services.identity.models import GlobalSetting
from services.identity.seed import seed_from_env

MAPPINGS = json.dumps({"jeremiah-home-desktop.local": {"values": ["192.168.2.43", "192.168.4.179"]}})


@pytest.fixture(autouse=True)
def _env():
    os.environ.update({
        "INTERNAL_SECRET": "test-secret",
        "FERNET_KEY": "bW9ja2VkLWtleS1mb3ItdGVzdGluZy1wdXJwb3NlcyE=",
        "DEFAULT_ADMIN_PASSWORD": "changeme",
        "DNS_MAPPINGS": MAPPINGS,
        "DNS_HEALTH_PORTS": "11434,5000",
    })
    yield


def _session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _stored(session):
    return {g.key: g.value for g in session.exec(select(GlobalSetting)).all()}


def test_an_empty_mappings_value_is_filled_from_env():
    with _session() as session:
        seed_from_env(session, force=True)
        stored = _stored(session)
    assert json.loads(stored["dns_mappings"]) == json.loads(MAPPINGS)
    # A default that is a real value is the database's: .env does not replace it.
    assert stored["dns_health_ports"] == "11434,80,443,8080,8000,9000"


def test_after_that_the_database_wins_over_env():
    with _session() as session:
        seed_from_env(session, force=True)
        row = session.exec(select(GlobalSetting).where(GlobalSetting.key == "dns_mappings")).one()
        row.value = json.dumps({"jeremiah-home-desktop.local": ["192.168.2.43"]})  # edited in the UI
        session.add(row)
        session.commit()
        seed_from_env(session, force=True)  # next restart
        stored = _stored(session)
    assert json.loads(stored["dns_mappings"]) == {"jeremiah-home-desktop.local": ["192.168.2.43"]}

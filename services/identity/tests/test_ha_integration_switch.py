"""The Home Assistant integration's switch in identity (integrations step 1).

HA is optional: a blank HA_URL (or the ``ha_url`` setting saved blank) turns it
off. Off, the start-up seed no longer refills the household account's HA login
from .env, and that stored login stops being the stack's HA login: callers get
no HA credentials and fall through to HomeHub. Nobody's stored data is changed.
"""
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlmodel import Session, SQLModel, StaticPool, create_engine, select

from services.identity import main as identity_main
from services.identity.crypto import decrypt, encrypt
from services.identity.main import LocationUpdate, _forward_location_to_ha, resolve_identity
from services.identity.models import GlobalSetting, User
from services.identity.schemas import ResolveRequest
from services.identity.seed import ha_integration_enabled, seed_from_env

STORED_URL = "http://ha.example.test:8123"

@pytest.fixture
def config():
    """services.config as the code sees it now (some tests re-import that module)."""
    from services import config as current

    return current



@pytest.fixture
def session(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(identity_main, "engine", engine)
    monkeypatch.setattr(identity_main, "HA_INTEGRATION_ENABLED", None)
    with Session(engine) as s:
        seed_from_env(s, force=True)
        yield s


def _default(session) -> User:
    return session.exec(select(User).where(User.username == "default")).one()


def _store_default_ha(session, url=STORED_URL, token="stored-ha-token"):
    user = _default(session)
    user.ha_url = url
    user.ha_token_enc = encrypt(token) if token else None
    session.add(user)
    session.commit()


def _ha_setting(session, value):
    session.add(GlobalSetting(key="ha_url", value=value))
    session.commit()


def _reseed(session, env_file):
    with patch("services.identity.seed.dotenv_values", return_value=env_file), \
         patch("services.identity.seed.os.path.exists", return_value=True):
        seed_from_env(session, force=False)


# -- the switch -----------------------------------------------------------------------------------

def test_the_saved_setting_decides_and_blank_means_off(session):
    assert ha_integration_enabled(session, "http://ha.test") is True
    assert ha_integration_enabled(session, "") is False
    _ha_setting(session, "  ")
    assert ha_integration_enabled(session, "http://ha.test") is False


# -- the seed --------------------------------------------------------------------------------------

def test_seed_does_not_refill_the_household_ha_login_when_ha_url_is_blank(session, monkeypatch):
    _store_default_ha(session, url=None, token=None)
    monkeypatch.delenv("HA_URL", raising=False)
    _reseed(session, {"HA_URL": "", "HA_TOKEN": "an-env-token"})
    user = _default(session)
    assert user.ha_url is None and user.ha_token_enc is None


def test_seed_does_not_refill_when_the_setting_turned_ha_off(session):
    _store_default_ha(session, url=None, token=None)
    _ha_setting(session, "")
    _reseed(session, {"HA_URL": "http://ha.test", "HA_TOKEN": "an-env-token"})
    user = _default(session)
    assert user.ha_url is None and user.ha_token_enc is None


def test_seed_still_fills_a_blank_household_ha_login_while_ha_is_on(session):
    _store_default_ha(session, url=None, token=None)
    _reseed(session, {"HA_URL": "http://ha.test", "HA_TOKEN": "an-env-token"})
    user = _default(session)
    assert user.ha_url == "http://ha.test" and decrypt(user.ha_token_enc) == "an-env-token"


def test_seed_leaves_stored_data_alone_when_ha_is_off(session):
    _store_default_ha(session)
    _ha_setting(session, "")
    _reseed(session, {"HA_URL": "", "HA_TOKEN": ""})
    user = _default(session)
    assert user.ha_url == STORED_URL and decrypt(user.ha_token_enc) == "stored-ha-token"


# -- credentials -----------------------------------------------------------------------------------

def test_household_gets_no_ha_login_while_ha_is_off(session):
    _store_default_ha(session)
    _ha_setting(session, "")
    creds = resolve_identity(ResolveRequest(household=True), session)
    assert creds.ha_url is None and creds.ha_token is None


def test_household_keeps_its_ha_login_while_ha_is_on(session):
    _store_default_ha(session)
    creds = resolve_identity(ResolveRequest(household=True), session)
    assert creds.ha_url == STORED_URL and creds.ha_token == "stored-ha-token"


def _person_with_own_ha(session) -> User:
    person = User(username="pat", display_name="Pat", ha_url="http://own.test:8123",
                  ha_token_enc=encrypt("own-token"))
    session.add(person)
    session.commit()
    return person


def test_a_persons_own_ha_login_is_not_used_while_ha_is_off(session):
    _store_default_ha(session)
    _ha_setting(session, "")
    _person_with_own_ha(session)
    creds = resolve_identity(ResolveRequest(rag_user="pat"), session)
    assert creds.ha_url is None and creds.ha_token is None
    # Not used is not deleted: the stored login is still there for when HA is back on.
    stored = session.exec(select(User).where(User.username == "pat")).one()
    assert stored.ha_url == "http://own.test:8123" and decrypt(stored.ha_token_enc) == "own-token"


def test_a_persons_own_ha_login_is_theirs_while_ha_is_on(session):
    _store_default_ha(session)
    _person_with_own_ha(session)
    creds = resolve_identity(ResolveRequest(rag_user="pat"), session)
    assert creds.ha_url == "http://own.test:8123" and creds.ha_token == "own-token"


# -- the location forward ------------------------------------------------------------------------

def _mock_client():
    resp = MagicMock()
    resp.status = 200
    resp.text = AsyncMock(return_value="[]")
    client = MagicMock()
    client.post.return_value.__aenter__ = AsyncMock(return_value=resp)
    client.post.return_value.__aexit__ = AsyncMock(return_value=None)

    @asynccontextmanager
    async def get_client():
        yield client

    return client, get_client


async def test_location_goes_to_homehub_not_the_stored_ha_login_while_ha_is_off(session, monkeypatch, config):
    _store_default_ha(session)
    _ha_setting(session, "")
    monkeypatch.setattr(config, "HA_URL", "")
    monkeypatch.setattr(config, "HOMEHUB_URL", "http://hub.test:8123")
    client, get_client = _mock_client()
    with patch("services.identity.main.get_client_insecure", get_client):
        await _forward_location_to_ha("default", LocationUpdate(latitude=1.0, longitude=2.0, accuracy=5))
    urls = [c.args[0] for c in client.post.call_args_list]
    assert urls and all(u.startswith("http://hub.test:8123/") for u in urls)


async def test_a_persons_own_ha_login_is_not_used_for_location_while_ha_is_off(session, monkeypatch, config):
    _ha_setting(session, "")
    _person_with_own_ha(session)
    monkeypatch.setattr(config, "HA_URL", "")
    monkeypatch.setattr(config, "HOMEHUB_URL", "http://hub.test:8123")
    client, get_client = _mock_client()
    with patch("services.identity.main.get_client_insecure", get_client):
        await _forward_location_to_ha("pat", LocationUpdate(latitude=1.0, longitude=2.0, accuracy=5))
    urls = [c.args[0] for c in client.post.call_args_list]
    assert urls and all(u.startswith("http://hub.test:8123/") for u in urls)
    assert not any("own.test" in u for u in urls)


async def test_no_hub_at_all_forwards_nothing(session, monkeypatch, config):
    _store_default_ha(session)
    _ha_setting(session, "")
    monkeypatch.setattr(config, "HA_URL", "")
    monkeypatch.setattr(config, "HOMEHUB_URL", "")
    client, get_client = _mock_client()
    with patch("services.identity.main.get_client_insecure", get_client):
        await _forward_location_to_ha("default", LocationUpdate(latitude=1.0, longitude=2.0, accuracy=5))
    assert not client.post.called

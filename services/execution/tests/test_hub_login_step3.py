"""The places that pick a hub login go through services.common.hub_login (integrations step 3).

With Home Assistant off, no HA login or HA address is used anywhere: not a person's
own, not the household's, not one handed to a workspace sandbox. A context that was
already given the hub's own login is never mistaken for the caller's own. With no
hub at all, callers get the shared NO_HUB_MESSAGE.
"""
import asyncio

import pytest

import services.config as config
from services.common import hub_login

OWN = {"ha_url": "http://ha.example.test:8123", "ha_token": "made-up-own-token"}
HUB = "http://hub.example.test:8123"
SECRET = "made-up-internal-secret"


@pytest.fixture
def ha_on(monkeypatch):
    monkeypatch.setattr(config, "HA_URL", "http://ha.example.test:8123")
    monkeypatch.setattr(config, "HA_TOKEN", "made-up-household-token")
    monkeypatch.setattr(config, "HOMEHUB_URL", HUB)
    monkeypatch.setattr(config, "INTERNAL_SECRET", SECRET)


@pytest.fixture
def ha_off(monkeypatch):
    monkeypatch.setattr(config, "HA_URL", "")
    monkeypatch.setattr(config, "HA_TOKEN", "made-up-household-token")
    monkeypatch.setattr(config, "HOMEHUB_URL", HUB)
    monkeypatch.setattr(config, "INTERNAL_SECRET", SECRET)


@pytest.fixture
def no_hub(monkeypatch):
    monkeypatch.setattr(config, "HA_URL", "")
    monkeypatch.setattr(config, "HOMEHUB_URL", "")
    monkeypatch.setattr(config, "INTERNAL_SECRET", SECRET)


# --- hub_login itself -------------------------------------------------------------------------

def test_own_login_is_the_callers_while_ha_is_on(ha_on):
    assert hub_login.own_login(OWN) == (OWN["ha_url"], OWN["ha_token"])


def test_own_login_is_none_while_ha_is_off(ha_off):
    assert hub_login.own_login(OWN) is None


def test_a_context_already_on_the_hubs_login_has_no_login_of_its_own(ha_on):
    swapped = {"ha_url": HUB, "ha_token": SECRET}
    assert hub_login.own_login(swapped) is None
    assert hub_login.has_own_login(swapped) is False
    assert hub_login.hub_login(swapped) == (HUB, SECRET)


# --- execution ----------------------------------------------------------------------------

def test_execution_does_not_count_a_stored_token_as_own_while_ha_is_off(ha_off):
    from services.execution import main
    from services.execution.schemas import UserContext

    ctx = UserContext(user="alex", **OWN)
    assert main._has_own_hub_login(ctx) is False
    assert main._has_own_hub_login(UserContext(user="alex", ha_url=HUB, ha_token=SECRET)) is False


def test_execution_counts_a_stored_token_as_own_while_ha_is_on(ha_on):
    from services.execution import main
    from services.execution.schemas import UserContext

    assert main._has_own_hub_login(UserContext(user="alex", **OWN)) is True


def test_with_hub_login_says_the_no_hub_message_without_a_hub(no_hub):
    from fastapi import HTTPException

    from services.execution import main
    from services.execution.schemas import MediaStatusRequest, UserContext

    req = MediaStatusRequest(user_context=UserContext(user="alex", **OWN))
    with pytest.raises(HTTPException) as err:
        main._with_hub_login(req)
    assert err.value.status_code == 503
    assert err.value.detail == hub_login.NO_HUB_MESSAGE


def test_the_tv_stage_does_not_look_up_a_household_ha_login_while_ha_is_off(ha_off, monkeypatch):
    from services.execution import main

    asked: list = []

    async def resolve(**kwargs):
        asked.append(kwargs)
        return dict(OWN, user="alex")

    monkeypatch.setattr(main, "resolve_internal_user", resolve)
    assert asyncio.run(main._stage_ha_creds("alex")) == (HUB, SECRET)
    assert asked == [{"rag_user": "alex"}]


def test_a_workspace_gets_no_ha_login_while_ha_is_off(ha_off):
    from services.execution.handlers.workspace import _identity_secrets_to_env

    env = _identity_secrets_to_env({"user": "alex", "github_token": "made-up-gh", **OWN})
    assert env["GITHUB_TOKEN"] == "made-up-gh"
    assert not {"HA_URL", "HA_TOKEN", "HOME_ASSISTANT_TOKEN"} & set(env)


def test_a_workspace_gets_the_callers_own_ha_login_while_ha_is_on(ha_on):
    from services.execution.handlers.workspace import _identity_secrets_to_env

    env = _identity_secrets_to_env({"user": "alex", **OWN})
    assert env["HA_URL"] == OWN["ha_url"] and env["HA_TOKEN"] == OWN["ha_token"]


def test_a_workspace_never_gets_the_hubs_own_login(ha_on):
    from services.execution.handlers.workspace import _identity_secrets_to_env

    env = _identity_secrets_to_env({"user": "alex", "ha_url": HUB, "ha_token": SECRET})
    assert SECRET not in env.values()


def test_the_sandbox_gets_no_ha_env_while_ha_is_off(ha_off, monkeypatch):
    from services import workspace_sandbox as ws

    for key in ws._SANDBOX_CRED_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("HA_TOKEN", "made-up-env-token")
    monkeypatch.setenv("HA_URL", "http://retired.example.test:8123")
    monkeypatch.setenv("GH_TOKEN", "made-up-gh")
    env = ws._sandbox_credential_env()
    assert env == {"GH_TOKEN": "made-up-gh"}
    assert "HA_TOKEN" not in ws._sandbox_credential_env_with_user({"HA_TOKEN": "made-up-user-token"})


def test_the_sandbox_gets_the_startup_ha_address_while_ha_is_on(ha_on, monkeypatch):
    from services import workspace_sandbox as ws

    for key in ws._SANDBOX_CRED_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("HA_TOKEN", "made-up-env-token")
    monkeypatch.setenv("HA_URL", "http://retired.example.test:8123")
    env = ws._sandbox_credential_env()
    assert env == {"HA_TOKEN": "made-up-env-token", "HA_URL": "http://ha.example.test:8123"}


def test_ha_config_uses_no_stored_ha_login_while_ha_is_off(ha_off, monkeypatch):
    from services.execution.handlers import ha_config

    class _Resp:
        status = 200

        async def json(self):
            return dict(OWN, user="alex")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

    class _Session:
        def __init__(self, *_a, **_k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        def post(self, *_a, **_k):
            return _Resp()

    monkeypatch.setattr(ha_config.aiohttp, "ClientSession", _Session)
    assert asyncio.run(ha_config._get_ha_credentials({"user": "alex"})) == (None, None)
    monkeypatch.setattr(config, "HA_URL", "http://ha.example.test:8123")
    assert asyncio.run(ha_config._get_ha_credentials({"user": "alex"})) == (OWN["ha_url"], OWN["ha_token"])


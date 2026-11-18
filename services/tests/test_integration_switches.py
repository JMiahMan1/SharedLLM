"""HomeHub and Home Assistant are optional integrations, each with one switch (integrations step 1).

``services.config`` holds the switch for both: HOMEHUB_URL and HA_URL, from .env
and then Identity's ``homehub_url`` / ``ha_url`` settings, resolved once at
startup. Blank is off, and an integration that is off is never contacted.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys

import pytest

from services.common import hub_login

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MAP = {"homehub_url": "HOMEHUB_URL", "ha_url": "HA_URL", "ha_token": "HA_TOKEN",
       "nextcloud_url": "NEXTCLOUD_URL", "log_retention_days": "LOG_RETENTION_DAYS"}

@pytest.fixture
def config():
    """services.config as the code sees it now (some tests re-import that module)."""
    from services import config as current

    return current



# -- services.config --------------------------------------------------------------------------------

def test_homehub_has_no_default_address(config):
    env = {k: v for k, v in os.environ.items() if k != "HOMEHUB_URL"}
    env.update(INTERNAL_SECRET="made-up-secret", FERNET_KEY="made-up-key")
    out = subprocess.run(
        [sys.executable, "-c", "import services.config as c; print(repr(c.HOMEHUB_URL), c.integrations())"],
        cwd=ROOT, env=env, capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert out.startswith("''") and "'homehub': False" in out


def test_homehub_url_is_a_runtime_setting(config):
    import inspect

    assert '"homehub_url": "HOMEHUB_URL"' in inspect.getsource(config.resolve_runtime_config)


def test_a_blank_setting_turns_an_integration_off_over_env(monkeypatch, config):
    monkeypatch.setattr(config, "HOMEHUB_URL", "http://hub.env:8123")
    monkeypatch.setattr(config, "HA_URL", "http://ha.env:8123")
    monkeypatch.setattr(config, "HA_TOKEN", "env-token")
    config._apply_settings({"homehub_url": "", "ha_url": "  ", "ha_token": ""}, MAP)
    assert (config.HOMEHUB_URL, config.HA_URL, config.HA_TOKEN) == ("", "", "")
    assert config.integrations() == {"homehub": False, "home_assistant": False}
    assert config.any_home_hub() is False


def test_a_blank_setting_still_leaves_other_env_values_alone(monkeypatch, config):
    # Most settings are seeded blank on purpose ("use .env"); only the switches clear.
    monkeypatch.setattr(config, "NEXTCLOUD_URL", "http://cloud.env")
    monkeypatch.setattr(config, "LOG_RETENTION_DAYS", 30)
    config._apply_settings({"nextcloud_url": "", "log_retention_days": ""}, MAP)
    assert config.NEXTCLOUD_URL == "http://cloud.env" and config.LOG_RETENTION_DAYS == 30


def test_a_missing_setting_leaves_env_alone_and_a_set_one_wins(monkeypatch, config):
    monkeypatch.setattr(config, "HOMEHUB_URL", "http://hub.env:8123")
    monkeypatch.setattr(config, "HA_URL", "")
    monkeypatch.setattr(config, "LOG_RETENTION_DAYS", 30)
    config._apply_settings({"ha_url": " http://ha.ui:8123 ", "log_retention_days": "7"}, MAP)
    assert config.HOMEHUB_URL == "http://hub.env:8123"
    assert config.HA_URL == "http://ha.ui:8123" and config.LOG_RETENTION_DAYS == 7
    config._apply_settings({"homehub_url": "http://hub.ui:8123/"}, MAP)
    assert config.HOMEHUB_URL == "http://hub.ui:8123"
    assert config.integrations() == {"homehub": True, "home_assistant": True}


# -- the hub's own login ------------------------------------------------------------------------

def test_blank_homehub_means_no_stack_login(monkeypatch, config):
    monkeypatch.setattr(config, "HOMEHUB_URL", "")
    monkeypatch.setattr(config, "HA_URL", "http://ha.test:8123")
    assert hub_login.stack_login() is None
    assert hub_login.hub_login({}) is None
    assert hub_login.hub_pair(None) == ("", "")


def test_no_hub_message_is_for_people_not_env_files():
    assert "HOMEHUB_URL" not in hub_login.NO_HUB_MESSAGE and "HA_URL" not in hub_login.NO_HUB_MESSAGE
    assert "No home hub is set up" in hub_login.NO_HUB_MESSAGE


# -- nothing calls a hub that is off ----------------------------------------------------------------

class _Refuse:
    """An HTTP client that fails the test if anything is asked of it."""

    def __getattr__(self, name):
        raise AssertionError(f"the hub was contacted ({name}) while it is off")


@pytest.mark.parametrize("module", ["discovery_routes", "phone_routes", "camera_live_routes",
                                    "dashboard_routes", "automation_routes"])
def test_homehub_admin_pages_answer_503_while_homehub_is_off(module, monkeypatch, config):
    import importlib

    from fastapi import HTTPException

    mod = importlib.import_module(f"services.gateway.{module}")
    monkeypatch.setattr(config, "HOMEHUB_URL", "")
    with pytest.raises(HTTPException) as err:
        mod._homehub_base()
    assert err.value.status_code == 503
    monkeypatch.setattr(config, "HOMEHUB_URL", "http://hub.test:8123")
    assert mod._homehub_base() == "http://hub.test:8123"


async def test_sentence_triggers_ask_nobody_while_homehub_is_off(monkeypatch, config):
    from services.gateway import main as gateway_main
    from services.gateway import sentence_triggers

    monkeypatch.setattr(config, "HOMEHUB_URL", "")
    monkeypatch.setattr(gateway_main, "shared_http_client", _Refuse())
    assert await sentence_triggers.ask_hub("I'm leaving") is None


async def test_the_app_check_asks_nobody_while_homehub_is_off(monkeypatch, config):
    from services.telemetry import app_catalog

    monkeypatch.setattr(config, "HOMEHUB_URL", "")
    monkeypatch.setattr(app_catalog, "_settings_cache", None)
    assert await app_catalog.check_settings(_Refuse()) == {"common_sense": False}


async def test_entity_sync_skips_when_no_hub_is_set_up(monkeypatch, config):
    from services.gateway import main as gateway_main

    monkeypatch.setattr(config, "HOMEHUB_URL", "")
    monkeypatch.setattr(config, "HA_URL", "")
    monkeypatch.setattr(gateway_main, "shared_http_client", _Refuse())
    monkeypatch.setattr(gateway_main, "get_http_client", _Refuse())
    assert await gateway_main.fetch_ha_entities({"user": "pat"}) == []


async def test_execution_loops_do_not_start_without_a_hub(monkeypatch, config):
    from services.execution import home_alerts
    from services.execution import main as exec_main

    monkeypatch.setattr(config, "HOMEHUB_URL", "")
    monkeypatch.setattr(config, "HA_URL", "")

    async def refuse(*_a, **_k):
        raise AssertionError("asked for a hub login while no hub is set up")

    monkeypatch.setattr(exec_main, "resolve_internal_user", refuse)
    monkeypatch.setattr(home_alerts, "check_once", refuse)
    await asyncio.wait_for(exec_main.telemetry_ingestion_loop(interval_seconds=0), 5)
    await asyncio.wait_for(home_alerts.home_alert_loop(), 5)


# -- the switches the UI reads ----------------------------------------------------------------------

def test_integrations_status_route(monkeypatch, config):
    from fastapi.testclient import TestClient

    from services.gateway import main as gateway_main

    async def anyone(request, body=None):
        return {"user": "pat", "is_admin": False}

    monkeypatch.setattr(gateway_main, "_require_identity_from_request", anyone)
    monkeypatch.setattr(config, "HOMEHUB_URL", "http://hub.test:8123")
    monkeypatch.setattr(config, "HA_URL", "")
    body = TestClient(gateway_main.app).get("/api/integrations/status").json()
    assert body == {"homehub": {"enabled": True}, "home_assistant": {"enabled": False}}
    assert "hub.test" not in str(body)


def test_integrations_status_needs_a_signed_in_caller(monkeypatch):
    from fastapi import HTTPException
    from fastapi.testclient import TestClient

    from services.gateway import main as gateway_main

    async def nobody(request, body=None):
        raise HTTPException(status_code=401, detail="Authentication required")

    monkeypatch.setattr(gateway_main, "_require_identity_from_request", nobody)
    assert TestClient(gateway_main.app).get("/api/integrations/status").status_code == 401

"""Expired stored credentials are flagged once and admins are notified once.

The first rejection sends an admin notice (through telemetry) and logs one
line; repeats stay quiet while the flag holds; an acceptance replaces the
notice with a "works again" card on the phone.
"""

import json
import logging
from unittest.mock import AsyncMock

import pytest

from services.execution import credential_alerts as ca

STATE_KEY = ca.STATE_KEY
KEY = "home_assistant:the system account"


class FakeRedis:
    def __init__(self):
        self.store: dict[str, str] = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value):
        self.store[key] = value
        return True

    async def aclose(self):
        return None


@pytest.fixture
def redis(monkeypatch):
    fake = FakeRedis()

    async def _client():
        return fake

    monkeypatch.setattr(ca, "_mirror", None)
    monkeypatch.setattr(ca, "_redis_client", _client)
    monkeypatch.setattr(ca.notify, "send", AsyncMock(return_value=True))
    return fake


async def test_a_rejection_notifies_once_and_repeats_stay_quiet(redis, caplog):
    with caplog.at_level(logging.WARNING, logger="execution.credentials"):
        await ca.auth_failed("home_assistant", "the system account", "HTTP 401")
        await ca.auth_failed("home_assistant", "the system account", "HTTP 401")

    warnings = [r for r in caplog.records if "rejected the" in r.getMessage()]
    assert len(warnings) == 1
    ca.notify.send.assert_awaited_once()
    payload = ca.notify.send.await_args.args[0]
    assert payload["title"] == "Credential expired: Home Assistant"
    assert payload["tag"] == f"admin:expired:{KEY}"
    assert payload["priority"] == 3
    assert payload["user"] == "@admins"


async def test_the_flag_is_kept_in_redis(redis):
    await ca.auth_failed("home_assistant", "the system account", "HTTP 401")
    state = json.loads(redis.store[STATE_KEY])
    assert KEY in state["flagged"]
    assert state["outbox"] == []


async def test_acceptance_replaces_the_notice(redis, caplog):
    await ca.auth_failed("home_assistant", "the system account", "HTTP 401")
    with caplog.at_level(logging.INFO, logger="execution.credentials"):
        await ca.auth_ok("home_assistant", "the system account")
        await ca.auth_ok("home_assistant", "the system account")

    infos = [r for r in caplog.records if "accepted the" in r.getMessage()]
    assert len(infos) == 1
    assert ca.notify.send.await_count == 2
    recovery = ca.notify.send.await_args.args[0]
    assert recovery["title"] == "Home Assistant login works again"
    assert recovery["tag"] == f"admin:expired:{KEY}"
    assert recovery["priority"] == 2


async def test_a_notice_telemetry_refused_is_retried(redis):
    ca.notify.send.side_effect = [False, True, True]
    await ca.auth_failed("home_assistant", "the system account", "HTTP 401")
    state = json.loads(redis.store[STATE_KEY])
    assert len(state["outbox"]) == 1

    await ca.auth_ok("home_assistant", "the system account")
    # The queued "expired" card goes out first, then the recovery card.
    titles = [call.args[0]["title"] for call in ca.notify.send.await_args_list]
    assert titles == [
        "Credential expired: Home Assistant",
        "Credential expired: Home Assistant",
        "Home Assistant login works again",
    ]
    assert json.loads(redis.store[STATE_KEY])["outbox"] == []

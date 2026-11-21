"""A tapped button on a Home notice fires mobile_app_notification_action in the device hub, as the
Home Assistant companion app does, so an automation waiting for the answer gets it. Only the person the
hub sent the notice to may press its buttons, and only the buttons it carried (signed by the hub)."""
import asyncio
import logging
import time

import pytest
from fastapi import HTTPException

from services.common import home_action_sign
from services.gateway import phone_routes, push_callbacks, push_routes

HANDLER = push_callbacks.PUSH_CALLBACKS["home_notification_action"]


@pytest.fixture
def hub(monkeypatch):
    calls = []
    box = {"status": 200, "user": "alex"}

    async def fake_hub(method, path, *, json=None):
        calls.append((method, path, json))
        return box["status"], {"message": "ok"}

    async def fake_caller(_request):
        return {"user": box["user"], "is_admin": False}

    monkeypatch.setattr(phone_routes, "_hub", fake_hub)
    monkeypatch.setattr(push_routes, "_caller", fake_caller)
    box["calls"] = calls
    return box


def _for(user, **params):
    """The button as the hub sends it to ``user``."""
    return home_action_sign.sign(user, params)


def test_the_button_fires_the_companion_apps_event_with_the_action_and_tag(hub):
    body = asyncio.run(HANDLER(object(), _for("alex", action="CONFIRM_ctx1", tag="door-check"), None))
    assert hub["calls"] == [("POST", "/api/events/mobile_app_notification_action",
                             {"action": "CONFIRM_ctx1", "tag": "door-check"})]
    assert body["status"] == "SUCCESS"


def test_a_reply_button_carries_the_typed_text(hub):
    asyncio.run(HANDLER(object(), _for("alex", action="REPLY"), "Back in ten"))
    assert hub["calls"][0][2] == {"action": "REPLY", "reply_text": "Back in ten"}


@pytest.mark.parametrize("params", [{}, {"action": ""}, {"action": 5}, {"action": "x" * 300},
                                    {"action": "OK", "tag": 7}])
def test_a_button_without_a_proper_action_is_refused(hub, params):
    with pytest.raises(HTTPException) as e:
        asyncio.run(HANDLER(object(), params, None))
    assert e.value.status_code == 422
    assert hub["calls"] == []


def test_a_hub_that_refuses_the_event_is_an_error_not_a_silent_success(hub):
    hub["status"] = 401
    with pytest.raises(HTTPException) as e:
        asyncio.run(HANDLER(object(), _for("alex", action="OK"), None))
    assert e.value.status_code == 502


def _refused(hub, params, caplog):
    with caplog.at_level(logging.WARNING), pytest.raises(HTTPException) as e:
        asyncio.run(HANDLER(object(), params, None))
    assert e.value.status_code == 403
    assert hub["calls"] == []
    assert [r for r in caplog.records if r.levelno == logging.WARNING and "refused" in r.getMessage()]
    return e.value.detail


def test_an_unsigned_action_is_refused(hub, caplog):
    assert "not signed" in _refused(hub, {"action": "DISARM_ALARM"}, caplog)


def test_someone_else_cannot_press_a_button_sent_to_alex(hub, caplog):
    hub["user"] = "sam"
    assert "not sent to this account" in _refused(hub, _for("alex", action="CONFIRM_ctx1", tag="door"), caplog)


def test_a_button_cannot_be_turned_into_another_action_or_notice(hub, caplog):
    button = _for("alex", action="DISMISS_ctx1", tag="door")
    assert "not sent to this account" in _refused(hub, {**button, "action": "CONFIRM_ctx1"}, caplog)
    assert "not sent to this account" in _refused(hub, {**button, "tag": "garage"}, caplog)
    assert "not sent to this account" in _refused(hub, {**button, "exp": button["exp"] + 999}, caplog)


def test_an_expired_button_is_refused(hub, caplog):
    old = home_action_sign.sign("alex", {"action": "OK"}, now=time.time() - home_action_sign.TTL_S - 10)
    assert "expired" in _refused(hub, old, caplog)

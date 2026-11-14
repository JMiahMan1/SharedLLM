"""A tapped button on a Home notice fires mobile_app_notification_action in the device hub, as the
Home Assistant companion app does, so an automation waiting for the answer gets it."""
import asyncio

import pytest
from fastapi import HTTPException

from services.gateway import phone_routes, push_callbacks

HANDLER = push_callbacks.PUSH_CALLBACKS["home_notification_action"]


@pytest.fixture
def hub(monkeypatch):
    calls = []
    box = {"status": 200}

    async def fake_hub(method, path, *, json=None):
        calls.append((method, path, json))
        return box["status"], {"message": "ok"}

    monkeypatch.setattr(phone_routes, "_hub", fake_hub)
    box["calls"] = calls
    return box


def test_the_button_fires_the_companion_apps_event_with_the_action_and_tag(hub):
    body = asyncio.run(HANDLER(object(), {"action": "CONFIRM_ctx1", "tag": "door-check"}, None))
    assert hub["calls"] == [("POST", "/api/events/mobile_app_notification_action",
                             {"action": "CONFIRM_ctx1", "tag": "door-check"})]
    assert body["status"] == "SUCCESS"


def test_a_reply_button_carries_the_typed_text(hub):
    asyncio.run(HANDLER(object(), {"action": "REPLY"}, "Back in ten"))
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
        asyncio.run(HANDLER(object(), {"action": "OK"}, None))
    assert e.value.status_code == 502

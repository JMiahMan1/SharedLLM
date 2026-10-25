"""The family guard / more-time notification buttons: a parent answers a
child's request from the notice, acting as the caller."""
import asyncio

import pytest
from fastapi import HTTPException

from services.gateway import push_callbacks


def test_the_family_buttons_are_registered():
    for name in ("guard_ask", "guard_decide", "more_time_decide"):
        assert name in push_callbacks.PUSH_CALLBACKS


def test_the_guard_button_decides_as_the_caller(monkeypatch):
    from services.gateway import main

    calls = []

    async def decide(request, request_id, payload):
        calls.append((request, request_id, payload))
        return 200, {"request": {"status": "approved"}}

    monkeypatch.setattr(main, "decide_guard_request", decide)
    request = object()
    body = asyncio.run(push_callbacks.PUSH_CALLBACKS["guard_decide"](request, {"id": 7, "approve": True}, None))
    assert calls == [(request, 7, {"approve": True})]
    assert body == {"request": {"status": "approved"}}


def test_the_more_time_button_grants_as_the_caller(monkeypatch):
    from services.gateway import main

    calls = []

    async def decide(request, request_id, payload):
        calls.append((request_id, payload))
        return 200, {"request": {"status": "granted", "granted_minutes": 30}}

    monkeypatch.setattr(main, "decide_more_time", decide)
    body = asyncio.run(push_callbacks.PUSH_CALLBACKS["more_time_decide"](object(), {"id": 3, "grant": True}, None))
    assert calls == [(3, {"grant": True})]
    assert body["request"]["granted_minutes"] == 30


@pytest.mark.parametrize("name,params", [
    ("guard_decide", {"approve": True}),
    ("guard_decide", {"id": "not-a-number", "approve": True}),
    ("more_time_decide", {}),
])
def test_a_button_without_a_request_id_is_refused(name, params):
    with pytest.raises(HTTPException) as e:
        asyncio.run(push_callbacks.PUSH_CALLBACKS[name](object(), params, None))
    assert e.value.status_code == 422


def test_a_parent_who_is_not_the_childs_parent_gets_identitys_refusal(monkeypatch):
    from services.gateway import main

    async def decide(request, request_id, payload):
        return 403, {"detail": "Only a parent can answer"}

    monkeypatch.setattr(main, "decide_guard_request", decide)
    with pytest.raises(HTTPException) as e:
        asyncio.run(push_callbacks.PUSH_CALLBACKS["guard_decide"](object(), {"id": 7, "approve": True}, None))
    assert e.value.status_code == 403 and e.value.detail == "Only a parent can answer"

"""The family member page's role rules at the gateway (docs/FAMILY_MEMBER_PAGE.md).

Identity decides who may see a child (``may_view_raw_data``) and who also sees
the technical side (``may_view_technical_data``: an admin and the father).
The gateway refuses the rest, and strips the technical side for the mother on
every route the page reads.
"""
from contextlib import asynccontextmanager
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from services.gateway import main as gateway_main
from services.gateway.main import app
from services.gateway.member_page import strip_technical

USAGE = {"status": "SUCCESS", "days": [{
    "day": "2026-10-21", "millis": 5_400_000, "first_use": 1, "last_use": 2, "sessions": 9,
    "source": "phone", "updated_at": "2026-10-21T20:00:00Z",
    "apps": [{"package": "com.google.android.youtube", "millis": 2_400_000, "last_use": 2}],
}]}
ACTIVITY = {"status": "SUCCESS", "events": [{
    "user": "kid", "feature": "web", "item": "example.org", "action": "visit", "at": "2026-10-21T19:00:00Z",
    "detail": {"visits": 3, "device_key": "kid-phone-0000000001", "app_version": "1.9.0"},
}]}
CONCERNS = {"status": "SUCCESS", "concerns": [{
    "category": "bullying", "level": "high", "excerpt": "...", "review": {"by": "model", "reason": "r", "model": "qwen3"},
}]}

# What identity's visibility rule answers for each viewer of "kid".
RULE = {
    "dad": {"allowed": True, "technical": True},
    "boss": {"allowed": True, "technical": True},  # an admin
    "mom": {"allowed": True, "technical": False},
    "sib": {"allowed": False, "technical": False},
    "kid": {"allowed": False, "technical": True},  # a minor does not browse their own stream
}


@pytest.fixture
def as_viewer(monkeypatch):
    acting = {"as": "dad"}
    calls: list[str] = []

    async def _who(_request):
        return {"user": acting["as"]}

    class _Resp:
        def __init__(self, status, body):
            self.status, self._body = status, body

        async def json(self, content_type=None):
            return self._body

        async def text(self):
            return ""

    class _Client:
        async def get(self, url, **kw):
            calls.append(url)
            if "may-view-raw" in url:
                return _Resp(200, RULE[kw["params"]["viewer"]])
            if "/usage/" in url:
                return _Resp(200, USAGE)
            if "/activity/" in url:
                return _Resp(200, ACTIVITY)
            return _Resp(200, CONCERNS)

    @asynccontextmanager
    async def fake():
        yield _Client()

    monkeypatch.setattr(gateway_main, "_resolve_identity_from_request", _who)
    monkeypatch.setattr(gateway_main, "borrow_http_client", fake)

    def act(name):
        acting["as"] = name
        return TestClient(app)

    return act, calls


@pytest.mark.parametrize("viewer", ["dad", "boss"])
def test_the_father_and_an_admin_see_the_technical_side(as_viewer, viewer):
    act, _ = as_viewer
    day = act(viewer).get("/api/usage/kid").json()["days"][0]
    assert day["source"] == "phone" and day["updated_at"]
    event = act(viewer).get("/api/guardian/activity/kid").json()["events"][0]
    assert event["detail"]["device_key"] == "kid-phone-0000000001"
    concern = act(viewer).get("/api/guardian/concerns/kid").json()["concerns"][0]
    assert concern["review"]["model"] == "qwen3"


def test_the_mother_sees_every_personal_detail_without_the_technical_side(as_viewer):
    act, _ = as_viewer
    day = act("mom").get("/api/usage/kid").json()["days"][0]
    assert "source" not in day and "updated_at" not in day
    # Personal data stays: screen time, first and last use, sessions, the apps.
    assert (day["millis"], day["sessions"], day["apps"][0]["millis"]) == (5_400_000, 9, 2_400_000)
    event = act("mom").get("/api/guardian/activity/kid").json()["events"][0]
    assert event["item"] == "example.org" and event["detail"] == {"visits": 3}
    concern = act("mom").get("/api/guardian/concerns/kid").json()["concerns"][0]
    assert concern["review"] == {"by": "model", "reason": "r"}


@pytest.mark.parametrize("viewer", ["sib", "kid"])
def test_a_sibling_and_the_child_themselves_are_refused_before_telemetry(as_viewer, viewer):
    act, calls = as_viewer
    for path in ("/api/usage/kid", "/api/guardian/activity/kid", "/api/guardian/concerns/kid", "/api/guardian/recap/kid"):
        assert act(viewer).get(path).status_code == 403
    assert all("telemetry" not in url for url in calls)


@pytest.fixture
def identity(monkeypatch):
    answers: dict = {}
    calls: list[dict] = []

    async def _as_caller(request, method, path, **kw):
        calls.append({"method": method, "path": path, **kw})
        return answers.get(path, (200, {}))

    class _Resp:
        status = 200

        async def json(self, content_type=None):
            return {"days": [{"day": "2026-10-21", "millis": 0, "apps": []}]}

    class _Client:
        async def get(self, url, **kw):
            return _Resp()

    @asynccontextmanager
    async def fake():
        yield _Client()

    monkeypatch.setattr(gateway_main, "_identity_as_caller", _as_caller)
    monkeypatch.setattr(gateway_main, "borrow_http_client", fake)
    monkeypatch.setattr(gateway_main, "_household_now", lambda: datetime(2026, 10, 21, 16, 0, tzinfo=ZoneInfo("America/Phoenix")))
    return TestClient(app), answers, calls


def test_devices_are_the_fathers_and_the_mother_is_refused(identity):
    client, answers, calls = identity
    answers["/api/family/devices/kid"] = (200, {"devices": [{"device_key": "kid-phone-0000000001", "app_version": "1.9.0"}]})
    assert client.get("/api/family/member/kid/devices").json()["devices"][0]["app_version"] == "1.9.0"
    assert calls[-1]["path"] == "/api/family/devices/kid"  # identity checks the caller, not the gateway

    answers["/api/family/devices/kid"] = (403, {"detail": "Only the father or an admin can see device details"})
    assert client.get("/api/family/member/kid/devices").status_code == 403


def test_the_status_drops_guard_and_approval_states_for_the_mother(identity):
    client, answers, _ = identity
    rules = {"rules": {"daily_limit_min": 120}, "can_manage": True, "protected": True,
             "approvals": {"apps": {"com.example.game": "pending"}}}
    answers["/api/screen-time/kid/rules"] = (200, {**rules, "technical": True})
    full = client.get("/api/screen-time/kid/status").json()
    assert full["protected"] is True and full["app_approvals"] == {"com.example.game": "pending"}

    answers["/api/screen-time/kid/rules"] = (200, {**rules, "technical": False})
    mine = client.get("/api/screen-time/kid/status").json()
    assert "protected" not in mine and "app_approvals" not in mine and "permission_approvals" not in mine
    assert mine["limit_min"] == 120 and mine["can_manage"] is True  # every control stays
    assert (full["technical"], mine["technical"]) == (True, False)  # the page hides Devices for her


def test_stripping_reaches_every_depth_and_keeps_personal_fields():
    body = {"a": [{"device_key": "x", "name": "Pixel", "nested": {"ip": "1.2.3.4", "steps": 10}}], "token": "t"}
    assert strip_technical(body) == {"a": [{"name": "Pixel", "nested": {"steps": 10}}]}

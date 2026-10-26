"""Screen-time routes: status from identity's rules + today's use; requests tell people."""
from contextlib import asynccontextmanager
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from services.gateway import main as gateway_main
from services.gateway.main import app

NOW = datetime(2026, 10, 21, 16, 0, tzinfo=ZoneInfo("America/Phoenix"))  # a Wednesday


@pytest.fixture
def wired(monkeypatch):
    calls: list[dict] = []
    sent: list[dict] = []
    answers: dict = {}

    class _Resp:
        def __init__(self, status, body):
            self.status, self._body = status, body

        async def json(self, content_type=None):
            return self._body

        async def text(self):
            return ""

    class _Client:
        async def request(self, method, url, **kw):
            calls.append({"method": method, "url": url, **kw})
            return _Resp(*answers.get((method, url.split("/api", 1)[1]), (200, {})))

        async def get(self, url, **kw):
            calls.append({"method": "GET", "url": url, **kw})
            return _Resp(200, {"days": [{"day": "2026-10-21", "millis": 100 * 60000,
                                         "apps": [{"package": "com.google.android.youtube", "millis": 40 * 60000}]}]})

    @asynccontextmanager
    async def fake():
        yield _Client()

    async def fake_send(payload, **kw):
        sent.append(payload)
        return True

    monkeypatch.setattr(gateway_main, "borrow_http_client", fake)
    monkeypatch.setattr(gateway_main._notify, "send", fake_send)
    monkeypatch.setattr(gateway_main, "_household_now", lambda: NOW)
    return TestClient(app, headers={"Authorization": "Bearer kid-key"}), calls, sent, answers


def test_status_combines_rules_bonus_and_todays_use(wired):
    client, calls, _, answers = wired
    answers[("GET", "/screen-time/kid/rules")] = (200, {"rules": {"daily_limit_min": 120}, "bonus_min": 10, "can_manage": False})

    body = client.get("/api/screen-time/kid/status").json()

    assert (body["limit_min"], body["used_min"], body["bonus_min"], body["remaining_min"]) == (120, 100, 10, 30)
    assert body["locked"] is False
    rules_call = calls[0]
    assert rules_call["headers"]["Authorization"] == "Bearer kid-key"  # identity checks the caller
    assert rules_call["params"] == {"day": "2026-10-21"}


def test_status_is_refused_when_identity_refuses(wired):
    client, _, _, answers = wired
    answers[("GET", "/screen-time/kid/rules")] = (403, {"detail": "Only a parent can see this"})
    assert client.get("/api/screen-time/kid/status").status_code == 403


def test_asking_for_time_tells_the_parents(wired):
    client, calls, sent, answers = wired
    answers[("POST", "/screen-time/requests")] = (
        200,
        {"request": {"id": 3, "username": "kid", "minutes": 30, "note": "one more level"}, "notify": ["dad", "mom"]},
    )

    r = client.post("/api/screen-time/requests", json={"minutes": 30, "note": "one more level"})

    assert r.status_code == 200
    assert calls[0]["json"]["day"] == "2026-10-21"  # the household's day, not the phone's
    assert [n["user"] for n in sent] == ["dad", "mom"]
    assert "30 more minutes" in sent[0]["body"] and "one more level" in sent[0]["body"]
    assert sent[0]["actions"] == [
        {"label": "Approve", "type": "callback", "callback": "more_time_decide", "params": {"id": 3, "grant": True}},
        {"label": "Disapprove", "type": "callback", "callback": "more_time_decide", "params": {"id": 3, "grant": False}},
    ]


def test_a_refused_request_tells_nobody(wired):
    client, _, sent, answers = wired
    answers[("POST", "/screen-time/requests")] = (409, {"detail": "You already asked"})
    assert client.post("/api/screen-time/requests", json={"minutes": 30}).status_code == 409
    assert sent == []


def test_tamper_reports_tell_the_parents(wired, monkeypatch):
    client, calls, sent, _ = wired

    async def _who(_request):
        return {"user": "kid"}

    monkeypatch.setattr(gateway_main, "_resolve_identity_from_request", _who)

    class _Resp:
        status = 200

        async def json(self, content_type=None):
            return {"parents": ["dad", "mom"], "display_name": "Zachary"}

    class _Client:
        async def post(self, url, **kw):
            calls.append({"url": url, **kw})
            return _Resp()

    @asynccontextmanager
    async def fake():
        yield _Client()

    monkeypatch.setattr(gateway_main, "borrow_http_client", fake)

    assert client.post("/api/guard/tamper", json={"event": "admin_disabled", "device_key": "k" * 16}).status_code == 200
    # Identity keeps the report for the father's Devices section and says who to tell.
    assert calls[-1]["url"].endswith("/api/internal/guard/tamper/kid")
    assert calls[-1]["json"] == {"event": "admin_disabled", "device_key": "k" * 16}
    assert [n["user"] for n in sent] == ["dad", "mom"]
    assert "Zachary's phone turned off Jarvis's device protection" in sent[0]["body"]
    assert client.post("/api/guard/tamper", json={"event": "nonsense"}).status_code == 422
    assert client.post("/api/guard/tamper", json={"event": "offline_unlock"}).status_code == 200
    assert "unlocked with a parent's offline code" in sent[-1]["body"]


def test_ways_around_the_web_filter_are_reported_with_what_they_were(wired, monkeypatch):
    client, calls, sent, _ = wired

    async def _who(_request):
        return {"user": "kid"}

    monkeypatch.setattr(gateway_main, "_resolve_identity_from_request", _who)

    class _Resp:
        status = 200

        async def json(self, content_type=None):
            return {"parents": ["dad"], "display_name": "Zachary"}

    class _Client:
        async def post(self, url, **kw):
            calls.append({"url": url, **kw})
            return _Resp()

    @asynccontextmanager
    async def fake():
        yield _Client()

    monkeypatch.setattr(gateway_main, "borrow_http_client", fake)

    assert client.post("/api/guard/tamper", json={"event": "bypass_app", "detail": "Tor  Browser"}).status_code == 200
    assert calls[-1]["json"] == {"event": "bypass_app", "device_key": "", "detail": "Tor Browser"}
    assert "gets around the web filter (closed while the filter is on): Tor Browser." in sent[-1]["body"]
    for event in ("private_dns", "private_dns_attempt", "other_vpn", "boot_without_guard"):
        assert client.post("/api/guard/tamper", json={"event": event}).status_code == 200
    assert "safe mode" in sent[-1]["body"]
    # A long detail is cut, never passed whole.
    client.post("/api/guard/tamper", json={"event": "private_dns", "detail": "x" * 500})
    assert len(calls[-1]["json"]["detail"]) == 80


def test_a_dropped_tamper_report_tells_nobody(wired, monkeypatch):
    client, calls, sent, _ = wired

    async def _who(_request):
        return {"user": "grown"}

    monkeypatch.setattr(gateway_main, "_resolve_identity_from_request", _who)

    class _Resp:
        status = 200

        async def json(self, content_type=None):
            # Identity dropped it: a grown member's own device is not monitored.
            return {"username": "grown", "parents": [], "notify": [], "display_name": "Alex", "dropped": "not_minor"}

    class _Client:
        async def post(self, url, **kw):
            calls.append({"url": url, **kw})
            return _Resp()

    @asynccontextmanager
    async def fake():
        yield _Client()

    monkeypatch.setattr(gateway_main, "borrow_http_client", fake)

    r = client.post("/api/guard/tamper", json={"event": "guard_disabled"})
    assert r.status_code == 200 and r.json()["dropped"] == "not_minor"  # explicit, not a kept report
    assert calls[-1]["url"].endswith("/api/internal/guard/tamper/grown")
    assert sent == []  # an empty list: nobody is notified


def test_a_parent_unlock_is_never_silent(wired, monkeypatch):
    client, _, sent, answers = wired

    async def _who(_request):
        return {"user": "kid"}

    monkeypatch.setattr(gateway_main, "_resolve_identity_from_request", _who)
    answers[("POST", "/guard/parent-unlock")] = (200, {"ok": True, "parent": "mom", "until": "x", "notify": ["dad", "mom"]})

    r = client.post("/api/guard/parent-unlock", json={"username": "mom", "password": "p"})

    assert r.status_code == 200 and "notify" not in r.json()
    assert [n["user"] for n in sent] == ["dad", "mom"] and "mom unlocked" in sent[0]["body"]


def test_lane_changes_go_to_identity_as_the_caller(wired):
    client, calls, _, answers = wired
    answers[("PUT", "/recommendation-lanes/kid")] = (403, {"detail": "Only a parent can change suggestions"})
    r = client.put("/api/recommendation-lanes/kid", json={"lanes": {"game": False}})
    assert r.status_code == 403
    assert calls[0]["headers"]["Authorization"] == "Bearer kid-key" and calls[0]["json"] == {"lanes": {"game": False}}


def test_a_self_pause_reads_as_you_and_a_parents_pause_as_a_parent(wired, monkeypatch):
    client, _, sent, answers = wired
    actor = {"user": "grown"}

    async def _who(_request):
        return actor

    monkeypatch.setattr(gateway_main, "_resolve_identity_from_request", _who)

    # A grown member pausing their own phone.
    answers[("POST", "/screen-time/grown/pause")] = (200, {"username": "grown", "rules": {"paused": True}})
    assert client.post("/api/screen-time/grown/pause", json={}).status_code == 200
    assert sent[-1]["title"] == "Phone paused"
    assert sent[-1]["body"] == "You paused your phone for now."

    # A parent pausing a child's phone.
    sent.clear()
    actor["user"] = "dad"
    answers[("POST", "/screen-time/kid/pause")] = (200, {"username": "kid", "rules": {"paused": True}})
    assert client.post("/api/screen-time/kid/pause", json={}).status_code == 200
    assert sent[-1]["title"] == "Phone paused"
    assert sent[-1]["body"] == "A parent paused your phone for now."

    # Resuming reads the same either way.
    sent.clear()
    actor["user"] = "grown"
    answers[("POST", "/screen-time/grown/pause")] = (200, {"username": "grown", "rules": {}})
    assert client.post("/api/screen-time/grown/pause", json={"resume": True}).status_code == 200
    assert sent[-1]["body"] == "Your phone is back on."


def test_a_refused_pause_tells_nobody(wired):
    client, _, sent, answers = wired
    answers[("POST", "/screen-time/kid/pause")] = (403, {"detail": "Only a parent can pause a minor's phone"})
    assert client.post("/api/screen-time/kid/pause", json={}).status_code == 403
    assert sent == []


def test_a_lock_is_only_audited_when_the_phone_is_a_device_owner(wired, monkeypatch):
    client, _, _, answers = wired
    audits: list[tuple] = []
    monkeypatch.setattr(gateway_main.mdm, "record_audit",
                        lambda action, actor, **kw: audits.append((action, actor, kw)))

    async def _who(_request):
        return {"user": "dad"}

    monkeypatch.setattr(gateway_main, "_resolve_identity_from_request", _who)

    answers[("POST", "/screen-time/kid/pause")] = (200, {"username": "kid", "rules": {"paused": True}})
    # The phone has not reported device owner: the "lock" is only the soft cover,
    # so it is not logged as a real lock.
    answers[("GET", "/family/devices/kid")] = (200, {"devices": [{"capabilities": {"guard": {"deviceOwner": False}}}]})
    assert client.post("/api/screen-time/kid/pause", json={}).status_code == 200
    assert audits == []

    # A device-owner phone: the lock is real, so it is logged.
    answers[("GET", "/family/devices/kid")] = (200, {"devices": [{"capabilities": {"guard": {"deviceOwner": True}}}]})
    assert client.post("/api/screen-time/kid/pause", json={}).status_code == 200
    assert [a[0] for a in audits] == ["device_lock"]

    # Unlocking is always logged: it lifts whatever lock there was.
    answers[("POST", "/screen-time/kid/pause")] = (200, {"username": "kid", "rules": {}})
    assert client.post("/api/screen-time/kid/pause", json={"resume": True}).status_code == 200
    assert [a[0] for a in audits] == ["device_lock", "device_unlock"]


def test_status_blocks_an_app_past_its_own_minutes(wired):
    client, _, _, answers = wired
    answers[("GET", "/screen-time/kid/rules")] = (200, {"rules": {"app_limits": {"com.google.android.youtube": 30}}})
    body = client.get("/api/screen-time/kid/status").json()
    assert body["blocked_apps"] == ["com.google.android.youtube"]
    assert body["app_minutes_left"] == {"com.google.android.youtube": 0}
    assert body["locked"] is False


def test_offline_code_routes_go_to_identity_as_the_caller(wired):
    client, calls, _, answers = wired
    answers[("PUT", "/guard/offline-code")] = (200, {"set": True, "updated_at": "x"})
    r = client.put("/api/guard/offline-code", json={"code": "48291735"})
    assert r.status_code == 200 and r.json()["set"] is True
    assert calls[-1]["headers"]["Authorization"] == "Bearer kid-key"
    assert calls[-1]["json"] == {"code": "48291735"}


def test_the_unlock_passes_offline_verifiers_to_the_phone(wired, monkeypatch):
    client, _, _, answers = wired

    async def _who(_request):
        return {"user": "kid"}

    monkeypatch.setattr(gateway_main, "_resolve_identity_from_request", _who)
    verifier = {"parent": "mom", "salt": "aa", "iterations": 120000, "hash": "bb"}
    answers[("POST", "/guard/parent-unlock")] = (200, {"ok": True, "parent": "mom", "until": "x", "notify": [], "offline_codes": [verifier]})
    assert client.post("/api/guard/parent-unlock", json={"username": "mom", "password": "p"}).json()["offline_codes"] == [verifier]


def test_status_passes_the_parents_answers_and_the_next_lock_to_the_phone(wired):
    client, _, _, answers = wired
    answers[("GET", "/screen-time/kid/rules")] = (200, {
        "rules": {"bedtime_start": "21:00", "bedtime_end": "07:00", "permission_gating": ["camera"]},
        "approvals": {"apps": {"com.roblox.client": "pending"}, "permissions": {"com.snapchat.android|camera": "approved"}},
    })
    body = client.get("/api/screen-time/kid/status").json()
    assert body["app_approvals"] == {"com.roblox.client": "pending"}
    assert body["permission_approvals"] == {"com.snapchat.android|camera": "approved"}
    assert body["install_approval"] is True and body["permission_gating"] == ["camera"]
    assert (body["next_lock_reason"], body["next_lock_at"]) == ("bedtime", "2026-10-21T21:00:00-07:00")


def test_asking_a_parent_first_tells_both_parents_once(wired):
    client, _, sent, answers = wired
    answers[("POST", "/guard/requests")] = (200, {
        "request": {"id": 7, "username": "kid", "kind": "site", "target": "minecraft.net", "label": "", "note": "school project"},
        "notify": ["dad", "mom"],
    })
    r = client.post("/api/guard/requests", json={"kind": "site", "target": "minecraft.net", "note": "school project"})
    assert r.status_code == 200 and "notify" not in r.json()
    assert [n["user"] for n in sent] == ["dad", "mom"]
    assert "kid asks to open minecraft.net" in sent[0]["body"] and "school project" in sent[0]["body"]
    assert sent[0]["actions"] == [
        {"label": "Approve", "type": "callback", "callback": "guard_decide", "params": {"id": 7, "approve": True}},
        {"label": "Disapprove", "type": "callback", "callback": "guard_decide", "params": {"id": 7, "approve": False}},
    ]

    sent.clear()
    answers[("POST", "/guard/requests")] = (200, {"request": {"username": "kid", "kind": "app", "target": "com.roblox.client"}, "notify": []})
    client.post("/api/guard/requests", json={"kind": "app", "target": "com.roblox.client"})
    assert sent == []  # asked before: the parents already heard


def test_a_permission_request_reads_in_plain_words_and_the_answer_reaches_the_child(wired):
    client, _, sent, answers = wired
    answers[("POST", "/guard/requests")] = (200, {
        "request": {"username": "kid", "kind": "permission", "target": "com.snapchat.android|camera", "label": "Snapchat"},
        "notify": ["mom"],
    })
    client.post("/api/guard/requests", json={"kind": "permission", "target": "com.snapchat.android|camera"})
    assert "kid wants to give Snapchat access to the camera" in sent[0]["body"]

    answers[("POST", "/guard/requests/7")] = (200, {
        "request": {"username": "kid", "kind": "site", "target": "minecraft.net", "status": "approved", "until": "x"},
        "notify": ["kid"],
    })
    assert client.post("/api/guard/requests/7", json={"approve": True, "minutes": 60}).status_code == 200
    assert sent[-1]["user"] == "kid" and sent[-1]["title"] == "Website allowed"


def test_a_parents_answer_to_a_more_time_request_reaches_the_child(wired):
    client, calls, sent, answers = wired
    answers[("POST", "/screen-time/requests/3")] = (
        200, {"request": {"username": "kid", "status": "granted", "granted_minutes": 30}, "notify": ["kid"]})
    r = client.post("/api/screen-time/requests/3", json={"grant": True})
    assert r.status_code == 200
    assert calls[-1]["url"].endswith("/api/screen-time/requests/3") and calls[-1]["json"] == {"grant": True}
    assert sent[-1]["user"] == "kid" and sent[-1]["title"] == "More time granted"
    assert "30 more minutes" in sent[-1]["body"]

"""A locked device asks a parent to unlock it: both parents get Approve / Deny,
and the answer replaces the notice on every parent's phone."""
import asyncio
from contextlib import asynccontextmanager

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from services.gateway import main as gateway_main
from services.gateway import push_callbacks
from services.gateway.main import app


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

    @asynccontextmanager
    async def fake():
        yield _Client()

    async def fake_send(payload, **kw):
        sent.append(payload)
        return True

    monkeypatch.setattr(gateway_main, "borrow_http_client", fake)
    monkeypatch.setattr(gateway_main._notify, "send", fake_send)
    return TestClient(app, headers={"Authorization": "Bearer wg_test-pc-key"}), calls, sent, answers


class _ParentTap:
    """The request a parent's notice button arrives with: their own key."""

    def __init__(self):
        self.headers = {"Authorization": "Bearer mom-own-key"}


ASKED = {
    "id": 7, "username": "kid", "device": "PC", "lock_reason": "bedtime", "note": "homework",
    "minutes_asked": 30, "status": "pending",
}


def test_a_pc_asking_reaches_both_parents_with_approve_and_deny(wired):
    client, calls, sent, answers = wired
    answers[("POST", "/guard/unlock-requests")] = (200, {"request": ASKED, "notify": ["dad", "mom"], "display_name": "Kid"})

    r = client.post("/api/guard/unlock-requests", json={"minutes": 30, "note": "homework", "lock_reason": "bedtime"})

    assert r.status_code == 200 and "notify" not in r.json() and r.json()["request"]["id"] == 7
    # Identity is asked as the caller: it checks the device key itself.
    assert calls[0]["headers"]["Authorization"] == "Bearer wg_test-pc-key"
    assert calls[0]["json"] == {"minutes": 30, "note": "homework", "lock_reason": "bedtime"}
    assert [n["user"] for n in sent] == ["dad", "mom"]
    notice = sent[0]
    assert notice["title"] == "Unlock Kid's PC?"
    assert "30 minutes at bedtime" in notice["body"] and "homework" in notice["body"]
    assert notice["data"]["url"] == "/family/member/kid"
    assert notice["tag"] == "guard-unlock:kid" and notice["replace"] is True
    assert notice["actions"] == [
        {"label": "Approve 30 min", "type": "callback", "callback": "guard_unlock_decide",
         "params": {"id": 7, "approve": True, "minutes": 30}},
        {"label": "Deny", "type": "callback", "callback": "guard_unlock_decide", "params": {"id": 7, "approve": False}},
    ]


def test_a_refused_request_tells_nobody(wired):
    client, _, sent, answers = wired
    answers[("POST", "/guard/unlock-requests")] = (429, {"detail": "You just asked. Wait a moment before asking again."})
    r = client.post("/api/guard/unlock-requests", json={"minutes": 30})
    assert r.status_code == 429 and "Wait" in r.json()["detail"]
    assert sent == []


def test_the_device_polls_its_own_request_through_identity(wired):
    client, calls, _, answers = wired
    answers[("GET", "/guard/unlock-requests/7")] = (200, {"request": {**ASKED, "status": "approved", "until": "x"}, "offline_codes": []})
    r = client.get("/api/guard/unlock-requests/7?device_key=phone-kid")
    assert r.status_code == 200 and r.json()["request"]["status"] == "approved"
    assert calls[0]["params"] == {"device_key": "phone-kid"}
    assert calls[0]["headers"]["Authorization"] == "Bearer wg_test-pc-key"
    answers[("GET", "/guard/unlock-requests/8")] = (404, {"detail": "No such request"})
    assert client.get("/api/guard/unlock-requests/8").status_code == 404
    assert calls[-1]["params"] is None


def test_an_approval_replaces_every_parents_notice(wired):
    client, calls, sent, answers = wired
    answers[("POST", "/guard/unlock-requests/7")] = (200, {
        "request": {**ASKED, "status": "approved", "minutes_granted": 20, "decided_by": "mom", "until": "x"},
        "notify": ["dad", "mom"], "display_name": "Kid",
    })
    r = client.post("/api/guard/unlock-requests/7", json={"approve": True, "minutes": 20})
    assert r.status_code == 200 and "notify" not in r.json()
    assert calls[0]["json"] == {"approve": True, "minutes": 20}
    assert [n["user"] for n in sent] == ["dad", "mom"]
    assert sent[0]["title"] == "Kid's PC unlocked" and "mom unlocked Kid's PC for 20 minutes" in sent[0]["body"]
    assert sent[0]["tag"] == "guard-unlock:kid" and not sent[0].get("actions")


def test_a_denial_is_told_and_a_refusal_is_passed_on(wired):
    client, _, sent, answers = wired
    answers[("POST", "/guard/unlock-requests/7")] = (200, {
        "request": {**ASKED, "status": "denied", "decided_by": "dad"}, "notify": ["dad", "mom"], "display_name": "Kid",
    })
    client.post("/api/guard/unlock-requests/7", json={"approve": False})
    assert sent[0]["title"] == "Kid's PC stays locked" and "dad said no" in sent[0]["body"]
    sent.clear()
    answers[("POST", "/guard/unlock-requests/7")] = (403, {"detail": "A device cannot answer an unlock request."})
    assert client.post("/api/guard/unlock-requests/7", json={"approve": True}).status_code == 403
    assert sent == []


def test_the_notice_button_answers_as_the_parent_who_tapped(wired):
    _, calls, _, answers = wired
    answers[("POST", "/guard/unlock-requests/7")] = (200, {
        "request": {**ASKED, "status": "approved", "minutes_granted": 30, "decided_by": "mom"}, "notify": [], "display_name": "Kid",
    })

    body = asyncio.run(push_callbacks.PUSH_CALLBACKS["guard_unlock_decide"](_ParentTap(), {"id": 7, "approve": True, "minutes": 30}, None))
    assert body["request"]["status"] == "approved"
    assert calls[0]["headers"]["Authorization"] == "Bearer mom-own-key"
    assert calls[0]["json"] == {"approve": True, "minutes": 30}
    asyncio.run(push_callbacks.PUSH_CALLBACKS["guard_unlock_decide"](_ParentTap(), {"id": 7, "approve": False, "minutes": 30}, None))
    assert calls[-1]["json"] == {"approve": False}


@pytest.mark.parametrize("params", [{"approve": True}, {"id": "x", "approve": True}, {"id": 7}, {"id": 7, "approve": "yes"}])
def test_the_notice_button_refuses_a_malformed_tap(params):
    with pytest.raises(HTTPException) as err:
        asyncio.run(push_callbacks.PUSH_CALLBACKS["guard_unlock_decide"](object(), params, None))
    assert err.value.status_code == 422


def test_the_notice_button_passes_identitys_refusal_on(wired):
    _, _, _, answers = wired
    answers[("POST", "/guard/unlock-requests/7")] = (409, {"detail": "This request has expired. The device can ask again."})

    with pytest.raises(HTTPException) as err:
        asyncio.run(push_callbacks.PUSH_CALLBACKS["guard_unlock_decide"](_ParentTap(), {"id": 7, "approve": True}, None))
    assert err.value.status_code == 409 and "expired" in err.value.detail


def test_the_password_unlock_route_is_gone(wired):
    client, _, _, _ = wired
    assert client.post("/api/guard/parent-unlock", json={"username": "mom", "password": "x"}).status_code in (404, 405)

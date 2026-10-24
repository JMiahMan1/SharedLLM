"""Place reminders through the gateway: always the caller's own (docs/NOTES.md)."""
import pytest
from fastapi.testclient import TestClient

from services.gateway import family_safety


class _Resp:
    def __init__(self, status, body):
        self.status = status
        self._body = body

    async def json(self, content_type=None):
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


@pytest.fixture
def proxy(monkeypatch):
    from services.gateway import main

    calls = []
    answer = {"status": 200, "body": {"reminders": []}}

    async def caller(request):
        return "leo"

    class Client:
        def request(self, method, url, params=None, json=None, **kw):
            calls.append((method, url, params, json))
            return _Resp(answer["status"], answer["body"])

    monkeypatch.setattr(main, "_require_authenticated", caller)
    monkeypatch.setattr(family_safety, "get_client", lambda: Client())
    return TestClient(main.app), calls, answer


def test_a_reminder_is_always_set_for_the_caller(proxy):
    client, calls, answer = proxy
    answer["body"] = {"reminder": {"id": "a" * 32}}
    res = client.post("/api/geo/place-reminders", json={
        "zone": "zone.home", "on": "arrive", "title": "Bins", "url": "/notes", "user": "michele",
    })
    assert res.status_code == 200
    method, url, params, body = calls[0]
    assert (method, params) == ("POST", {"user": "leo"})
    assert url.endswith("/place-reminders")
    assert body == {"zone": "zone.home", "on": "arrive", "title": "Bins", "url": "/notes"}


def test_list_and_remove_are_the_callers_and_geo_refusals_pass_through(proxy):
    client, calls, answer = proxy
    assert client.get("/api/geo/place-reminders").json() == {"reminders": []}
    assert calls[-1][2] == {"user": "leo"}
    answer.update(status=404, body={"detail": "No such place reminder (it may have gone off already)"})
    res = client.delete(f"/api/geo/place-reminders/{'b' * 32}")
    assert res.status_code == 404 and "gone off" in res.json()["detail"]
    assert calls[-1][:3][0] == "DELETE" and calls[-1][2] == {"user": "leo"}


def test_a_body_that_is_not_an_object_is_refused(proxy):
    client, calls, _ = proxy
    assert client.post("/api/geo/place-reminders", json=["zone.home"]).status_code == 422
    assert calls == []

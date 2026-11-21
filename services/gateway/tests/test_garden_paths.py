"""Client ids never steer a Garden call to another route.

Every Garden call carries ``X-Internal-Secret``. Plant, room and plan ids come
from clients (a digest button's params, a route's path parameter), so an id such
as ``../hooks/weather#`` used to reach the weather hook with the secret. These
tests drive the real ``garden_routes._call`` (only the HTTP client underneath is
replaced) and check the URL that would have gone on the wire.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import pytest
import yarl
from fastapi import HTTPException
from fastapi.testclient import TestClient

from services.gateway import garden_assist, garden_routes, push_callbacks
from services.gateway import main as gateway_main


class _Resp:
    status = 200

    async def json(self, content_type=None):
        return {"ok": True}

    async def read(self):
        return b""


class _Client:
    def __init__(self, sent):
        self.sent = sent

    @asynccontextmanager
    async def request(self, method, url, **_kw):
        self.sent.append((method, yarl.URL(url)))
        yield _Resp()

    def get(self, url, **kw):
        return self.request("GET", url, **kw)


@pytest.fixture()
def wire(monkeypatch):
    sent: list[tuple[str, yarl.URL]] = []

    @asynccontextmanager
    async def shared_http_client():
        yield _Client(sent)

    async def headers(_request):
        return {"X-User-Id": "parent_one"}

    async def acting_identity(_request):
        return {"user": "parent_one", "is_admin": False, "family_role": "mother"}

    monkeypatch.setattr(gateway_main, "shared_http_client", shared_http_client)
    monkeypatch.setattr(garden_routes, "_verified_headers", headers)
    monkeypatch.setattr(gateway_main, "_acting_identity", acting_identity)
    return sent


HOSTILE_IDS = ("../hooks/weather#", "../hooks/weather?x=1", "p-1?source=homehub_valve", "a/b", "p#frag")


@pytest.mark.parametrize("plant_id", HOSTILE_IDS)
def test_a_digest_button_keeps_a_hostile_id_inside_one_segment(wire, plant_id):
    asyncio.run(push_callbacks.PUSH_CALLBACKS["garden_done"](object(), {"plant_id": plant_id}, None))

    (method, url), = wire
    assert method == "POST"
    assert url.raw_path.startswith("/plants/") and url.raw_path.endswith("/care")
    assert url.raw_path.count("/") == 3, url.raw_path
    assert url.query_string == "" and url.fragment == ""
    assert "hooks" not in url.path.split("/")[1]


def test_dot_dot_alone_is_refused_before_any_call(wire):
    with pytest.raises(HTTPException) as caught:
        asyncio.run(push_callbacks.PUSH_CALLBACKS["garden_done"](object(), {"plant_id": ".."}, None))

    assert caught.value.status_code == 400
    assert wire == []


def test_a_batch_reports_the_refused_id_and_carries_on(wire):
    body = asyncio.run(
        push_callbacks.PUSH_CALLBACKS["garden_snooze"](object(), {"plant_ids": ["..", "p-2"]}, None)
    )

    assert body["done"] == 1 and body["failed"] == 1
    assert body["results"][0] == {"plant_id": "..", "ok": False, "status": 400, "error": "That id is not valid."}
    assert [url.raw_path for _m, url in wire] == ["/plants/p-2/snooze"]


@pytest.mark.parametrize("path", ("/plants/../hooks/weather", "/plants/p?x=1", "/plants/p#x", "plants/p", "/a\\b"))
def test_call_refuses_a_path_that_could_leave_its_route(wire, path):
    with pytest.raises(HTTPException) as caught:
        asyncio.run(garden_routes._call("GET", path))

    assert caught.value.status_code == 400
    assert wire == []


def test_the_shared_assist_client_refuses_them_too():
    for path in ("/plants/../hooks/weather", "/x?y", "/x#y"):
        with pytest.raises(ValueError):
            garden_assist.checked_path(path)


def test_a_route_path_parameter_is_encoded_on_the_way_through(wire):
    client = TestClient(gateway_main.app)

    resp = client.get("/api/garden/plants/p%3Fsource%3Dhack/logs", headers={"Authorization": "Bearer x"})

    assert resp.status_code == 200, resp.text
    (_method, url), = wire
    assert url.path == "/plants/p?source=hack/logs" and url.raw_path.startswith("/plants/p%3F")
    assert url.query.get("source") is None

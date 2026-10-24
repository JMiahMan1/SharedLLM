"""Nextcloud session cookies are kept per login, never shared between logins."""

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from services.execution import http_client


@pytest.fixture()
async def server():
    seen: list[dict] = []
    revoked: set[str] = set()

    async def handler(request: web.Request) -> web.Response:
        user = request.headers.get("Authorization", "")
        cookie = request.cookies.get("nc_session")
        seen.append({"auth": user, "cookie": cookie})
        if cookie and cookie in revoked:
            return web.Response(status=401, text="expired")
        resp = web.Response(text="ok")
        if not cookie:
            resp.set_cookie("nc_session", f"session-for-{len(seen)}")
        return resp

    srv = TestServer(web.Application())
    srv.app.router.add_get("/ocs", handler)
    await srv.start_server()
    http_client._LOGIN_COOKIES.clear()
    yield srv, seen, revoked
    http_client._LOGIN_COOKIES.clear()
    await http_client.close_all_sessions()
    await srv.close()


async def test_the_same_login_reuses_its_session(server):
    srv, seen, _ = server
    url = str(srv.make_url("/ocs"))
    await http_client.request("GET", url, auth=("dad", "pw"), keep_login=True)
    await http_client.request("GET", url, auth=("dad", "pw"), keep_login=True)
    assert seen[0]["cookie"] is None
    assert seen[1]["cookie"] == "session-for-1"


async def test_a_session_never_rides_along_with_another_login(server):
    srv, seen, _ = server
    url = str(srv.make_url("/ocs"))
    await http_client.request("GET", url, auth=("dad", "pw"), keep_login=True)
    await http_client.request("GET", url, auth=("kiddo", "pw"), keep_login=True)
    await http_client.request("GET", url, auth=("dad", "new-password"), keep_login=True)
    await http_client.request("GET", url, auth=("dad", "pw"), keep_login=False)
    assert [s["cookie"] for s in seen] == [None, None, None, None]


async def test_a_dropped_session_logs_in_afresh_once(server):
    srv, seen, revoked = server
    url = str(srv.make_url("/ocs"))
    await http_client.request("GET", url, auth=("dad", "pw"), keep_login=True)
    revoked.add("session-for-1")

    resp = await http_client.request("GET", url, auth=("dad", "pw"), keep_login=True)

    assert resp["ok"]
    assert [s["cookie"] for s in seen] == [None, "session-for-1", None]


async def test_downloads_and_thumbnails_keep_the_login_too(server):
    srv, seen, _ = server
    url = str(srv.make_url("/ocs"))
    await http_client.fetch_bytes(url, auth=("dad", "pw"))
    status, _, body = await http_client.fetch_bytes(url, auth=("dad", "pw"))
    assert (status, body) == (200, b"ok")
    assert [s["cookie"] for s in seen] == [None, "session-for-1"]


async def test_logins_are_kept_by_default(server):
    srv, seen, _ = server
    url = str(srv.make_url("/ocs"))
    await http_client.request("GET", url, auth=("dad", "pw"))
    await http_client.request("GET", url, auth=("dad", "pw"))
    assert seen[1]["cookie"] == "session-for-1"

"""A caller hanging up mid-request is not a gateway error."""
from fastapi.testclient import TestClient
from starlette.requests import ClientDisconnect

from services.gateway import main


def test_a_client_that_hangs_up_is_a_499_not_a_logged_500(monkeypatch):
    emitted = []
    monkeypatch.setattr(main, "_run_in_background", lambda coro: emitted.append(coro) or coro.close())

    @main.app.get("/__test_disconnect")
    async def _hang_up():
        raise ClientDisconnect()

    try:
        r = TestClient(main.app).get("/__test_disconnect")
    finally:
        main.app.router.routes = [rt for rt in main.app.router.routes if getattr(rt, "path", "") != "/__test_disconnect"]
    assert r.status_code == 499
    assert emitted == []

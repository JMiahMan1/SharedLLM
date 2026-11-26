"""Music Assistant accounts: the logins people open Music Assistant itself with.

Music Assistant's administrator login lives on HomeHub's config entry and nowhere
else, so the gateway asks HomeHub instead of talking to Music Assistant itself.
No household member is named here: the accounts are read from Music Assistant at
the time of the call.
"""
import pytest

from services.gateway import main as gateway_main


def test_the_accounts_list_comes_from_homehub(client):
    seen = {}

    async def hub(method, path, *, json=None, **_kwargs):
        seen.update({"method": method, "path": path})
        return 200, {
            "users": [{"username": "robin", "role": "admin", "enabled": True}],
            "server": "http://music.test:8095",
        }

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("services.gateway.automation_routes._hub", hub)
        resp = client.get("/api/media/music-assistant/users")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert seen["method"] == "GET" and seen["path"] == "/hub/music_assistant/users"
    assert body["users"][0]["username"] == "robin"
    assert body["server"] == "http://music.test:8095"


def test_setting_a_password_passes_it_to_homehub_and_never_echoes_it(client):
    seen = {}

    async def hub(method, path, *, json=None, **_kwargs):
        seen.update({"method": method, "path": path, "body": json})
        return 200, {"username": "robin", "user_id": "u1", "changed": True}

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("services.gateway.automation_routes._hub", hub)
        resp = client.post(
            "/api/media/music-assistant/users/password",
            json={"username": "robin", "password": "long-enough"},
        )

    assert resp.status_code == 200, resp.text
    assert seen["method"] == "POST" and seen["path"] == "/hub/music_assistant/users/password"
    assert seen["body"] == {"username": "robin", "password": "long-enough"}
    assert "long-enough" not in resp.text
    assert resp.json()["changed"] is True


def test_a_password_homehub_refuses_keeps_homehubs_reason(client):
    async def hub(method, path, *, json=None, **_kwargs):
        return 404, {"detail": "Music Assistant has no account called nobody"}

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("services.gateway.automation_routes._hub", hub)
        resp = client.post(
            "/api/media/music-assistant/users/password",
            json={"username": "nobody", "password": "long-enough"},
        )

    assert resp.status_code == 404
    assert "no account called nobody" in resp.json()["message"]


def test_a_homehub_that_is_down_is_a_502_that_says_so(client):
    async def hub(*_args, **_kwargs):
        raise RuntimeError("down")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("services.gateway.automation_routes._hub", hub)
        assert client.get("/api/media/music-assistant/users").status_code == 502
        resp = client.post(
            "/api/media/music-assistant/users/password",
            json={"username": "robin", "password": "long-enough"},
        )

    assert resp.status_code == 502
    assert "HomeHub unreachable" in resp.json()["message"]


def test_a_username_and_a_password_are_required(client):
    resp = client.post("/api/media/music-assistant/users/password", json={"username": "robin"})

    assert resp.status_code == 400
    assert "password" in resp.json()["message"].lower()


def test_the_accounts_are_for_administrators_only(client):
    async def not_admin(api_key):
        return {"user": "kid", "is_admin": False}

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(gateway_main, "_resolve_strict_identity", not_admin)
        assert client.get("/api/media/music-assistant/users").status_code == 403
        assert client.post(
            "/api/media/music-assistant/users/password",
            json={"username": "robin", "password": "long-enough"},
        ).status_code == 403

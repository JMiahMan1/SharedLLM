"""Setting every household member up on Audiobookshelf from one administrator token.

Audiobookshelf lets an administrator create an account and answers with that
account's own token, so the household needs exactly one administrator credential
(on the default account) to give everyone their own login. Nothing here knows a
member by name: Identity lists the members and Audiobookshelf lists the accounts,
both read at the time of the call.
"""
import json

import pytest

from services.gateway import main as gateway_main


class _Resp:
    def __init__(self, status=200, payload=None):
        self.status = status
        self._payload = payload if payload is not None else {}

    async def json(self):
        return self._payload


class _Answer:
    """What ``_execution_post_as`` hands back: a JSONResponse with a body."""

    def __init__(self, payload, status=200):
        self.status = status
        self.body = json.dumps(payload).encode()


class _Client:
    def __init__(self):
        self.posts = []
        self.gets = []

    async def get(self, url, **kwargs):
        self.gets.append({"url": url, "headers": kwargs.get("headers")})
        return _Resp(payload=[{"username": "sam"}, {"username": "newbie"}])

    async def post(self, url, **kwargs):
        self.posts.append({"url": url, "json": kwargs.get("json"), "headers": kwargs.get("headers")})
        return _Resp()


class _Ctx:
    def __init__(self, client):
        self._client = client

    async def __aenter__(self):
        return self._client

    async def __aexit__(self, *exc):
        return False


def _identity_posts(fake):
    """Identity writes only: the same client is also the logging transport."""
    return [post for post in fake.posts if "/api/users/" in post["url"]]


def _accounts(*entries):
    return {"users": list(entries)}


def test_provisioning_is_for_administrators_only(client):
    async def _not_admin(api_key):
        return {"user": "kid", "is_admin": False}

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(gateway_main, "_resolve_strict_identity", _not_admin)
        assert client.post("/api/media/audiobookshelf/users", json={"username": "a", "password": "b"}).status_code == 403
        assert client.post("/api/media/audiobookshelf/users/provision", json={"password": "b"}).status_code == 403


def test_a_password_is_required_before_anything_is_created(client):
    async def post_as(creds, endpoint, payload):  # pragma: no cover - must not run
        raise AssertionError("no account may be created without a password")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(gateway_main, "_execution_post_as", post_as)
        resp = client.post("/api/media/audiobookshelf/users/provision", json={})

    assert resp.status_code == 400
    assert "password" in resp.json()["message"].lower()


def test_one_account_is_created_and_its_token_filed(client):
    fake = _Client()
    seen = []

    async def post_as(creds, endpoint, payload):
        seen.append(payload)
        assert endpoint == "/execute/audiobookshelf"
        return _Answer({
            "status": "SUCCESS",
            "detail": {"user": {"id": "u9", "username": payload["username"], "type": "user", "has_token": True, "token": "new-token"}},
        })

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(gateway_main, "_execution_post_as", post_as)
        mp.setattr(gateway_main, "shared_http_client", lambda: _Ctx(fake))
        resp = client.post("/api/media/audiobookshelf/users", json={"username": "Robin", "password": "temp-pass"})

    assert resp.status_code == 200, resp.text
    assert seen[0] == {"action": "user_create", "username": "Robin", "password": "temp-pass"}
    # The browser is told the account exists; it is never handed the token.
    assert resp.json()["user"] == {"id": "u9", "username": "Robin", "type": "user", "has_token": True}
    assert "new-token" not in resp.text


def test_provisioning_creates_the_missing_and_files_everyone(client):
    fake = _Client()
    made = []

    async def exec_abs(creds, params, *, timeout=10.0):
        # sam already has an account; newbie does not.
        return _accounts({"id": "u1", "username": "sam", "type": "user", "has_token": True, "token": "sam-token"})

    async def post_as(creds, endpoint, payload):
        made.append(payload["username"])
        assert payload["action"] == "user_create"
        return _Answer({
            "status": "SUCCESS",
            "detail": {"user": {"id": f"id-{payload['username']}", "username": payload["username"], "type": "user", "has_token": True, "token": f"{payload['username']}-token"}},
        })

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(gateway_main, "_media_exec_audiobookshelf", exec_abs)
        mp.setattr(gateway_main, "_execution_post_as", post_as)
        mp.setattr(gateway_main, "shared_http_client", lambda: _Ctx(fake))
        resp = client.post("/api/media/audiobookshelf/users/provision", json={"password": "temp-pass"})

    assert resp.status_code == 200, resp.text
    data = resp.json()
    # Only the member without an account is created; the existing one is left alone.
    assert made == ["newbie"]
    assert data["created"] == ["newbie"]
    assert [entry["username"] for entry in data["filed"]] == ["newbie", "sam"]

    posts = _identity_posts(fake)
    filed = {post["url"].rsplit("/", 2)[-2]: post["json"]["token"] for post in posts}
    assert filed == {"newbie": "newbie-token", "sam": "sam-token"}
    assert all(post["headers"]["X-Internal-Secret"] == gateway_main.INTERNAL_SECRET for post in posts)


def test_an_account_that_cannot_be_created_is_reported_not_swallowed(client):
    fake = _Client()

    async def exec_abs(creds, params, *, timeout=10.0):
        return _accounts({"id": "u1", "username": "sam", "type": "user", "has_token": True, "token": "sam-token"})

    async def post_as(creds, endpoint, payload):
        return _Answer({"status": "SUCCESS", "detail": {"user": {}}})

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(gateway_main, "_media_exec_audiobookshelf", exec_abs)
        mp.setattr(gateway_main, "_execution_post_as", post_as)
        mp.setattr(gateway_main, "shared_http_client", lambda: _Ctx(fake))
        resp = client.post("/api/media/audiobookshelf/users/provision", json={"password": "temp-pass", "usernames": ["newbie"]})

    assert resp.status_code == 200, resp.text
    data = resp.json()
    # Nothing came back, so nothing is claimed: no account, no token, and the
    # member is named with the reason instead of being silently dropped.
    assert data["created"] == []
    assert data["filed"] == []
    assert data["skipped"] == [{"username": "newbie", "reason": "Audiobookshelf did not return the new account"}]


def test_provisioning_never_creates_or_overwrites_the_system_default_account(client):
    """The system default account holds the household's admin ABS token and must never be touched."""
    class _WithDefault(_Client):
        async def get(self, url, **kwargs):
            self.gets.append({"url": url, "headers": kwargs.get("headers")})
            return _Resp(payload=[
                {"username": "default", "is_system_default": True},
                {"username": "household", "is_system_default": True},
                {"username": "sam", "is_system_default": False},
            ])

    fake = _WithDefault()
    made = []

    async def exec_abs(creds, params, *, timeout=10.0):
        return _accounts({"id": "u1", "username": "sam", "type": "user", "has_token": True, "token": "sam-token"})

    async def post_as(creds, endpoint, payload):
        made.append(payload["username"])
        return _Answer({"status": "SUCCESS", "detail": {"user": {"id": "u2", "username": payload["username"], "type": "user", "has_token": True, "token": "t"}}})

    async def hub(method, path, *, json=None, **_kwargs):
        return 200, {"created": [], "updated": ["sam"], "skipped": []}

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(gateway_main, "_media_exec_audiobookshelf", exec_abs)
        mp.setattr(gateway_main, "_execution_post_as", post_as)
        mp.setattr(gateway_main, "shared_http_client", lambda: _Ctx(fake))
        mp.setattr("services.gateway.automation_routes._hub", hub)
        resp = client.post("/api/media/audiobookshelf/users/provision", json={"password": "temp-pass"})

    assert resp.status_code == 200, resp.text
    assert made == []
    assert [entry["username"] for entry in resp.json()["filed"]] == ["sam"]


def test_provisioning_leaves_music_assistant_to_homehub_and_reports_it(client):
    """A person's Music Assistant account is homehub's job, and it is told who to set up.

    Music Assistant sits behind homehub (its token is homehub's config entry), so the
    gateway asks homehub instead of talking to MA itself.
    """
    fake = _Client()
    seen = {}

    async def exec_abs(creds, params, *, timeout=10.0):
        return _accounts({"id": "u1", "username": "sam", "type": "user", "has_token": True, "token": "sam-token"})

    async def post_as(creds, endpoint, payload):
        return _Answer({
            "status": "SUCCESS",
            "detail": {"user": {"id": "id-newbie", "username": payload["username"], "type": "user", "has_token": True, "token": "newbie-token"}},
        })

    async def hub(method, path, *, json=None, **_kwargs):
        seen.update({"method": method, "path": path, "body": json})
        return 200, {"created": ["newbie"], "updated": ["sam"], "skipped": []}

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(gateway_main, "_media_exec_audiobookshelf", exec_abs)
        mp.setattr(gateway_main, "_execution_post_as", post_as)
        mp.setattr(gateway_main, "shared_http_client", lambda: _Ctx(fake))
        mp.setattr("services.gateway.automation_routes._hub", hub)
        resp = client.post("/api/media/audiobookshelf/users/provision", json={"password": "temp-pass"})

    assert resp.status_code == 200, resp.text
    assert seen["method"] == "POST"
    assert seen["path"] == "/hub/music-assistant/users/reconcile"
    assert seen["body"]["members"] == ["newbie", "sam"]
    assert seen["body"]["password"] == "temp-pass"
    assert resp.json()["music_assistant"] == {"created": ["newbie"], "updated": ["sam"], "skipped": []}


def test_a_homehub_that_is_down_does_not_lose_the_filed_tokens(client):
    fake = _Client()

    async def exec_abs(creds, params, *, timeout=10.0):
        return _accounts({"id": "u1", "username": "sam", "type": "user", "has_token": True, "token": "sam-token"})

    async def post_as(creds, endpoint, payload):
        return _Answer({
            "status": "SUCCESS",
            "detail": {"user": {"id": "id-newbie", "username": payload["username"], "type": "user", "has_token": True, "token": "newbie-token"}},
        })

    async def broken_hub(*_args, **_kwargs):
        raise RuntimeError("down")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(gateway_main, "_media_exec_audiobookshelf", exec_abs)
        mp.setattr(gateway_main, "_execution_post_as", post_as)
        mp.setattr(gateway_main, "shared_http_client", lambda: _Ctx(fake))
        mp.setattr("services.gateway.automation_routes._hub", broken_hub)
        resp = client.post("/api/media/audiobookshelf/users/provision", json={"password": "temp-pass"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["filed"]) == 2
    assert [entry["username"] for entry in body["music_assistant"]["skipped"]] == ["newbie", "sam"]
    assert "HomeHub unreachable" in body["music_assistant"]["skipped"][0]["reason"]
